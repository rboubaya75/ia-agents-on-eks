import asyncio
from datetime import datetime, timedelta

import pytest
from ia_application import (
    DocumentIngestionWorker,
    DocumentNotReadyError,
    IngestDocumentCommand,
    IngestionHeartbeatError,
    IngestionTask,
    InvalidExtractionError,
    ReceivedIngestionTask,
)
from ia_domain import DocumentId, IngestionJob, IngestionStatus, JobId, TenantId
from test_support import (
    InMemoryDocumentIngestionLeaseRepository,
    InMemoryIngestionJobRepository,
)

from tests.unit.test_document_ingestion_worker import (
    NOW,
    FakeIngestion,
    FakeQueue,
    _pending,
    _received,
)

ATTEMPT_ID = ":".join(("execution", "a"))


class RaisingIngestion:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def ingest(self, command: IngestDocumentCommand) -> IngestionJob:
        del command
        raise self._error


class CancellableIngestion:
    def __init__(self) -> None:
        self.cancelled = False

    async def ingest(self, command: IngestDocumentCommand) -> IngestionJob:
        del command
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class MinimalQueue:
    def __init__(self, received: ReceivedIngestionTask | None) -> None:
        self.received = received
        self.acknowledged: list[ReceivedIngestionTask] = []

    async def enqueue(self, task: IngestionTask) -> None:
        del task

    async def receive(self, *, wait_seconds: int) -> ReceivedIngestionTask | None:
        del wait_seconds
        return self.received

    async def acknowledge(self, received: ReceivedIngestionTask) -> None:
        self.acknowledged.append(received)

    async def is_ready(self) -> bool:
        return True


class SequencedLeaseRepository:
    def __init__(self, renewals: list[bool]) -> None:
        self._renewals = renewals

    async def renew(
        self,
        *,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
        owner_token: str,
        fencing_token: int,
        execution_token: str,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        del tenant_id, document_id, source_version, owner_token
        del fencing_token, execution_token, expires_at, now
        return self._renewals.pop(0)


@pytest.mark.asyncio
async def test_worker_returns_false_without_message() -> None:
    worker = DocumentIngestionWorker(
        jobs=InMemoryIngestionJobRepository(),
        queue=FakeQueue(None),
        ingestion=FakeIngestion(),
    )

    assert await worker.run_once(wait_seconds=0) is False


@pytest.mark.asyncio
async def test_worker_acknowledges_missing_or_mismatched_job() -> None:
    queue = FakeQueue(_received())
    worker = DocumentIngestionWorker(
        jobs=InMemoryIngestionJobRepository(),
        queue=queue,
        ingestion=FakeIngestion(),
    )

    assert await worker.run_once(wait_seconds=0) is True
    assert queue.acknowledged == [_received()]


@pytest.mark.asyncio
async def test_worker_acknowledges_non_retryable_failed_job() -> None:
    jobs = InMemoryIngestionJobRepository()
    failed = _pending().model_copy(
        update={
            "status": IngestionStatus.FAILED,
            "error_code": "INVALID_EXTRACTION",
            "completed_at": NOW,
        }
    )
    await jobs.save(failed)
    queue = FakeQueue(_received())

    assert (
        await DocumentIngestionWorker(
            jobs=jobs,
            queue=queue,
            ingestion=FakeIngestion(),
        ).run_once(wait_seconds=0)
        is True
    )
    assert queue.acknowledged == [_received()]


@pytest.mark.asyncio
async def test_worker_terminalizes_invalid_pending_metadata() -> None:
    jobs = InMemoryIngestionJobRepository()
    invalid = _pending().model_copy(update={"embedding_model_alias": None})
    await jobs.submit(invalid)
    queue = FakeQueue(_received())

    assert (
        await DocumentIngestionWorker(
            jobs=jobs,
            queue=queue,
            ingestion=FakeIngestion(),
            clock=lambda: NOW,
        ).run_once(wait_seconds=0)
        is True
    )
    stored = await jobs.get(invalid.tenant_id, invalid.job_id)
    assert stored is not None and stored.error_code == "INVALID_PENDING_JOB"
    assert queue.acknowledged == [_received()]


@pytest.mark.asyncio
async def test_worker_acknowledges_document_not_ingestable() -> None:
    jobs = InMemoryIngestionJobRepository()
    await jobs.submit(_pending())
    queue = FakeQueue(_received())
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=RaisingIngestion(DocumentNotReadyError("not ready")),
        clock=lambda: NOW,
    )

    assert await worker.run_once(wait_seconds=0) is True
    stored = await jobs.get(TenantId("tenant-a"), JobId("job-a"))
    assert stored is not None and stored.error_code == "DOCUMENT_NOT_INGESTABLE"


