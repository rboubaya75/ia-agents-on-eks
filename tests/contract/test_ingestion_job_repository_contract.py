from datetime import UTC, datetime

import pytest
from ia_application import IngestionJobClaim
from ia_domain import DocumentId, IngestionJob, IngestionStatus, JobId, TenantId
from test_support import InMemoryIngestionJobRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _job(job_id: str, status: IngestionStatus = IngestionStatus.RUNNING) -> IngestionJob:
    return IngestionJob(
        tenant_id=TenantId("tenant-a"),
        job_id=JobId(job_id),
        document_id=DocumentId("document-a"),
        source_version="v1",
        status=status,
        fingerprint="a" * 64,
        source_checksum="b" * 64,
        embedding_model_alias="default",
        chunking_version="paragraph-v1",
        pipeline_version="ingestion-v1",
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
async def test_failed_fingerprint_can_be_claimed_for_retry() -> None:
    repository = InMemoryIngestionJobRepository()
    failed = _job("job-failed", IngestionStatus.FAILED).model_copy(
        update={"completed_at": NOW, "error_code": "ExampleError"}
    )
    await repository.save(failed)

    retry = await repository.claim(_job("job-retry"))

    assert retry.acquired is True
    assert retry.job.job_id == JobId("job-retry")
