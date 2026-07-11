from datetime import UTC, datetime

from ia_domain import ChatSession, SessionId, TenantId, UserId
from test_support import InMemoryChatSessionRepository


async def test_chat_session_repository_does_not_cross_tenant_boundary() -> None:
    repository = InMemoryChatSessionRepository()
    session = ChatSession(
        tenant_id=TenantId("tenant-a"),
        session_id=SessionId("session-1"),
        user_id=UserId("user-1"),
        status="active",
        title="Session",
        created_at=datetime.now(UTC),
        last_activity=datetime.now(UTC),
    )
    await repository.save(session)

    assert await repository.get(TenantId("tenant-a"), SessionId("session-1")) == session
    assert await repository.get(TenantId("tenant-b"), SessionId("session-1")) is None
