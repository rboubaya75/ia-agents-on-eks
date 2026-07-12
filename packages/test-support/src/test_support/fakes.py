from collections.abc import Sequence

from ia_agent_contracts import AgentRequest, AgentResponse
from ia_application import (
    EmbeddingRequest,
    EmbeddingResponse,
    ExtractedDocument,
    IngestionJobClaim,
    ModelRequest,
    ModelResponse,
    VectorMatch,
    VectorQuery,
    VectorRecord,
)
from ia_domain import (
    ChatMessage,
    ChatSession,
    ChunkId,
    Document,
    DocumentChunk,
    DocumentId,
    IngestionJob,
    IngestionStatus,
    JobId,
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


class FakeEmbeddingProvider:
    def __init__(self, responses: Sequence[EmbeddingResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[EmbeddingRequest] = []

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        if not self._responses:
            msg = "no fake embedding response configured"
            raise RuntimeError(msg)
        return self._responses.pop(0)


class FakeTextExtractor:
    def __init__(
        self,
        responses: dict[tuple[TenantId, DocumentId, str], ExtractedDocument],
    ) -> None:
        self._responses = responses.copy()
        self.requests: list[Document] = []

    async def extract(self, document: Document) -> ExtractedDocument:
        self.requests.append(document)
        key = (document.tenant_id, document.document_id, document.source_version)
        try:
            return self._responses[key]
        except KeyError as error:
            msg = "no fake extraction configured"
            raise RuntimeError(msg) from error


class InMemoryVectorRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[TenantId, ChunkId], VectorRecord] = {}

    @property
    def records(self) -> tuple[VectorRecord, ...]:
        return tuple(self._records.values())

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        for record in records:
            self._records[(record.tenant_id, record.chunk_id)] = record

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None:
        keys = [
            key
            for key, record in self._records.items()
            if record.tenant_id == tenant_id and record.document_id == document_id
        ]
        for key in keys:
            del self._records[key]

    async def delete_version(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> None:
        keys = [
            key
            for key, record in self._records.items()
            if record.tenant_id == tenant_id
            and record.document_id == document_id
            and record.source_version == source_version
        ]
        for key in keys:
            del self._records[key]

    async def query(self, query: VectorQuery) -> tuple[VectorMatch, ...]:
        matches: list[VectorMatch] = []
        for record in self._records.values():
            if record.tenant_id != query.tenant_id:
                continue
            if record.classification not in query.allowed_classifications:
                continue
            if record.allowed_roles.isdisjoint(query.allowed_roles):
                continue
            score = self._cosine_similarity(query.query_vector, record.vector)
            matches.append(
                VectorMatch(
                    tenant_id=record.tenant_id,
                    document_id=record.document_id,
                    chunk_id=record.chunk_id,
                    score=score,
                )
            )
        matches.sort(key=lambda item: item.score, reverse=True)
        return tuple(matches[: query.top_k])

    @staticmethod
    def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        if len(left) != len(right):
            msg = "vector dimensions must match"
            raise ValueError(msg)
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = sum(value * value for value in left) ** 0.5
        right_norm = sum(value * value for value in right) ** 0.5
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        raw_score = dot / (left_norm * right_norm)
        return float(max(0.0, min(1.0, raw_score)))


class InMemoryChunkStore:
    def __init__(self) -> None:
        self._chunks: dict[tuple[TenantId, ChunkId], DocumentChunk] = {}

    @property
    def chunks(self) -> tuple[DocumentChunk, ...]:
        return tuple(self._chunks.values())

    async def put(self, chunk: DocumentChunk) -> None:
        self._chunks[(chunk.tenant_id, chunk.chunk_id)] = chunk

    async def get(self, tenant_id: TenantId, chunk_id: ChunkId) -> DocumentChunk | None:
        return self._chunks.get((tenant_id, chunk_id))

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None:
        keys = [
            key
            for key, chunk in self._chunks.items()
            if chunk.tenant_id == tenant_id and chunk.document_id == document_id
        ]
        for key in keys:
            del self._chunks[key]

    async def delete_version(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> None:
        keys = [
            key
            for key, chunk in self._chunks.items()
            if chunk.tenant_id == tenant_id
            and chunk.document_id == document_id
            and chunk.source_version == source_version
        ]
        for key in keys:
            del self._chunks[key]


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._documents: dict[tuple[TenantId, DocumentId], Document] = {}

    async def save(self, document: Document) -> None:
        self._documents[(document.tenant_id, document.document_id)] = document

    async def get(self, tenant_id: TenantId, document_id: DocumentId) -> Document | None:
        return self._documents.get((tenant_id, document_id))


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


class InMemoryIngestionJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[tuple[TenantId, JobId], IngestionJob] = {}
        self._fingerprints: dict[tuple[TenantId, str], JobId] = {}

    async def save(self, job: IngestionJob) -> None:
        self._jobs[(job.tenant_id, job.job_id)] = job
        if job.fingerprint is not None:
            self._fingerprints[(job.tenant_id, job.fingerprint)] = job.job_id

    async def claim(self, job: IngestionJob) -> IngestionJobClaim:
        if job.fingerprint is None:
            msg = "claimed ingestion jobs require a fingerprint"
            raise ValueError(msg)
        existing = await self.find_by_fingerprint(job.tenant_id, job.fingerprint)
        if existing is not None and existing.status in {
            IngestionStatus.RUNNING,
            IngestionStatus.SUCCEEDED,
        }:
            return IngestionJobClaim(job=existing, acquired=False)
        await self.save(job)
        return IngestionJobClaim(job=job, acquired=True)

    async def get(self, tenant_id: TenantId, job_id: JobId) -> IngestionJob | None:
        return self._jobs.get((tenant_id, job_id))

    async def find_by_fingerprint(
        self, tenant_id: TenantId, fingerprint: str
    ) -> IngestionJob | None:
        job_id = self._fingerprints.get((tenant_id, fingerprint))
        return None if job_id is None else self._jobs.get((tenant_id, job_id))


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