@pytest.mark.asyncio
async def test_worker_acknowledges_deterministic_ingestion_error() -> None:
    jobs = InMemoryIngestionJobRepository()
    await jobs.submit(_pending())
    queue = FakeQueue(_received())
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=RaisingIngestion(InvalidExtractionError("invalid content")),
        clock=lambda: NOW,
    )

    assert await worker.run_once(wait_seconds=0) is True
    assert queue.acknowledged == [_received()]


@pytest.mark.asyncio
async def test_worker_surfaces_unexpected_error_without_acknowledging() -> None:
    jobs = InMemoryIngestionJobRepository()
    await jobs.submit(_pending())
    queue = FakeQueue(_received())
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=RaisingIngestion(RuntimeError("infrastructure unavailable")),
    )

    with pytest.raises(RuntimeError, match="infrastructure unavailable"):
        await worker.run_once(wait_seconds=0)
    assert queue.acknowledged == []


@pytest.mark.asyncio
async def test_worker_fails_closed_when_heartbeat_dependencies_are_missing() -> None:
    jobs = InMemoryIngestionJobRepository()
    await jobs.submit(_pending())
    ingestion = FakeIngestion()
    ingestion.sleep_seconds = 1.1
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=MinimalQueue(_received()),
        ingestion=ingestion,
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=1,
        visibility_timeout_seconds=30,
    )

    with pytest.raises(IngestionHeartbeatError, match="lease repository"):
        await worker.run_once(wait_seconds=0)


@pytest.mark.asyncio
async def test_worker_cancels_ingestion_after_confirmed_lease_loss() -> None:
    jobs = InMemoryIngestionJobRepository()
    running = _pending().model_copy(
        update={
            "status": IngestionStatus.RUNNING,
            "fingerprint": "f" * 64,
            "generation_id": "generation-a",
            "fencing_token": 7,
        }
    )
    await jobs.save(running)
    queue = FakeQueue(_received())
    ingestion = CancellableIngestion()
    worker = DocumentIngestionWorker(
        jobs=jobs,
        leases=SequencedLeaseRepository([True, False]),
        queue=queue,
        ingestion=ingestion,
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=1,
        visibility_timeout_seconds=30,
        execution_token_factory=lambda: ATTEMPT_ID,
    )

    with pytest.raises(IngestionHeartbeatError, match="lease was lost"):
        await worker.run_once(wait_seconds=0)

    assert ingestion.cancelled is True
    assert queue.visibility_extensions == [30]
    assert queue.acknowledged == []


@pytest.mark.asyncio
async def test_worker_requires_visibility_extension_after_lease_resolution() -> None:
    jobs = InMemoryIngestionJobRepository()
    pending = _pending()
    await jobs.submit(pending)
    leases = InMemoryDocumentIngestionLeaseRepository()
    await leases.acquire(
        tenant_id=pending.tenant_id,
        document_id=pending.document_id,
        source_version=pending.source_version,
        owner_token=str(pending.job_id),
        execution_token=ATTEMPT_ID,
        expires_at=NOW + timedelta(seconds=30),
        now=NOW,
    )
    ingestion = FakeIngestion()
    ingestion.sleep_seconds = 1.1
    worker = DocumentIngestionWorker(
        jobs=jobs,
        leases=leases,
        queue=MinimalQueue(_received()),
        ingestion=ingestion,
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=1,
        visibility_timeout_seconds=30,
        clock=lambda: NOW,
        execution_token_factory=lambda: ATTEMPT_ID,
    )

    with pytest.raises(IngestionHeartbeatError, match="extend visibility"):
        await worker.run_once(wait_seconds=0)
