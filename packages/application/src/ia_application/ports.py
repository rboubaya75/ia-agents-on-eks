from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Protocol, runtime_checkable

from ia_agent_contracts import AgentRequest, AgentResponse
from ia_domain import (
    ChatMessage,
    ChatSession,
    ChunkId,
    Classification,
    Document,
    DocumentChunk,
    DocumentId,
    IngestionJob,
    JobId,
    Role,
    SessionId,
    TenantId,
    UsageRecord,
    UserId,
    UserProfile,
)
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ModelRequest(StrictModel):
    model_alias: Annotated[str, Field(min_length=1, max_length=128)]
    system_prompt: Annotated[str, Field(min_length=1, max_length=100_000)]
    messages: Annotated[tuple[dict[str, Any], ...], Field(min_length=1)]
    max_output_tokens: Annotated[int, Field(ge=1, le=32_000)]
    temperature: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0


class ModelResponse(StrictModel):
    model_id: Annotated[str, Field(min_length=1, max_length=300)]
    content: Annotated[str, Field(min_length=1, max_length=200_000)]
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[int, Field(ge=0)]
    estimated_cost_usd: Annotated[Decimal, Field(ge=0)]


class EmbeddingRequest(StrictModel):
    model_alias: Annotated[str, Field(min_length=1, max_length=128)]
    texts: Annotated[tuple[str, ...], Field(min_length=1, max_length=256)]


class EmbeddingResponse(StrictModel):
    model_id: Annotated[str, Field(min_length=1, max_length=300)]
    dimensions: Annotated[int, Field(ge=1, le=4096)]
    vectors: Annotated[tuple[tuple[float, ...], ...], Field(min_length=1)]
    input_tokens: Annotated[int, Field(ge=0)] = 0


class ExtractedSection(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    content: Annotated[str, Field(min_length=1, max_length=2_000_000)]
    page_number: Annotated[int, Field(gt=0)] | None = None


class ExtractedDocument(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    source_version: Annotated[str, Field(min_length=1, max_length=128)]
    sections: Annotated[tuple[ExtractedSection, ...], Field(min_length=1)]


class IngestionJobClaim(StrictModel):
    job: IngestionJob
    acquired: bool


class VectorRecord(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    chunk_id: Annotated[ChunkId, Field(min_length=1, max_length=128)]
    classification: Classification
    allowed_roles: Annotated[frozenset[Role], Field(min_length=1)]
    source_version: Annotated[str, Field(min_length=1, max_length=128)]
    checksum: Annotated[str, Field(min_length=32, max_length=128)]
    vector: Annotated[tuple[float, ...], Field(min_length=1, max_length=4096)]
    embedding_model_id: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    embedding_dimensions: Annotated[int, Field(ge=1, le=4096)] | None = None
    pipeline_version: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class VectorQuery(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    allowed_classifications: Annotated[frozenset[Classification], Field(min_length=1)]
    allowed_roles: Annotated[frozenset[Role], Field(min_length=1)]
    query_vector: Annotated[tuple[float, ...], Field(min_length=1, max_length=4096)]
    top_k: Annotated[int, Field(ge=1, le=100)]


class VectorMatch(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    chunk_id: Annotated[ChunkId, Field(min_length=1, max_length=128)]
    score: Annotated[float, Field(ge=0.0, le=1.0)]


@runtime_checkable
class ModelProvider(Protocol):
    async def converse(self, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...


@runtime_checkable
class TextExtractor(Protocol):
    async def extract(self, document: Document) -> ExtractedDocument: ...


@runtime_checkable
class ChunkingStrategy(Protocol):
    def chunk(
        self,
        document: Document,
        extracted: ExtractedDocument,
        *,
        created_at: datetime,
    ) -> tuple[DocumentChunk, ...]: ...


@runtime_checkable
class VectorRepository(Protocol):
    async def upsert(self, records: Sequence[VectorRecord]) -> None: ...

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None: ...

    async def delete_version(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> None: ...

    async def query(self, query: VectorQuery) -> tuple[VectorMatch, ...]: ...


@runtime_checkable
class ChunkStore(Protocol):
    async def put(self, chunk: DocumentChunk) -> None: ...

    async def get(self, tenant_id: TenantId, chunk_id: ChunkId) -> DocumentChunk | None: ...

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None: ...

    async def delete_version(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> None: ...


@runtime_checkable
class DocumentRepository(Protocol):
    async def save(self, document: Document) -> None: ...

    async def get(self, tenant_id: TenantId, document_id: DocumentId) -> Document | None: ...


@runtime_checkable
class UserProfileRepository(Protocol):
    async def save(self, profile: UserProfile) -> None: ...

    async def get(self, tenant_id: TenantId, user_id: UserId) -> UserProfile | None: ...


@runtime_checkable
class ChatSessionRepository(Protocol):
    async def save(self, session: ChatSession) -> None: ...

    async def get(self, tenant_id: TenantId, session_id: SessionId) -> ChatSession | None: ...

    async def list_for_user(
        self, tenant_id: TenantId, user_id: UserId
    ) -> tuple[ChatSession, ...]: ...


@runtime_checkable
class ChatSessionCommandRepository(ChatSessionRepository, Protocol):
    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> bool: ...


@runtime_checkable
class ChatMessageRepository(Protocol):
    async def append(self, message: ChatMessage) -> None: ...

    async def list_for_session(
        self, tenant_id: TenantId, session_id: SessionId
    ) -> tuple[ChatMessage, ...]: ...


@runtime_checkable
class UsageRecordRepository(Protocol):
    async def save(self, record: UsageRecord) -> None: ...

    async def list_for_user(
        self, tenant_id: TenantId, user_id: UserId
    ) -> tuple[UsageRecord, ...]: ...


@runtime_checkable
class IngestionJobRepository(Protocol):
    async def save(self, job: IngestionJob) -> None: ...

    async def claim(self, job: IngestionJob) -> IngestionJobClaim: ...

    async def get(self, tenant_id: TenantId, job_id: JobId) -> IngestionJob | None: ...

    async def find_by_fingerprint(
        self, tenant_id: TenantId, fingerprint: str
    ) -> IngestionJob | None: ...


@runtime_checkable
class AgentRuntimeClient(Protocol):
    async def invoke(self, agent_id: str, request: AgentRequest) -> AgentResponse: ...


@runtime_checkable
class SecretsProvider(Protocol):
    async def get_secret(self, secret_name: str) -> str: ...
