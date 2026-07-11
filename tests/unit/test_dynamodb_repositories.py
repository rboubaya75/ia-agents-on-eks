from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from ia_aws_clients import (
    DynamoChatMessageRepository,
    DynamoChatSessionRepository,
    DynamoTable,
    DynamoUsageRecordRepository,
    DynamoUserProfileRepository,
)
from ia_domain import (
    AgentId,
    ChatMessage,
    ChatSession,
    ChunkId,
    Citation,
    DocumentId,
    MessageId,
    MessageRole,
    RequestId,
    Role,
    SessionId,
    TenantId,
    UsageRecord,
    UserId,
    UserProfile,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


class InMemoryDynamoTable:
    def __init__(self, key_fields: tuple[str, ...]) -> None:
        self._key_fields = key_fields
        self.items: dict[tuple[object, ...], dict[str, object]] = {}

    def _key(self, source: Mapping[str, object]) -> tuple[object, ...]:
        return tuple(source[field] for field in self._key_fields)

    async def get_item(self, key: Mapping[str, object]) -> dict[str, object] | None:
        item = self.items.get(self._key(key))
        return None if item is None else item.copy()

    async def put_item(self, item: Mapping[str, object]) -> None:
        self.items[self._key(item)] = dict(item)

    async def delete_item(self, key: Mapping[str, object]) -> dict[str, object] | None:
        return self.items.pop(self._key(key), None)

    async def query_items(
        self,
        *,
        key_name: str,
        key_value: str,
        index_name: str | None = None,
        scan_forward: bool = True,
    ) -> tuple[dict[str, object], ...]:
        del index_name
        matching = [item.copy() for item in self.items.values() if item.get(key_name) == key_value]
        return tuple(matching if scan_forward else reversed(matching))

    async def ping(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_user_profile_round_trip_is_tenant_scoped() -> None:
    table = InMemoryDynamoTable(("tenantId", "userId"))
    repository = DynamoUserProfileRepository(cast(DynamoTable, table))
    profile = UserProfile(
        tenant_id=TenantId("tenant-a"),
        user_id=UserId("user-a"),
        email="user@example.com",
        display_name="User A",
        roles=frozenset({Role.USER}),
        preferences={"language": "fr"},
        created_at=NOW,
        updated_at=NOW,
    )

    await repository.save(profile)

    assert await repository.get(TenantId("tenant-a"), UserId("user-a")) == profile
    assert await repository.get(TenantId("tenant-b"), UserId("user-a")) is None


@pytest.mark.asyncio
async def test_chat_session_round_trip_list_and_delete() -> None:
    table = InMemoryDynamoTable(("tenantId", "sessionId"))
    repository = DynamoChatSessionRepository(cast(DynamoTable, table))
    session = ChatSession(
        tenant_id=TenantId("tenant-a"),
        session_id=SessionId("session-a"),
        user_id=UserId("user-a"),
        status="active",
        title="Session",
        created_at=NOW,
        last_activity=NOW,
        message_count=2,
        ttl_epoch_seconds=2_000_000_000,
    )

    await repository.save(session)

    assert await repository.get(TenantId("tenant-a"), SessionId("session-a")) == session
    assert await repository.list_for_user(TenantId("tenant-a"), UserId("user-a")) == (session,)
    assert await repository.delete(TenantId("tenant-b"), SessionId("session-a")) is False
    assert await repository.delete(TenantId("tenant-a"), SessionId("session-a")) is True


@pytest.mark.asyncio
async def test_chat_message_round_trip_preserves_citations_and_decimal_cost() -> None:
    table = InMemoryDynamoTable(("tenantSessionKey", "messageId"))
    repository = DynamoChatMessageRepository(cast(DynamoTable, table))
    message = ChatMessage(
        tenant_id=TenantId("tenant-a"),
        session_id=SessionId("session-a"),
        message_id=MessageId("message-a"),
        user_id=UserId("user-a"),
        role=MessageRole.ASSISTANT,
        content="Grounded answer",
        citations=(
            Citation(
                document_id=DocumentId("document-a"),
                title="Document",
                section="Section",
                source_uri="s3://bucket/key",
                chunk_id=ChunkId("chunk-a"),
                score=0.9,
            ),
        ),
        model_id="model-a",
        input_tokens=10,
        output_tokens=5,
        latency_ms=100,
        estimated_cost_usd=Decimal("0.01"),
        created_at=NOW,
    )

    await repository.append(message)
    result = await repository.list_for_session(TenantId("tenant-a"), SessionId("session-a"))

    assert result == (message,)
    assert await repository.list_for_session(TenantId("tenant-b"), SessionId("session-a")) == ()


@pytest.mark.asyncio
async def test_usage_record_round_trip_is_tenant_scoped() -> None:
    table = InMemoryDynamoTable(("tenantUserKey", "timestampRequestKey"))
    repository = DynamoUsageRecordRepository(cast(DynamoTable, table))
    record = UsageRecord(
        tenant_id=TenantId("tenant-a"),
        user_id=UserId("user-a"),
        request_id=RequestId("request-a"),
        model_id="model-a",
        agent_id=AgentId("agent-a"),
        input_tokens=10,
        output_tokens=5,
        vector_queries=1,
        latency_ms=100,
        estimated_cost_usd=Decimal("0.01"),
        timestamp=NOW,
        status="succeeded",
    )

    await repository.save(record)

    assert await repository.list_for_user(TenantId("tenant-a"), UserId("user-a")) == (record,)
    assert await repository.list_for_user(TenantId("tenant-b"), UserId("user-a")) == ()
