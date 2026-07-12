from collections.abc import Sequence
from datetime import datetime

from ia_agent_contracts import AgentRequest, AgentResponse
from ia_application import (
    EmbeddingProfile,
    EmbeddingRequest,
    EmbeddingResponse,
    ExtractedDocument,
    IngestionJobClaim,
    IngestionLease,
    IngestionLeaseClaim,
    ModelRequest,
    ModelResponse,
    RepositoryConflictError,
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
    DocumentStatus,
    IndexGeneration,
    IndexGenerationStatus,
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
    def __init__(
        self,
        responses: Sequence[EmbeddingResponse],
        *,
        profile: EmbeddingProfile | None = None,
    ) -> None:
        self._responses = list(responses)
        first_response = self._responses[0] if self._responses else None
        self._profile = profile or EmbeddingProfile(
            alias="default",
            revision="fake-profile-v1",
            model_id=(
                first_response.model_id if first_response is not None else "fake-embedding-model"
            ),
            dimensions=first_response.dimensions if first_response is not None else 2,
        )
        self.profile_requests: list[str] = []
        self.requests: list[EmbeddingRequest] = []

    async def resolve_profile(self, model_alias: str) -> EmbeddingProfile:
        self.profile_requests.append(model_alias)
        return self._profile.model_copy(update={"alias": model_alias})

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
        self._records: dict[tuple[TenantId, str, ChunkId], VectorRecord] = {}

    @property
    def records(self) -> tuple[VectorRecord, ...]:
        return tuple(self._records.values())

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        for record in records:
            key = (record.tenant_id, record.generation_id, record.chunk_id)
            self._records[key] = record

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None:
        keys = [
            key
            for key, record in self._records.items()
            if record.tenant_id == tenant_id and record.document_id == document_id
        ]
        for key in keys:
            del self._records[key]

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        keys = [
            key
            for key, record in self._records.items()
            if record.tenant_id == tenant_id
            and record.document_id == document_id
            and record.generation_id == generation_id
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
        self._chunks: dict[tuple[TenantId, str, ChunkId], DocumentChunk] = {}

    @property
    def chunks(self) -> tuple[DocumentChunk, ...]:
        return tuple(self._chunks.values())

    async def put(self, chunk: DocumentChunk) -> None:
        key = (chunk.tenant_id, chunk.generation_id, chunk.chunk_id)
        self._chunks[key] = chunk

    async def put_batch(self, chunks: Sequence[DocumentChunk]) -> None:
        for chunk in chunks:
            await self.put(chunk)

    async def get(
        self,
        tenant_id: TenantId,
        generation_id: str,
        chunk_id: ChunkId,
    ) -> DocumentChunk | None:
        return self._chunks.get((tenant_id, generation_id, chunk_id))

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None:
        keys = [
            key
            for key, chunk in self._chunks.items()
            if chunk.tenant_id == tenant_id and chunk.document_id == document_id
        ]
        for key in keys:
            del self._chunks[key]

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        keys = [
            key
            for key, chunk in self._chunks.items()
            if chunk.tenant_id == tenant_id
            and chunk.document_id == document_id
            and chunk.generation_id == generation_id
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

    async def save(
        self,
        document: Document,
        *,
        expected_revision: int | None = None,
    ) -> Document:
        key = (document.tenant_id, document.document_id)
        current = self._documents.get(key)
        if current is None:
            if expected_revision is not None:
                raise RepositoryConflictError("document does not exist")
            self._documents[key] = document
            return document
        if expected_revision is not None and current.revision != expected_revision:
            raise RepositoryConflictError("document revision changed")
        stored = document.model_copy(update={"revision": current.revision + 1})
        self._documents[key] = stored
        return stored

    async def get(self, tenant_id: TenantId, document_id: DocumentId) -> Document | None:
        return self._documents.get((tenant_id, document_id))


class InMemoryIndexGenerationRepository:
    def __init__(self) -> None:
        self._generations: dict[tuple[TenantId, DocumentId, str], IndexGeneration] = {}

    @property
    def generations(self) -> tuple[IndexGeneration, ...]:
        return tuple(self._generations.values())

    async def save(self, generation: IndexGeneration) -> None:
        key = (
            generation.tenant_id,
            generation.document_id,
            generation.generation_id,
        )
        self._generations[key] = generation

    async def get(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> IndexGeneration | None:
        return self._generations.get((tenant_id, document_id, generation_id))


class InMemoryDocumentIngestionLeaseRepository:
    def __init__(self) -> None:
        self._leases: dict[tuple[TenantId, DocumentId, str], IngestionLease] = {}
        self._fencing_tokens: dict[tuple[TenantId, DocumentId, str], int] = {}

    async def acquire(
        self,
        *,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
        owner_token: str,
        expires_at: datetime,
        now: datetime,
    ) -> IngestionLeaseClaim:
        key = (tenant_id, document_id, source_version)
        current = self._leases.get(key)
        if current is not None and current.expires_at > now:
            return IngestionLeaseClaim(
                lease=current,
                acquired=current.owner_token == owner_token,
            )
        fencing_token = self._fencing_tokens.get(key, 0) + 1
        self._fencing_tokens[key] = fencing_token
        lease = IngestionLease(
            tenant_id=tenant_id,
            document_id=document_id,
            source_version=source_version,
            owner_token=owner_token,
            fencing_token=fencing_token,
            expires_at=expires_at,
        )
        self._leases[key] = lease
        return IngestionLeaseClaim(lease=lease, acquired=True)

    async def release(self, lease: IngestionLease) -> None:
        key = (lease.tenant_id, lease.document_id, lease.source_version)
        current = self._leases.get(key)
        if current == lease:
            del self._leases[key]


class InMemoryIngestionJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[tuple[TenantId, JobId], IngestionJob] = {}
        self._fingerprints: dict[tuple[TenantId, str], JobId] = {}

    async def save(self, job: IngestionJob) -> None:
        self._jobs[(job.tenant_id, job.job_id)] = job
        if job.fingerprint is not None:
            self._fingerprints[(job.tenant_id, job.fingerprint)] = job.job_id

    async def claim(self, job: IngestionJob) -> IngestionJobClaim:
        if job.fingerprint is None or job.fencing_token is None:
            msg = "claimed ingestion jobs require a fingerprint and fencing token"
            raise ValueError(msg)
        existing = await self.find_by_fingerprint(job.tenant_id, job.fingerprint)
        if existing is not None and existing.status is IngestionStatus.SUCCEEDED:
            return IngestionJobClaim(job=existing, acquired=False)
        if (
            existing is not None
            and existing.status is IngestionStatus.RUNNING
            and existing.fencing_token is not None
            and existing.fencing_token >= job.fencing_token
        ):
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


class InMemoryIndexActivationRepository:
    def __init__(
        self,
        *,
        documents: InMemoryDocumentRepository,
        generations: InMemoryIndexGenerationRepository,
        jobs: InMemoryIngestionJobRepository,
    ) -> None:
        self._documents = documents
        self._generations = generations
        self._jobs = jobs

    async def activate(
        self,
        *,
        generation: IndexGeneration,
        succeeded_job: IngestionJob,
        expected_document_revision: int,
        activated_at: datetime,
    ) -> Document:
        document = await self._documents.get(generation.tenant_id, generation.document_id)
        if document is None:
            raise RepositoryConflictError("document does not exist")
        if document.revision != expected_document_revision:
            raise RepositoryConflictError("document revision changed")
        if document.source_version != generation.source_version:
            raise RepositoryConflictError("document source version changed")
        if generation.status is not IndexGenerationStatus.READY:
            raise RepositoryConflictError("index generation is not ready")
        if generation.fencing_token <= document.last_fencing_token:
            raise RepositoryConflictError("stale ingestion fencing token")
        if (
            succeeded_job.generation_id != generation.generation_id
            or succeeded_job.fingerprint != generation.fingerprint
            or succeeded_job.status is not IngestionStatus.SUCCEEDED
        ):
            raise RepositoryConflictError("activation metadata is inconsistent")

        activated_document = await self._documents.save(
            document.model_copy(
                update={
                    "active_generation_id": generation.generation_id,
                    "active_index_fingerprint": generation.fingerprint,
                    "last_fencing_token": generation.fencing_token,
                    "status": DocumentStatus.INDEXED,
                    "updated_at": activated_at,
                }
            ),
            expected_revision=expected_document_revision,
        )
        await self._generations.save(
            generation.model_copy(
                update={
                    "status": IndexGenerationStatus.ACTIVE,
                    "activated_at": activated_at,
                }
            )
        )
        await self._jobs.save(succeeded_job)
        return activated_document


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

    async def list_for_user(
        self, tenant_id: TenantId, user_id: UserId
    ) -> tuple[ChatSession, ...]:
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

    async def list_for_user(
        self, tenant_id: TenantId, user_id: UserId
    ) -> tuple[UsageRecord, ...]:
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
