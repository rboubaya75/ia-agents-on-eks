from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ia_domain.types import (
    AgentId,
    ChunkId,
    DocumentId,
    JobId,
    MessageId,
    RequestId,
    SessionId,
    TenantId,
    UserId,
)

TenantIdField = Annotated[TenantId, Field(min_length=1, max_length=128)]
UserIdField = Annotated[UserId, Field(min_length=1, max_length=128)]
SessionIdField = Annotated[SessionId, Field(min_length=1, max_length=128)]
MessageIdField = Annotated[MessageId, Field(min_length=1, max_length=128)]
DocumentIdField = Annotated[DocumentId, Field(min_length=1, max_length=128)]
ChunkIdField = Annotated[ChunkId, Field(min_length=1, max_length=128)]
JobIdField = Annotated[JobId, Field(min_length=1, max_length=128)]
RequestIdField = Annotated[RequestId, Field(min_length=1, max_length=128)]
AgentIdField = Annotated[AgentId, Field(min_length=1, max_length=128)]
NonEmptyText = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Role(StrEnum):
    USER = "user"
    SUPPORT = "support"
    TENANT_ADMIN = "tenant-admin"
    PLATFORM_ADMIN = "platform-admin"


class Classification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class IngestionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "timestamp must include a timezone"
        raise ValueError(msg)
    return value


class UserProfile(StrictModel):
    user_id: UserIdField
    tenant_id: TenantIdField
    email: Annotated[str, Field(min_length=3, max_length=320)]
    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    roles: Annotated[frozenset[Role], Field(min_length=1)]
    preferences: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    _validate_created_at = field_validator("created_at")(_require_aware)
    _validate_updated_at = field_validator("updated_at")(_require_aware)


class ChatSession(StrictModel):
    tenant_id: TenantIdField
    session_id: SessionIdField
    user_id: UserIdField
    status: Annotated[str, Field(min_length=1, max_length=32)]
    title: Annotated[str, Field(min_length=1, max_length=300)]
    created_at: datetime
    last_activity: datetime
    message_count: Annotated[int, Field(ge=0)] = 0
    ttl_epoch_seconds: Annotated[int, Field(gt=0)] | None = None

    _validate_created_at = field_validator("created_at")(_require_aware)
    _validate_last_activity = field_validator("last_activity")(_require_aware)


class Citation(StrictModel):
    document_id: DocumentIdField
    title: Annotated[str, Field(min_length=1, max_length=500)]
    section: Annotated[str, Field(min_length=1, max_length=500)]
    source_uri: Annotated[str, Field(min_length=1, max_length=2048)]
    chunk_id: ChunkIdField
    score: Annotated[float, Field(ge=0.0, le=1.0)]


class ChatMessage(StrictModel):
    tenant_id: TenantIdField
    session_id: SessionIdField
    message_id: MessageIdField
    user_id: UserIdField
    role: MessageRole
    content: Annotated[str, Field(min_length=1, max_length=100_000)]
    citations: tuple[Citation, ...] = ()
    model_id: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0
    latency_ms: Annotated[int, Field(ge=0)] = 0
    estimated_cost_usd: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    created_at: datetime
    ttl_epoch_seconds: Annotated[int, Field(gt=0)] | None = None

    _validate_created_at = field_validator("created_at")(_require_aware)


class UsageRecord(StrictModel):
    tenant_id: TenantIdField
    user_id: UserIdField
    request_id: RequestIdField
    model_id: Annotated[str, Field(min_length=1, max_length=300)]
    agent_id: AgentIdField
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    vector_queries: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[int, Field(ge=0)]
    estimated_cost_usd: Annotated[Decimal, Field(ge=0)]
    timestamp: datetime
    status: Annotated[str, Field(min_length=1, max_length=32)]

    _validate_timestamp = field_validator("timestamp")(_require_aware)


class IngestionJob(StrictModel):
    tenant_id: TenantIdField
    job_id: JobIdField
    document_id: DocumentIdField
    source_version: Annotated[str, Field(min_length=1, max_length=128)]
    status: IngestionStatus
    chunks_created: Annotated[int, Field(ge=0)] = 0
    vectors_created: Annotated[int, Field(ge=0)] = 0
    error_code: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    started_at: datetime
    completed_at: datetime | None = None

    _validate_started_at = field_validator("started_at")(_require_aware)

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware(value)


class DocumentChunk(StrictModel):
    tenant_id: TenantIdField
    document_id: DocumentIdField
    chunk_id: ChunkIdField
    source_version: Annotated[str, Field(min_length=1, max_length=128)]
    source_uri: Annotated[str, Field(min_length=1, max_length=2048)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    section: Annotated[str, Field(min_length=1, max_length=500)]
    language: Annotated[str, Field(min_length=2, max_length=35)]
    classification: Classification
    allowed_roles: Annotated[frozenset[Role], Field(min_length=1)]
    checksum: Annotated[str, Field(min_length=32, max_length=128)]
    content: Annotated[str, Field(min_length=1, max_length=200_000)]
    created_at: datetime

    _validate_created_at = field_validator("created_at")(_require_aware)
