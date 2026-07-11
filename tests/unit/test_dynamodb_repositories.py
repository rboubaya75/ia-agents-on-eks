from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
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


def _as_boto3_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, list):
        return [_as_boto3_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_boto3_value(item) for key, item in value.items()}
    return value


class InMemoryDynamoTable:
    def __init__(self, key_fields: tuple[str, ...]) -> None:
        self._key_fields = key_fields
        self.items: dict[tuple[object, ...], dict[str, object]] = {}

    def _key(self, source: Mapping[str, object]) -> tuple[object, ...]:
        return tuple(source[field] for field in self._key_fields)

    @staticmethod
    def _read(item: Mapping[str, object]) -> dict[str, object]:
        return cast(dict[str, object], _as_boto3_value(dict(item)))

    async def get_item(self, key: Mapping[str, object]) -> dict[str, object] | None:
        item = self.items.get(self._key(key))
        return None if item is None else self._read(item)

    async def put_item(self, item: Mapping[str, object]) -> None:
        self.items[self._key(item)] = dict(item)

    async def delete_item(self, key: Mapping[str, object]) -> dict[str, object] | None:
        item = self.items.pop(self._key(key), None)
        return None if item is None else self._read(item)

    async def query_items(
        self,
        *,
        key_name: str,
        key_value: str,
        index_name: str | None = None,
        scan_forward: bool = True,
    ) -> tuple[dict[str, object], ...]:
        del index_name
        matching = [
            item for item in self.items.values() if item.get(key_name) == key_value
        ]
        if len(self._key_fields) > 1:
            sort_field = self._key_fields[1]
            matching.sort(
                key=lambda item: str(item[sort_field]), reverse=not scan_forward
            )
        return tuple(self._read(item) for item in matching)

    async def ping(self) -> bool:
        return True


def _session(
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "user-a",
    session_id: str = "session-a",
) -> ChatSession:
    return ChatSession(
        tenant_id=TenantId(tenant_id),
        session_id=SessionId(session_id),
        user_id=UserId(user_id),
        status="active",
        title="Session",
        created_at=NOW,
        last_activity=NOW,
        message_count=2,
        ttl_epoch_seconds=2_000_000_000,
    )


def _message(*, message_id: str, created_at: datetime) -> ChatMessage:
    return ChatMessage(
        tenant_id=TenantId("tenant-a"),
        session_id=SessionId("session-a"),
        message_id=MessageId(message_id),
        user_id=UserId("user-a"),
        role=MessageRole.ASSISTANT,
        content=f"Answer {message_id}",
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
        created_at=created_at,
    )


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
async def test_chat_session_round_trip_accepts_boto3_decimal_numbers() -> None:
    table = InMemoryDynamoTable(("tenantId", "sessionId"))
    repository = DynamoChatSessionRepository(
        cast(DynamoTable, table), user_index_name="tenant-user-index"
    )
    session = _session()

    await repository.save(session)

    assert await repository.get(TenantId("tenant-a"), SessionId("session-a")) == session
    assert await repository.list_for_user(TenantId("tenant-a"), UserId("user-a")) == (
        session,
    )
    assert (
        await repository.delete(TenantId("tenant-b"), SessionId("session-a")) is False
    )
    assert await repository.delete(TenantId("tenant-a"), SessionId("session-a")) is True


@pytest.mark.asyncio
async def test_composite_keys_are_unambiguous_and_results_are_revalidated() -> None:
    table = InMemoryDynamoTable(("tenantId", "sessionId"))
    repository = DynamoChatSessionRepository(
        cast(DynamoTable, table), user_index_name="tenant-user-index"
    )
    first = _session(tenant_id="a#b", user_id="c", session_id="first")
    second = _session(tenant_id="a", user_id="b#c", session_id="second")

    await repository.save(first)
    await repository.save(second)

    stored_keys = {str(item["tenantUserKey"]) for item in table.items.values()}
    assert len(stored_keys) == 2
    assert await repository.list_for_user(TenantId("a#b"), UserId("c")) == (first,)
    assert await repository.list_for_user(TenantId("a"), UserId("b#c")) == (second,)

    first_item = next(
        item for item in table.items.values() if item["sessionId"] == "first"
    )
    first_item["tenantId"] = "other-tenant"
    assert await repository.list_for_user(TenantId("a#b"), UserId("c")) == ()


@pytest.mark.asyncio
async def test_fractional_decimal_is_rejected_for_integer_field() -> None:
    table = InMemoryDynamoTable(("tenantId", "sessionId"))
    repository = DynamoChatSessionRepository(
        cast(DynamoTable, table), user_index_name="tenant-user-index"
    )
    await repository.save(_session())
    stored = table.items[("tenant-a", "session-a")]
    stored["messageCount"] = Decimal("1.5")

    with pytest.raises(ValueError, match="messageCount"):
        await repository.get(TenantId("tenant-a"), SessionId("session-a"))


@pytest.mark.asyncio
async def test_chat_messages_are_returned_in_chronological_order() -> None:
    table = InMemoryDynamoTable(("tenantSessionKey", "createdAtMessageKey"))
    repository = DynamoChatMessageRepository(cast(DynamoTable, table))
    later = _message(message_id="message-z", created_at=NOW + timedelta(minutes=1))
    earlier = _message(message_id="message-a", created_at=NOW)

    await repository.append(later)
    await repository.append(earlier)
    result = await repository.list_for_session(
        TenantId("tenant-a"), SessionId("session-a")
    )

    assert result == (earlier, later)
    assert (
        await repository.list_for_session(TenantId("tenant-b"), SessionId("session-a"))
        == ()
    )


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

    assert await repository.list_for_user(TenantId("tenant-a"), UserId("user-a")) == (
        record,
    )
    assert await repository.list_for_user(TenantId("tenant-b"), UserId("user-a")) == ()
