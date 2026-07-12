from datetime import UTC, datetime

import pytest
from ia_application import IngestionJobClaim
from ia_domain import DocumentId, IngestionJob, IngestionStatus, JobId, TenantId
from test_support import InMemoryIngestionJobRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _job(
    job_id: str,
    status: IngestionStatus = IngestionStatus.RUNNING,
    *,
    fencing_token: int = 1,
) -> IngestionJob:
    return IngestionJob(
        tenant_id=TenantId("tenant-a"),
        job_id=JobId(job_id),
        document_id=DocumentId("document-a"),
        source_version="v1",
        status=status,
        fingerprint="a" * 64,
        generation_id=f"generation-{job_id}",
        source_checksum="b" * 64,
        authorization_checksum="c" * 64,
        embedding_model_alias="default",
        embedding_profile_revision="profile-v1",
        resolved_embedding_model_id="model-v1",
        embedding_dimensions=2,
        chunking_version="paragraph-v1",
        pipeline_version="ingestion-v1",
        fencing_token=fencing_token,
        started_at=NOW,
    )


@pytest.mark.asyncio
async def test_claim_returns_one_canonical_running_job_per_fingerprint() -> None:
    repository = InMemoryIngestionJobRepository()

    first = await repository.claim(_job("job-a"))
    second = await repository.claim(_job("job-b"))

    assert first == IngestionJobClaim(job=_job("job-a"), acquired=True)
    assert second == IngestionJobClaim(job=_job("job-a"), acquired=False)


@pytest.mark.asyncio
async def test_newer_fencing_token_can_take_over_stale_running_job() -> None:
    repository = InMemoryIngestionJobRepository()
    await repository.claim(_job("job-a", fencing_token=1))

    replacement = await repository.claim(_job("job-b", fencing_token=2))

    assert replacement.acquired is True
    assert replacement.job.job_id == JobId("job-b")


@pytest.mark.asyncio
async def test_failed_fingerprint_can_be_claimed_for_retry() -> None:
    repository = InMemoryIngestionJobRepository()
    failed = _job("job-failed", IngestionStatus.FAILED).model_copy(
        update={"completed_at": NOW, "error_code": "EXAMPLE_ERROR"}
    )
    await repository.save(failed)

    retry = await repository.claim(_job("job-retry", fencing_token=2))

    assert retry.acquired is True
    assert retry.job.job_id == JobId("job-retry")
