from datetime import UTC, datetime

from fastapi.testclient import TestClient
from ia_backend_api import AppContainer, StaticReadinessProbe, create_app
from ia_domain import ChatSession, Classification, Role, SessionId, TenantId, UserId
from ia_security import ExpiredTokenError, Principal

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


class StaticTokenVerifier:
    def __init__(self, principal: Principal | None = None, error: Exception | None = None) -> None:
        self._principal = principal
        self._error = error

    async def verify(self, access_token: str) -> Principal:
        del access_token
        if self._error is not None:
            raise self._error
        if self._principal is None:
            raise RuntimeError("no principal configured")
        return self._principal


class InMemoryChatSessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[tuple[TenantId, SessionId], ChatSession] = {}

    async def save(self, session: ChatSession) -> None:
        self._sessions[(session.tenant_id, session.session_id)] = session

    async def get(self, tenant_id: TenantId, session_id: SessionId) -> ChatSession | None:
        return self._sessions.get((tenant_id, session_id))

    async def list_for_user(self, tenant_id: TenantId, user_id: UserId) -> tuple[ChatSession, ...]:
        return tuple(
            session
            for session in self._sessions.values()
            if session.tenant_id == tenant_id and session.user_id == user_id
        )

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> bool:
        return self._sessions.pop((tenant_id, session_id), None) is not None


def _principal(
    *,
    tenant: str = "tenant-a",
    user: str = "user-a",
    scopes: frozenset[str] | None = None,
    roles: frozenset[Role] = frozenset({Role.USER}),
) -> Principal:
    return Principal(
        user_id=UserId(user),
        tenant_id=TenantId(tenant),
        email=f"{user}@example.com",
        roles=roles,
        scopes=scopes
        or frozenset(
            {
                "platform/profile.read",
                "platform/chat.read",
                "platform/chat.write",
            }
        ),
        maximum_classification=Classification.INTERNAL,
        token_id=None,
    )


def _client(
    *,
    principal: Principal | None = None,
    error: Exception | None = None,
    ready: bool = True,
    repository: InMemoryChatSessionRepository | None = None,
) -> tuple[TestClient, InMemoryChatSessionRepository]:
    repo = repository or InMemoryChatSessionRepository()
    app = create_app(
        AppContainer(
            token_verifier=StaticTokenVerifier(principal or _principal(), error),
            chat_sessions=repo,
            readiness=StaticReadinessProbe(ready),
            now=lambda: NOW,
            new_id=lambda: "session-new",
        )
    )
    return TestClient(app, raise_server_exceptions=False), repo


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer access-token", "X-Request-ID": "request-123"}


def test_health_endpoints_and_correlation_headers() -> None:
    client, _ = _client()

    live = client.get("/api/v1/health/live")
    ready = client.get("/api/v1/health/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "live"
    assert live.headers["x-request-id"]
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_readiness_failure_is_normalized_without_stack_trace() -> None:
    client, _ = _client(ready=False)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"
    assert "Traceback" not in response.text


def test_me_uses_verified_claims() -> None:
    client, _ = _client()

    response = client.get("/api/v1/me", headers=_headers())

    assert response.status_code == 200
    assert response.json()["tenantId"] == "tenant-a"
    assert response.json()["userId"] == "user-a"
    assert response.json()["requestId"] == "request-123"


def test_missing_or_expired_token_is_rejected() -> None:
    client, _ = _client()
    missing = client.get("/api/v1/me")
    expired_client, _ = _client(error=ExpiredTokenError("expired"))
    expired = expired_client.get("/api/v1/me", headers=_headers())

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "authentication_required"
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "token_expired"


def test_scope_is_enforced_per_endpoint() -> None:
    client, _ = _client(principal=_principal(scopes=frozenset({"platform/profile.read"})))

    response = client.get("/api/v1/chat/sessions", headers=_headers())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_session_lifecycle_never_accepts_tenant_from_body() -> None:
    client, _repo = _client()

    invalid = client.post(
        "/api/v1/chat/sessions",
        headers=_headers(),
        json={"title": "Hello", "tenantId": "tenant-b"},
    )
    created = client.post(
        "/api/v1/chat/sessions",
        headers=_headers(),
        json={"title": "Hello"},
    )
    listed = client.get("/api/v1/chat/sessions", headers=_headers())
    fetched = client.get("/api/v1/chat/sessions/session-new", headers=_headers())
    deleted = client.delete("/api/v1/chat/sessions/session-new", headers=_headers())

    assert invalid.status_code == 422
    assert created.status_code == 201
    assert created.json()["session"]["sessionId"] == "session-new"
    assert listed.json()["sessions"][0]["title"] == "Hello"
    assert fetched.status_code == 200
    assert deleted.json()["deleted"] is True


def test_cross_tenant_session_is_hidden() -> None:
    repo = InMemoryChatSessionRepository()
    other = ChatSession(
        tenant_id=TenantId("tenant-b"),
        session_id=SessionId("shared-id"),
        user_id=UserId("user-b"),
        status="active",
        title="Secret",
        created_at=NOW,
        last_activity=NOW,
    )
    import asyncio

    asyncio.run(repo.save(other))
    client, _ = _client(repository=repo)

    response = client.get("/api/v1/chat/sessions/shared-id", headers=_headers())

    assert response.status_code == 404
    assert "Secret" not in response.text


def test_openapi_create_session_schema_has_no_tenant_id() -> None:
    client, _ = _client()

    schema = client.get("/api/openapi.json").json()
    properties = schema["components"]["schemas"]["CreateSessionRequest"]["properties"]

    assert "tenantId" not in properties
