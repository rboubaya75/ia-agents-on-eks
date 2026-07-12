import pytest
from ia_application import (
    ConcurrentDocumentUpdateError,
    DocumentIngestionService,
    DocumentNotReadyError,
    IngestionFailedError,
    InvalidExtractionError,
)
from ia_domain import DocumentId, DocumentStatus, JobId, TenantId
from test_support import InMemoryIndexActivationRepository

from tests.unit.phase3_ingestion_support import (
    Clock,
    ConflictingActivationRepository,
    DelegatingChunker,
    FailingVectorRepository,
    chunk_count,
    command,
    document,
    environment,
    extracted,
    responses,
    seed_active_generation,
)


@pytest.mark.asyncio
async def test_ingestion_accepts_an_alternative_chunking_strategy() -> None:
    custom_chunker = DelegatingChunker()
    expected = custom_chunker.chunk(
        document(),
        extracted(),
        generation_id="expected",
        created_at=Clock()(),
    )
    value = await environment(embedding_responses=responses(len(expected), batch_size=10))
    service = DocumentIngestionService(
        documents=value.documents,
        jobs=value.jobs,
        leases=value.leases,
        generations=value.generations,
        activations=InMemoryIndexActivationRepository(
            documents=value.documents,
            generations=value.generations,
            jobs=value.jobs,
        ),
        extractor=value.extractor,
        chunker=custom_chunker,
        embeddings=value.embeddings,
        chunks=value.chunks,
        vectors=value.vectors,
        clock=Clock(),
    )

    result = await service.ingest(command("job-custom", batch_size=10))

    assert result.chunking_version == "custom-strategy-v1"
    assert all(chunk.chunking_version == "custom-strategy-v1" for chunk in value.chunks.chunks)


@pytest.mark.asyncio
async def test_failed_reindex_preserves_previous_active_generation() -> None:
    target = document(
        status=DocumentStatus.INDEXED,
        active_generation_id="generation-old",
    )
    vectors = FailingVectorRepository()
    value = await environment(
        stored_document=target,
        embedding_responses=responses(chunk_count(target), batch_size=10),
        vector_repository=vectors,
    )
    await seed_active_generation(value, target)
    vectors.fail_on_upsert = True

    with pytest.raises(IngestionFailedError):
        await value.service.ingest(command("job-reindex", batch_size=10))

    stored = await value.documents.get(target.tenant_id, target.document_id)
    assert stored is not None
    assert stored.status is DocumentStatus.INDEXED
    assert stored.active_generation_id == "generation-old"
    assert {chunk.generation_id for chunk in value.chunks.chunks} == {"generation-old"}
    assert {record.generation_id for record in value.vectors.records} == {"generation-old"}


@pytest.mark.asyncio
async def test_activation_conflict_cleans_candidate_and_records_failure() -> None:
    value = await environment(embedding_responses=responses(chunk_count(), batch_size=10))
    service = DocumentIngestionService(
        documents=value.documents,
        jobs=value.jobs,
        leases=value.leases,
        generations=value.generations,
        activations=ConflictingActivationRepository(),
        extractor=value.extractor,
        chunker=value.chunker,
        embeddings=value.embeddings,
        chunks=value.chunks,
        vectors=value.vectors,
        clock=Clock(),
    )

    with pytest.raises(ConcurrentDocumentUpdateError):
        await service.ingest(command("job-conflict", batch_size=10))

    assert value.chunks.chunks == ()
    assert value.vectors.records == ()
    failed = await value.jobs.get(TenantId("tenant-a"), JobId("job-conflict"))
    assert failed is not None
    assert failed.error_code == "INDEX_ACTIVATION_CONFLICT"


@pytest.mark.asyncio
async def test_mismatched_extraction_fails_closed_before_candidate_writes() -> None:
    value = await environment()
    value.extractor._responses[(TenantId("tenant-a"), DocumentId("document-a"), "v1")] = extracted(
        tenant="tenant-b"
    )

    with pytest.raises(InvalidExtractionError):
        await value.service.ingest(command("job-mismatch"))

    assert value.chunks.chunks == ()
    assert value.vectors.records == ()


@pytest.mark.asyncio
async def test_document_state_must_allow_ingestion() -> None:
    value = await environment(stored_document=document(status=DocumentStatus.DELETING))

    with pytest.raises(DocumentNotReadyError):
        await value.service.ingest(command("job-deleting"))


@pytest.mark.asyncio
async def test_empty_normalized_extraction_is_rejected() -> None:
    value = await environment()
    value.extractor._responses[(TenantId("tenant-a"), DocumentId("document-a"), "v1")] = extracted(
        content="   \n\n  "
    )

    with pytest.raises(InvalidExtractionError, match="no indexable content"):
        await value.service.ingest(command("job-empty"))
