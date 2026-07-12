from datetime import timedelta

import pytest
from ia_application import (
    DocumentNotFoundError,
    EmbeddingResponse,
    IngestDocumentCommand,
    IngestionInProgressError,
    InvalidEmbeddingResponseError,
)
from ia_domain import (
    Classification,
    DocumentId,
    DocumentStatus,
    IngestionStatus,
    JobId,
    Role,
    TenantId,
)
from test_support import FakeEmbeddingProvider, InMemoryIndexActivationRepository

from tests.unit.phase3_ingestion_support import (
    NOW,
    Clock,
    chunk_count,
    command,
    environment,
    profile,
    responses,
)


@pytest.mark.asyncio
async def test_ingestion_publishes_generation_and_is_idempotent() -> None:
    expected_count = chunk_count()
    value = await environment(embedding_responses=responses(expected_count, batch_size=2))

    first = await value.service.ingest(command())
    second = await value.service.ingest(command("job-b"))

    assert first.status is IngestionStatus.SUCCEEDED
    assert first.chunks_created == expected_count
    assert first.vectors_created == expected_count
    assert second == first
    assert len(value.extractor.requests) == 1
    stored = await value.documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None
    assert stored.status is DocumentStatus.INDEXED
    assert stored.active_generation_id == first.generation_id
    assert all(chunk.generation_id == first.generation_id for chunk in value.chunks.chunks)
    active = await value.generations.get(
        TenantId("tenant-a"),
        DocumentId("document-a"),
        str(first.generation_id),
    )
    assert active is not None
    assert active.status.value == "active"


@pytest.mark.asyncio
async def test_invalid_embedding_response_records_stable_failure_code() -> None:
    value = await environment(
        embedding_responses=[
            EmbeddingResponse(
                model_id="embedding-model-v1",
                dimensions=2,
                vectors=((1.0, 0.0),),
            )
        ]
    )

    with pytest.raises(InvalidEmbeddingResponseError):
        await value.service.ingest(command("job-failed", batch_size=10))

    assert value.chunks.chunks == ()
    assert value.vectors.records == ()
    failed = await value.jobs.get(TenantId("tenant-a"), JobId("job-failed"))
    assert failed is not None
    assert failed.status is IngestionStatus.FAILED
    assert failed.error_code == "INVALID_EMBEDDING_RESPONSE"
    stored = await value.documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None
    assert stored.status is DocumentStatus.FAILED


@pytest.mark.asyncio
async def test_wrong_tenant_cannot_start_ingestion() -> None:
    value = await environment()

    with pytest.raises(DocumentNotFoundError):
        await value.service.ingest(
            IngestDocumentCommand(
                tenant_id=TenantId("tenant-b"),
                document_id=DocumentId("document-a"),
                job_id=JobId("job-cross-tenant"),
                embedding_model_alias="default",
            )
        )

    assert value.extractor.requests == []


@pytest.mark.asyncio
async def test_document_version_lease_blocks_different_pipeline_fingerprint() -> None:
    value = await environment()
    owner_token = str(command("other-job").job_id)
    await value.leases.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token=owner_token,
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )

    with pytest.raises(IngestionInProgressError):
        await value.service.ingest(command("job-b", pipeline_version="ingestion-v2"))

    assert value.extractor.requests == []


@pytest.mark.asyncio
async def test_permissions_change_forces_new_generation() -> None:
    expected_count = chunk_count()
    value = await environment(
        embedding_responses=(
            responses(expected_count, batch_size=2) + responses(expected_count, batch_size=2)
        )
    )

    first = await value.service.ingest(command("job-a"))
    current = await value.documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert current is not None
    await value.documents.save(
        current.model_copy(
            update={
                "allowed_roles": frozenset({Role.TENANT_ADMIN}),
                "classification": Classification.RESTRICTED,
            }
        ),
        expected_revision=current.revision,
    )

    second = await value.service.ingest(command("job-b"))

    assert second.fingerprint != first.fingerprint
    assert second.authorization_checksum != first.authorization_checksum
    assert second.generation_id != first.generation_id
    assert len(value.extractor.requests) == 2
    assert all(chunk.generation_id == second.generation_id for chunk in value.chunks.chunks)


@pytest.mark.asyncio
async def test_embedding_profile_revision_forces_reingestion() -> None:
    expected_count = chunk_count()
    value = await environment(
        embedding_responses=responses(expected_count, batch_size=2),
        embedding_profile=profile(revision="profile-v1"),
    )
    first = await value.service.ingest(command("job-a"))
    replacement_embeddings = FakeEmbeddingProvider(
        responses(expected_count, batch_size=2),
        profile=profile(revision="profile-v2"),
    )
    replacement_service = type(value.service)(
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
        chunker=value.chunker,
        embeddings=replacement_embeddings,
        chunks=value.chunks,
        vectors=value.vectors,
        clock=Clock(),
    )

    second = await replacement_service.ingest(command("job-b"))

    assert second.fingerprint != first.fingerprint
    assert second.embedding_profile_revision == "profile-v2"


@pytest.mark.asyncio
async def test_model_id_cannot_change_between_embedding_batches() -> None:
    value = await environment(
        embedding_responses=[
            EmbeddingResponse(
                model_id="embedding-model-v1",
                dimensions=2,
                vectors=((1.0, 0.0),),
            ),
            EmbeddingResponse(
                model_id="embedding-model-v2",
                dimensions=2,
                vectors=((0.0, 1.0),),
            ),
            EmbeddingResponse(
                model_id="embedding-model-v1",
                dimensions=2,
                vectors=((1.0, 1.0),),
            ),
        ],
        embedding_profile=profile(model_id="embedding-model-v1"),
    )

    with pytest.raises(InvalidEmbeddingResponseError, match="model changed"):
        await value.service.ingest(command("job-model-change", batch_size=1))

    assert value.chunks.chunks == ()
    assert value.vectors.records == ()


@pytest.mark.asyncio
async def test_non_finite_embedding_value_is_rejected() -> None:
    expected_count = chunk_count()
    vectors = tuple(
        (float("nan"), 0.0) if index == 0 else (1.0, 0.0) for index in range(expected_count)
    )
    value = await environment(
        embedding_responses=[
            EmbeddingResponse(
                model_id="embedding-model-v1",
                dimensions=2,
                vectors=vectors,
            )
        ]
    )

    with pytest.raises(InvalidEmbeddingResponseError, match="non-finite"):
        await value.service.ingest(command("job-nan", batch_size=10))
