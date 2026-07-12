from collections.abc import Sequence

from ia_agent_contracts import AgentRequest, AgentResponse
from ia_application import ModelRequest, ModelResponse
from ia_domain import (
    ChatMessage,
    ChatSession,
    SessionId,
    TenantId,
    UsageRecord,
    UserId,
    UserProfile,
)


class FakeModelProvider:
    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def converse(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            msg = "no fake model response configured"
            raise RuntimeError(msg)
        return self._responses.pop(0)


class InMemoryUserProfileRepository:
    def __init__(self) -> None:
        self._profiles: dict[tuple[TenantId, UserId], UserProfile] = {}

    async def save(self, profile: UserProfile) -> None:
        self._profiles[(profile.tenant_id, profile.user_id)] = profile

    async def get(self, tenant_id: TenantId, user_id: UserId) -> UserProfile | None:
        return self._profiles.get((tenant_id, user_id))


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


class InMemoryChatMessageRepository:
    def __init__(self) -> None:
        self._messages: list[ChatMessage] = []

    async def append(self, message: ChatMessage) -> None:
        self._messages.append(message)

    async def list_for_session(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
    ) -> tuple[ChatMessage, ...]:
        return tuple(
            message
            for message in self._messages
            if message.tenant_id == tenant_id and message.session_id == session_id
        )


class InMemoryUsageRecordRepository:
    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    async def save(self, record: UsageRecord) -> None:
        self._records.append(record)

    async def list_for_user(self, tenant_id: TenantId, user_id: UserId) -> tuple[UsageRecord, ...]:
        return tuple(
            record
            for record in self._records
            if record.tenant_id == tenant_id and record.user_id == user_id
        )


class FakeAgentRuntimeClient:
    def __init__(self, responses: Sequence[AgentResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, AgentRequest]] = []

    async def invoke(self, agent_id: str, request: AgentRequest) -> AgentResponse:
        self.requests.append((agent_id, request))
        if not self._responses:
            msg = "no fake agent response configured"
            raise RuntimeError(msg)
        return self._responses.pop(0)


class FakeSecretsProvider:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets.copy()

    async def get_secret(self, secret_name: str) -> str:
        try:
            return self._secrets[secret_name]
        except KeyError as error:
            msg = f"secret not configured: {secret_name}"
            raise KeyError(msg) from error
