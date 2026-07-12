from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from ia_application import (
    ChunkingConfig,
    DocumentIngestionService,
    EmbeddingProfile,
    EmbeddingResponse,
    ExtractedDocument,
    ExtractedSection,
    IngestDocumentCommand,
    ParagraphChunker,
    RepositoryConflictError,
    VectorRecord,
)
from ia_domain import (
    ChunkId,
    Classification,
    Document,
    DocumentChunk,
    DocumentId,
    DocumentStatus,
    IndexGeneration,
    IndexGenerationStatus,
    JobId,
    Role,
    TenantId,
    UserId,
)
from test_support import (
    FakeEmbeddingProvider,
    FakeTextExtractor,
    InMemoryChunkStore,
    InMemoryDocumentIngestionLeaseRepository,
    InMemoryDocumentRepository,
    InMemoryIndexActivationRepository,
    InMemoryIndexGenerationRepository,
    InMemoryIngestionJobRepository,
    InMemoryVectorRepository,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self._value = NOW

    def __call__(self) -> datetime:
        value = self._value
        self._value += timedelta(seconds=1)
        return value


class Environment(NamedTuple):
    service: DocumentIngestionService
    documents: InMemoryDocumentRepository
    jobs: InMemoryIngestionJobRepository
    leases: InMemoryDocumentIngestionLeaseRepository
    generations: InMemoryIndexGenerationRepository
    extractor: FakeTextExtractor
    embeddings: FakeEmbeddingProvider
    chunks: InMemoryChunkStore
    vectors: InMemoryVectorRepository
    chunker: ParagraphChunker


def document(
    *,
    tenant: str = "tenant-a",
    status: DocumentStatus = DocumentStatus.UPLOADED,
    active_generation_id: str | None = None,
) -> Document:
    return Document(
        tenant_id=TenantId(tenant),
        document_id=DocumentId("document-a"),
        owner_user_id=UserId("user-a"),
        title="Policy",
        source_uri=f"s3://documents/{tenant}/document-a/v1/source.pdf",
        source_version="v1",
        source_checksum="a" * 64,
        content_type="application/pdf",
        language="fr",
        classification=Classification.CONFIDENTIAL,
        allowed_roles=frozenset({Role.SUPPORT, Role.TENANT_ADMIN}),
        status=status,
        active_generation_id=active_generation_id,
        active_index_fingerprint="f" * 64 if active_generation_id is not None else None,
        last_fencing_token=1 if active_generation_id is not None else 0,
        created_at=NOW,
        updated_at=NOW,
    )


def extracted(*, tenant: str = "tenant-a", content: str | None = None) -> ExtractedDocument:
    return ExtractedDocument(
        tenant_id=TenantId(tenant),
        document_id=DocumentId("document-a"),
        source_version="v1",
        sections=(
            ExtractedSection(
                title="Policy",
                content=content
                or (
                    "First paragraph contains the refund rules and processing deadlines. "
                    "It includes enough detail to form a useful retrieval chunk.\n\n"
                    "Second paragraph explains exceptional approval rules and audit controls. "
                    "It also contains enough detail for a second retrieval chunk."
                ),
            ),
        ),
    )


def profile(
    *,
    revision: str = "profile-v1",
    model_id: str = "embedding-model-v1",
    dimensions: int = 2,
) -> EmbeddingProfile:
    return EmbeddingProfile(
        alias="default",
        revision=revision,
        model_id=model_id,
        dimensions=dimensions,
    )


def responses(
    chunk_count: int,
    *,
    batch_size: int,
    model_id: str = "embedding-model-v1",
    dimensions: int = 2,
) -> list[EmbeddingResponse]:
    result: list[EmbeddingResponse] = []
    for offset in range(0, chunk_count, batch_size):
        batch_count = min(batch_size, chunk_count - offset)
        vectors = tuple(
            tuple(float(index + dimension + 1) for dimension in range(dimensions))
            for index in range(batch_count)
        )
        result.append(
            EmbeddingResponse(
                model_id=model_id,
                dimensions=dimensions,
                vectors=vectors,
            )
        )
    return result


def chunk_count(value: Document | None = None) -> int:
    target = value or document()
    return len(
        ParagraphChunker(
            ChunkingConfig(
                max_characters=130,
                overlap_characters=10,
                minimum_characters=30,
                version="paragraph-test-v1",
            )
        ).chunk(target, extracted(), generation_id="expected", created_at=NOW)
    )


async def environment(
    *,
    stored_document: Document | None = None,
    embedding_responses: Sequence[EmbeddingResponse] = (),
    embedding_profile: EmbeddingProfile | None = None,
    vector_repository: InMemoryVectorRepository | None = None,
) -> Environment:
    target = stored_document or document()
    documents = InMemoryDocumentRepository()
    await documents.save(target)
    jobs = InMemoryIngestionJobRepository()
    leases = InMemoryDocumentIngestionLeaseRepository()
    generations = InMemoryIndexGenerationRepository()
    extractor = FakeTextExtractor(
        {
            (target.tenant_id, target.document_id, target.source_version): extracted(
                tenant=str(target.tenant_id)
            )
        }
    )
    embeddings = FakeEmbeddingProvider(embedding_responses, profile=embedding_profile)
    chunks = InMemoryChunkStore()
    vectors = vector_repository or InMemoryVectorRepository()
    chunker = ParagraphChunker(
        ChunkingConfig(
            max_characters=130,
            overlap_characters=10,
            minimum_characters=30,
            version="paragraph-test-v1",
        )
    )
    service = DocumentIngestionService(
        documents=documents,
        jobs=jobs,
        leases=leases,
        generations=generations,
        activations=InMemoryIndexActivationRepository(
            documents=documents,
            generations=generations,
            jobs=jobs,
        ),
        extractor=extractor,
        chunker=chunker,
        embeddings=embeddings,
        chunks=chunks,
        vectors=vectors,
        clock=Clock(),
    )
    return Environment(
        service,
        documents,
        jobs,
        leases,
        generations,
        extractor,
        embeddings,
        chunks,
        vectors,
        chunker,
    )


def command(
    job_id: str = "job-a",
    *,
    pipeline_version: str = "ingestion-v1",
    batch_size: int = 2,
) -> IngestDocumentCommand:
    return IngestDocumentCommand(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        job_id=JobId(job_id),
        embedding_model_alias="default",
        pipeline_version=pipeline_version,
        embedding_batch_size=batch_size,
    )


class DelegatingChunker:
    def __init__(self) -> None:
        self._delegate = ParagraphChunker(
            ChunkingConfig(
                max_characters=130,
                overlap_characters=10,
                minimum_characters=30,
                version="delegate-internal-v1",
            )
        )

    @property
    def version(self) -> str:
        return "custom-strategy-v1"

    def chunk(
        self,
        target: Document,
        value: ExtractedDocument,
        *,
        generation_id: str,
        created_at: datetime,
    ) -> tuple[DocumentChunk, ...]:
        chunks = self._delegate.chunk(
            target,
            value,
            generation_id=generation_id,
            created_at=created_at,
        )
        return tuple(
            chunk.model_copy(update={"chunking_version": self.version}) for chunk in chunks
        )


class FailingVectorRepository(InMemoryVectorRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_on_upsert = False

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not self.fail_on_upsert:
            await super().upsert(records)
            return
        if records:
            await super().upsert(records[:1])
        raise RuntimeError("vector write failed")


class ConflictingActivationRepository:
    async def activate(self, **kwargs: object) -> Document:
        del kwargs
        raise RepositoryConflictError("document revision changed")


async def seed_active_generation(value: Environment, target: Document) -> None:
    old_chunk = DocumentChunk(
        tenant_id=target.tenant_id,
        document_id=target.document_id,
        chunk_id=ChunkId("old-chunk"),
        generation_id="generation-old",
        source_version=target.source_version,
        source_uri=target.source_uri,
        title=target.title,
        section="Old",
        language=target.language,
        classification=target.classification,
        allowed_roles=target.allowed_roles,
        checksum="d" * 64,
        content="previous active content",
        created_at=NOW,
        end_offset=23,
    )
    await value.chunks.put_batch((old_chunk,))
    await value.vectors.upsert(
        (
            VectorRecord(
                tenant_id=target.tenant_id,
                document_id=target.document_id,
                chunk_id=old_chunk.chunk_id,
                generation_id=old_chunk.generation_id,
                classification=target.classification,
                allowed_roles=target.allowed_roles,
                source_version=target.source_version,
                checksum=old_chunk.checksum,
                vector=(1.0, 0.0),
                embedding_model_id="embedding-model-v1",
                embedding_dimensions=2,
                pipeline_version="ingestion-v0",
            ),
        )
    )
    await value.generations.save(
        IndexGeneration(
            tenant_id=target.tenant_id,
            document_id=target.document_id,
            source_version=target.source_version,
            generation_id="generation-old",
            fingerprint="f" * 64,
            authorization_checksum="e" * 64,
            embedding_profile_revision="profile-v0",
            embedding_model_id="embedding-model-v1",
            embedding_dimensions=2,
            status=IndexGenerationStatus.ACTIVE,
            fencing_token=1,
            chunk_count=1,
            vector_count=1,
            created_at=NOW,
            ready_at=NOW,
            activated_at=NOW,
        )
    )
