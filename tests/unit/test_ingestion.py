from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from ia_application import (
    ChunkingConfig,
    DocumentIngestionService,
    DocumentNotFoundError,
    EmbeddingResponse,
    ExtractedDocument,
    ExtractedSection,
    IngestDocumentCommand,
    IngestionFailedError,
    InvalidEmbeddingResponseError,
    InvalidExtractionError,
    ParagraphChunker,
    VectorRecord,
)
from ia_domain import (
    Classification,
    Document,
    DocumentId,
    DocumentStatus,
    IngestionStatus,
    JobId,
    Role,
    TenantId,
    UserId,
)
from test_support import (
    FakeEmbeddingProvider,
    FakeTextExtractor,
    InMemoryChunkStore,
    InMemoryDocumentRepository,
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


def _document(*, tenant: str = "tenant-a") -> Document:
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
        status=DocumentStatus.UPLOADED,
        created_at=NOW,
        updated_at=NOW,
    )


def _extracted(*, tenant: str = "tenant-a") -> ExtractedDocument:
    return ExtractedDocument(
        tenant_id=TenantId(tenant),
        document_id=DocumentId("document-a"),
        source_version="v1",
        sections=(
            ExtractedSection(
                title="Policy",
                content=(
                    "First paragraph contains the refund rules and processing deadlines. "
                    "It includes enough detail to form a useful retrieval chunk.\n\n"
                    "Second paragraph explains exceptional approval rules and audit controls. "
                    "It also contains enough detail for a second retrieval chunk."
                ),
            ),
        ),
    )


async def _service(
    *,
    embedding_responses: list[EmbeddingResponse],
) -> tuple[
    DocumentIngestionService,
    InMemoryDocumentRepository,
    InMemoryIngestionJobRepository,
    FakeTextExtractor,
    FakeEmbeddingProvider,
    InMemoryChunkStore,
    InMemoryVectorRepository,
    ParagraphChunker,
]:
    document = _document()
    documents = InMemoryDocumentRepository()
    await documents.save(document)
    jobs = InMemoryIngestionJobRepository()
    extractor = FakeTextExtractor(
        {(document.tenant_id, document.document_id, document.source_version): _extracted()}
    )
    embeddings = FakeEmbeddingProvider(embedding_responses)
    chunks = InMemoryChunkStore()
    vectors = InMemoryVectorRepository()
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
        extractor=extractor,
        chunker=chunker,
        embeddings=embeddings,
        chunks=chunks,
        vectors=vectors,
        clock=Clock(),
    )
    return service, documents, jobs, extractor, embeddings, chunks, vectors, chunker


@pytest.mark.asyncio
async def test_ingestion_indexes_chunks_in_batches_and_is_idempotent() -> None:
    chunker = ParagraphChunker(
        ChunkingConfig(
            max_characters=130,
            overlap_characters=10,
            minimum_characters=30,
            version="paragraph-test-v1",
        )
    )
    expected = chunker.chunk(_document(), _extracted(), created_at=NOW)
    responses = [
        EmbeddingResponse(
            model_id="embedding-model-v1",
            dimensions=2,
            vectors=tuple((float(index + 1), 1.0) for index in range(len(batch))),
        )
        for batch in (expected[index : index + 2] for index in range(0, len(expected), 2))
    ]
    (
        service,
        documents,
        _jobs,
        extractor,
        embeddings,
        chunks,
        vectors,
        _chunker,
    ) = await _service(embedding_responses=responses)
    command = IngestDocumentCommand(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        job_id=JobId("job-a"),
        embedding_model_alias="default",
        embedding_batch_size=2,
    )

    first = await service.ingest(command)
    second = await service.ingest(command.model_copy(update={"job_id": JobId("job-b")}))

    assert first.status is IngestionStatus.SUCCEEDED
    assert first.chunks_created == len(expected)
    assert first.vectors_created == len(expected)
    assert second == first
    assert len(extractor.requests) == 1
    assert len(embeddings.requests) == len(responses)
    assert len(chunks.chunks) == len(expected)
    assert len(vectors.records) == len(expected)
    assert all(record.embedding_model_id == "embedding-model-v1" for record in vectors.records)
    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None
    assert stored.status is DocumentStatus.INDEXED


