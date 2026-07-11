import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, cast

from ia_application import (
    ChatMessageRepository,
    ChatSessionCommandRepository,
    UsageRecordRepository,
    UserProfileRepository,
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

type DynamoScalar = str | int | bool | Decimal | None
type DynamoValue = DynamoScalar | list["DynamoValue"] | dict[str, "DynamoValue"]
type DynamoItem = dict[str, DynamoValue]


class Boto3TableResource(Protocol):
    def get_item(self, **kwargs: object) -> dict[str, object]: ...

    def put_item(self, **kwargs: object) -> dict[str, object]: ...

    def delete_item(self, **kwargs: object) -> dict[str, object]: ...

    def query(self, **kwargs: object) -> dict[str, object]: ...

    def load(self) -> None: ...


class DynamoTable(Protocol):
    async def get_item(self, key: Mapping[str, DynamoValue]) -> DynamoItem | None: ...

    async def put_item(self, item: Mapping[str, DynamoValue]) -> None: ...

    async def delete_item(
        self, key: Mapping[str, DynamoValue]
    ) -> DynamoItem | None: ...

    async def query_items(
        self,
        *,
        key_name: str,
        key_value: str,
        index_name: str | None = None,
        scan_forward: bool = True,
    ) -> tuple[DynamoItem, ...]: ...

    async def ping(self) -> bool: ...


class Boto3DynamoTable:
    """Async facade over a boto3 DynamoDB Table resource."""

    def __init__(self, table: Boto3TableResource) -> None:
        self._table = table

    @classmethod
    def from_table_name(
        cls, table_name: str, *, region_name: str | None = None
    ) -> "Boto3DynamoTable":
        if not table_name:
            msg = "table_name must not be empty"
            raise ValueError(msg)
        import boto3  # type: ignore[import-untyped]

        resource = boto3.resource("dynamodb", region_name=region_name)
        return cls(cast(Boto3TableResource, resource.Table(table_name)))

    async def get_item(self, key: Mapping[str, DynamoValue]) -> DynamoItem | None:
        response = await asyncio.to_thread(
            self._table.get_item,
            Key=dict(key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return cast(DynamoItem | None, item)

    async def put_item(self, item: Mapping[str, DynamoValue]) -> None:
        await asyncio.to_thread(self._table.put_item, Item=dict(item))

    async def delete_item(self, key: Mapping[str, DynamoValue]) -> DynamoItem | None:
        response = await asyncio.to_thread(
            self._table.delete_item,
            Key=dict(key),
            ReturnValues="ALL_OLD",
        )
        return cast(DynamoItem | None, response.get("Attributes"))

    async def query_items(
        self,
        *,
        key_name: str,
        key_value: str,
        index_name: str | None = None,
        scan_forward: bool = True,
    ) -> tuple[DynamoItem, ...]:
        from boto3.dynamodb.conditions import Key  # type: ignore[import-untyped]

        kwargs: dict[str, object] = {
            "KeyConditionExpression": Key(key_name).eq(key_value),
            "ScanIndexForward": scan_forward,
        }
        if index_name is not None:
            kwargs["IndexName"] = index_name

        items: list[DynamoItem] = []
        while True:
            response = await asyncio.to_thread(self._table.query, **kwargs)
            page_items = response.get("Items", [])
            items.extend(cast(Sequence[DynamoItem], page_items))
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not isinstance(last_evaluated_key, dict) or not last_evaluated_key:
                break
            kwargs["ExclusiveStartKey"] = last_evaluated_key
        return tuple(items)

    async def ping(self) -> bool:
        try:
            await asyncio.to_thread(self._table.load)
        except Exception:
            return False
        return True


def _encode_value(value: object) -> DynamoValue:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _encode_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_encode_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    msg = f"unsupported DynamoDB value type: {type(value).__name__}"
    raise TypeError(msg)


def _encode_item(value: Mapping[str, object]) -> DynamoItem:
    return cast(DynamoItem, _encode_value(value))


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        msg = "stored timestamp must be an ISO-8601 string"
        raise ValueError(msg)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = "stored timestamp must include a timezone"
        raise ValueError(msg)
    return parsed


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"stored field must be a non-empty string: {field_name}"
        raise ValueError(msg)
    return value


def _int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        msg = f"stored field must be an integer: {field_name}"
        raise ValueError(msg)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    msg = f"stored field must be an integer: {field_name}"
    raise ValueError(msg)


def _composite_key(*parts: str) -> str:
    if not parts or any(not part for part in parts):
        msg = "composite key parts must not be empty"
        raise ValueError(msg)
    return "".join(f"{len(part)}:{part}" for part in parts)


def _chronological_key(timestamp: datetime, unique_id: str) -> str:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        msg = "timestamp must include a timezone"
        raise ValueError(msg)
    utc_timestamp = (
        timestamp.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    return f"{utc_timestamp}#{unique_id}"


class DynamoUserProfileRepository(UserProfileRepository):
    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    async def save(self, profile: UserProfile) -> None:
        await self._table.put_item(
            _encode_item(
                {
                    "tenantId": str(profile.tenant_id),
                    "userId": str(profile.user_id),
                    "email": profile.email,
                    "displayName": profile.display_name,
                    "roles": sorted(role.value for role in profile.roles),
                    "preferences": profile.preferences,
                    "createdAt": profile.created_at,
                    "updatedAt": profile.updated_at,
                }
            )
        )

    async def get(self, tenant_id: TenantId, user_id: UserId) -> UserProfile | None:
        item = await self._table.get_item(
            {"tenantId": str(tenant_id), "userId": str(user_id)}
        )
        if item is None:
            return None
        profile = UserProfile(
            tenant_id=TenantId(_string(item.get("tenantId"), "tenantId")),
            user_id=UserId(_string(item.get("userId"), "userId")),
            email=_string(item.get("email"), "email"),
            display_name=_string(item.get("displayName"), "displayName"),
            roles=frozenset(
                Role(value) for value in cast(list[str], item.get("roles", []))
            ),
            preferences=cast(dict[str, str], item.get("preferences", {})),
            created_at=_datetime(item.get("createdAt")),
            updated_at=_datetime(item.get("updatedAt")),
        )
        if profile.tenant_id != tenant_id or profile.user_id != user_id:
            return None
        return profile


class DynamoChatSessionRepository(ChatSessionCommandRepository):
    def __init__(self, table: DynamoTable, *, user_index_name: str) -> None:
        if not user_index_name:
            msg = "user_index_name must not be empty"
            raise ValueError(msg)
        self._table = table
        self._user_index_name = user_index_name

    async def save(self, session: ChatSession) -> None:
        await self._table.put_item(
            _encode_item(
                {
                    "tenantId": str(session.tenant_id),
                    "sessionId": str(session.session_id),
                    "tenantUserKey": _composite_key(
                        str(session.tenant_id), str(session.user_id)
                    ),
                    "userId": str(session.user_id),
                    "status": session.status,
                    "title": session.title,
                    "createdAt": session.created_at,
                    "lastActivity": session.last_activity,
                    "messageCount": session.message_count,
                    "ttl": session.ttl_epoch_seconds,
                }
            )
        )

    async def get(
        self, tenant_id: TenantId, session_id: SessionId
    ) -> ChatSession | None:
        item = await self._table.get_item(
            {"tenantId": str(tenant_id), "sessionId": str(session_id)}
        )
        if item is None:
            return None
        session = self._decode(item)
        if session.tenant_id != tenant_id or session.session_id != session_id:
            return None
        return session

    async def list_for_user(
        self, tenant_id: TenantId, user_id: UserId
    ) -> tuple[ChatSession, ...]:
        items = await self._table.query_items(
            key_name="tenantUserKey",
            key_value=_composite_key(str(tenant_id), str(user_id)),
            index_name=self._user_index_name,
            scan_forward=False,
        )
        sessions = tuple(self._decode(item) for item in items)
        return tuple(
            session
            for session in sessions
            if session.tenant_id == tenant_id and session.user_id == user_id
        )

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> bool:
        deleted = await self._table.delete_item(
            {"tenantId": str(tenant_id), "sessionId": str(session_id)}
        )
        if deleted is None:
            return False
        deleted_tenant = deleted.get("tenantId")
        deleted_session = deleted.get("sessionId")
        return deleted_tenant == str(tenant_id) and deleted_session == str(session_id)

    @staticmethod
    def _decode(item: Mapping[str, DynamoValue]) -> ChatSession:
        ttl = item.get("ttl")
        return ChatSession(
            tenant_id=TenantId(_string(item.get("tenantId"), "tenantId")),
            session_id=SessionId(_string(item.get("sessionId"), "sessionId")),
            user_id=UserId(_string(item.get("userId"), "userId")),
            status=_string(item.get("status"), "status"),
            title=_string(item.get("title"), "title"),
            created_at=_datetime(item.get("createdAt")),
            last_activity=_datetime(item.get("lastActivity")),
            message_count=_int(item.get("messageCount"), "messageCount"),
            ttl_epoch_seconds=None if ttl is None else _int(ttl, "ttl"),
        )


class DynamoChatMessageRepository(ChatMessageRepository):
    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    async def append(self, message: ChatMessage) -> None:
        citations = [
            {
                "documentId": str(citation.document_id),
                "title": citation.title,
                "section": citation.section,
                "sourceUri": citation.source_uri,
                "chunkId": str(citation.chunk_id),
                "score": citation.score,
            }
            for citation in message.citations
        ]
        await self._table.put_item(
            _encode_item(
                {
                    "tenantSessionKey": _composite_key(
                        str(message.tenant_id), str(message.session_id)
                    ),
                    "createdAtMessageKey": _chronological_key(
                        message.created_at, str(message.message_id)
                    ),
                    "messageId": str(message.message_id),
                    "tenantId": str(message.tenant_id),
                    "sessionId": str(message.session_id),
                    "userId": str(message.user_id),
                    "role": message.role.value,
                    "content": message.content,
                    "citations": citations,
                    "modelId": message.model_id,
                    "inputTokens": message.input_tokens,
                    "outputTokens": message.output_tokens,
                    "latencyMs": message.latency_ms,
                    "estimatedCostUsd": message.estimated_cost_usd,
                    "createdAt": message.created_at,
                    "ttl": message.ttl_epoch_seconds,
                }
            )
        )

    async def list_for_session(
        self, tenant_id: TenantId, session_id: SessionId
    ) -> tuple[ChatMessage, ...]:
        items = await self._table.query_items(
            key_name="tenantSessionKey",
            key_value=_composite_key(str(tenant_id), str(session_id)),
        )
        messages = tuple(self._decode(item) for item in items)
        return tuple(
            message
            for message in messages
            if message.tenant_id == tenant_id and message.session_id == session_id
        )

    @staticmethod
    def _decode(item: Mapping[str, DynamoValue]) -> ChatMessage:
        raw_citations = cast(list[dict[str, DynamoValue]], item.get("citations", []))
        citations = tuple(
            Citation(
                document_id=DocumentId(_string(raw.get("documentId"), "documentId")),
                title=_string(raw.get("title"), "title"),
                section=_string(raw.get("section"), "section"),
                source_uri=_string(raw.get("sourceUri"), "sourceUri"),
                chunk_id=ChunkId(_string(raw.get("chunkId"), "chunkId")),
                score=float(cast(Decimal, raw.get("score"))),
            )
            for raw in raw_citations
        )
        ttl = item.get("ttl")
        return ChatMessage(
            tenant_id=TenantId(_string(item.get("tenantId"), "tenantId")),
            session_id=SessionId(_string(item.get("sessionId"), "sessionId")),
            message_id=MessageId(_string(item.get("messageId"), "messageId")),
            user_id=UserId(_string(item.get("userId"), "userId")),
            role=MessageRole(_string(item.get("role"), "role")),
            content=_string(item.get("content"), "content"),
            citations=citations,
            model_id=cast(str | None, item.get("modelId")),
            input_tokens=_int(item.get("inputTokens"), "inputTokens"),
            output_tokens=_int(item.get("outputTokens"), "outputTokens"),
            latency_ms=_int(item.get("latencyMs"), "latencyMs"),
            estimated_cost_usd=cast(Decimal, item.get("estimatedCostUsd")),
            created_at=_datetime(item.get("createdAt")),
            ttl_epoch_seconds=None if ttl is None else _int(ttl, "ttl"),
        )


class DynamoUsageRecordRepository(UsageRecordRepository):
    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    async def save(self, record: UsageRecord) -> None:
        await self._table.put_item(
            _encode_item(
                {
                    "tenantUserKey": _composite_key(
                        str(record.tenant_id), str(record.user_id)
                    ),
                    "timestampRequestKey": _chronological_key(
                        record.timestamp, str(record.request_id)
                    ),
                    "tenantId": str(record.tenant_id),
                    "userId": str(record.user_id),
                    "requestId": str(record.request_id),
                    "modelId": record.model_id,
                    "agentId": str(record.agent_id),
                    "inputTokens": record.input_tokens,
                    "outputTokens": record.output_tokens,
                    "vectorQueries": record.vector_queries,
                    "latencyMs": record.latency_ms,
                    "estimatedCostUsd": record.estimated_cost_usd,
                    "timestamp": record.timestamp,
                    "status": record.status,
                }
            )
        )

    async def list_for_user(
        self, tenant_id: TenantId, user_id: UserId
    ) -> tuple[UsageRecord, ...]:
        items = await self._table.query_items(
            key_name="tenantUserKey",
            key_value=_composite_key(str(tenant_id), str(user_id)),
            scan_forward=False,
        )
        records = tuple(self._decode(item) for item in items)
        return tuple(
            record
            for record in records
            if record.tenant_id == tenant_id and record.user_id == user_id
        )

    @staticmethod
    def _decode(item: Mapping[str, DynamoValue]) -> UsageRecord:
        return UsageRecord(
            tenant_id=TenantId(_string(item.get("tenantId"), "tenantId")),
            user_id=UserId(_string(item.get("userId"), "userId")),
            request_id=RequestId(_string(item.get("requestId"), "requestId")),
            model_id=_string(item.get("modelId"), "modelId"),
            agent_id=AgentId(_string(item.get("agentId"), "agentId")),
            input_tokens=_int(item.get("inputTokens"), "inputTokens"),
            output_tokens=_int(item.get("outputTokens"), "outputTokens"),
            vector_queries=_int(item.get("vectorQueries"), "vectorQueries"),
            latency_ms=_int(item.get("latencyMs"), "latencyMs"),
            estimated_cost_usd=cast(Decimal, item.get("estimatedCostUsd")),
            timestamp=_datetime(item.get("timestamp")),
            status=_string(item.get("status"), "status"),
        )