@pytest.mark.asyncio
async def test_invalid_embedding_response_cleans_partial_version_and_records_failure() -> None:
    (
        service,
        documents,
        jobs,
        _extractor,
        _embeddings,
        chunks,
        vectors,
        _chunker,
    ) = await _service(
        embedding_responses=[
            EmbeddingResponse(
                model_id="embedding-model-v1",
                dimensions=2,
                vectors=((1.0, 0.0),),
            )
        ]
    )
    command = IngestDocumentCommand(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        job_id=JobId("job-failed"),
        embedding_model_alias="default",
        embedding_batch_size=10,
    )

    with pytest.raises(InvalidEmbeddingResponseError):
        await service.ingest(command)

    assert chunks.chunks == ()
    assert vectors.records == ()
    failed = await jobs.get(TenantId("tenant-a"), JobId("job-failed"))
    assert failed is not None
    assert failed.status is IngestionStatus.FAILED
    assert failed.error_code == "InvalidEmbeddingResponseError"
    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None
    assert stored.status is DocumentStatus.FAILED


@pytest.mark.asyncio
async def test_wrong_tenant_cannot_start_ingestion() -> None:
    (
        service,
        _documents,
        _jobs,
        extractor,
        _embeddings,
        _chunks,
        _vectors,
        _chunker,
    ) = await _service(embedding_responses=[])

    with pytest.raises(DocumentNotFoundError):
        await service.ingest(
            IngestDocumentCommand(
                tenant_id=TenantId("tenant-b"),
                document_id=DocumentId("document-a"),
                job_id=JobId("job-cross-tenant"),
                embedding_model_alias="default",
            )
        )

    assert extractor.requests == []


class FailingVectorRepository(InMemoryVectorRepository):
    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        if records:
            await super().upsert(records[:1])
        raise RuntimeError("vector write failed")


@pytest.mark.asyncio
async def test_mismatched_extraction_fails_closed_before_replacement() -> None:
    document = _document().model_copy(update={"status": DocumentStatus.INDEXED})
    documents = InMemoryDocumentRepository()
    await documents.save(document)
    jobs = InMemoryIngestionJobRepository()
    extractor = FakeTextExtractor(
        {
            (document.tenant_id, document.document_id, document.source_version): _extracted(
                tenant="tenant-b"
            )
        }
    )
    chunks = InMemoryChunkStore()
    vectors = InMemoryVectorRepository()
    service = DocumentIngestionService(
        documents=documents,
        jobs=jobs,
        extractor=extractor,
        chunker=ParagraphChunker(
            ChunkingConfig(
                max_characters=130,
                overlap_characters=10,
                minimum_characters=30,
                version="paragraph-test-v1",
            )
        ),
        embeddings=FakeEmbeddingProvider([]),
        chunks=chunks,
        vectors=vectors,
        clock=Clock(),
    )

    with pytest.raises(InvalidExtractionError):
        await service.ingest(
            IngestDocumentCommand(
                tenant_id=document.tenant_id,
                document_id=document.document_id,
                job_id=JobId("job-mismatch"),
                embedding_model_alias="default",
            )
        )

    stored = await documents.get(document.tenant_id, document.document_id)
    assert stored is not None
    assert stored.status is DocumentStatus.INDEXED


@pytest.mark.asyncio
async def test_partial_vector_write_is_cleaned_and_wrapped() -> None:
    document = _document()
    documents = InMemoryDocumentRepository()
    await documents.save(document)
    jobs = InMemoryIngestionJobRepository()
    extractor = FakeTextExtractor(
        {(document.tenant_id, document.document_id, document.source_version): _extracted()}
    )
    chunks = InMemoryChunkStore()
    vectors = FailingVectorRepository()
    chunker = ParagraphChunker(
        ChunkingConfig(
            max_characters=500,
            overlap_characters=20,
            minimum_characters=30,
            version="paragraph-test-v1",
        )
    )
    expected = chunker.chunk(document, _extracted(), created_at=NOW)
    service = DocumentIngestionService(
        documents=documents,
        jobs=jobs,
        extractor=extractor,
        chunker=chunker,
        embeddings=FakeEmbeddingProvider(
            [
                EmbeddingResponse(
                    model_id="embedding-model-v1",
                    dimensions=2,
                    vectors=tuple((1.0, 0.0) for _chunk in expected),
                )
            ]
        ),
        chunks=chunks,
        vectors=vectors,
        clock=Clock(),
    )

    with pytest.raises(IngestionFailedError):
        await service.ingest(
            IngestDocumentCommand(
                tenant_id=document.tenant_id,
                document_id=document.document_id,
                job_id=JobId("job-partial-write"),
                embedding_model_alias="default",
            )
        )

    assert chunks.chunks == ()
    assert vectors.records == ()
