import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from ia_application import (
    DocumentIngestionWorker,
    IngestDocumentCommand,
    IngestionFailedError,
    IngestionInProgressError,
    IngestionTask,
    ReceivedIngestionTask,
)
from ia_domain import (
    DocumentId,
    IngestionJob,
    IngestionStatus,
    JobId,
    TenantId,
)
from test_support import (
    InMemoryDocumentIngestionLeaseRepository,
    InMemoryIngestionJobRepository,
)

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class FakeQueue:
    def __init__(self, received: ReceivedIngestionTask | None) -> None:
        self.received = received
        self.acknowledged: list[ReceivedIngestionTask] = []
        self.visibility_extensions: list[int] = []

    async def enqueue(self, task: IngestionTask) -> None:
        del task

    async def receive(self, *, wait_seconds: int) -> ReceivedIngestionTask | None:
        del wait_seconds
        return self.received

    async def acknowledge(self, received: ReceivedIngestionTask) -> None:
        self.acknowledged.append(received)

    async def extend_visibility(
        self,
        received: ReceivedIngestionTask,
        *,
        timeout_seconds: int,
    ) -> None:
        assert received == self.received
        self.visibility_extensions.append(timeout_seconds)

    async def is_ready(self) -> bool:
        return True


class FakeIngestion:
    def __init__(self) -> None:
        self.commands: list[IngestDocumentCommand] = []
        self.raise_in_progress = False
        self.sleep_seconds = 0.0
        self.result: IngestionJob | None = None

    async def ingest(self, command: IngestDocumentCommand) -> IngestionJob:
        self.commands.append(command)
        if self.raise_in_progress:
            raise IngestionInProgressError("busy")
        if self.sleep_seconds:
            await asyncio.sleep(self.sleep_seconds)
        if self.result is not None:
            return self.result
        return IngestionJob(
            tenant_id=command.tenant_id,
            job_id=command.job_id,
            document_id=command.document_id,
            source_version="source-a",
            status=IngestionStatus.SUCCEEDED,
            chunks_created=2,
            vectors_created=2,
            generation_id="generation-a",
            started_at=NOW,
            completed_at=NOW,
        )


class TransientFailureIngestion:
    def __init__(self, jobs: InMemoryIngestionJobRepository) -> None:
        self._jobs = jobs

    async def ingest(self, command: IngestDocumentCommand) -> IngestionJob:
        current = await self._jobs.get(command.tenant_id, command.job_id)
        assert current is not None
        await self._jobs.save(
            current.model_copy(
                update={
                    "status": IngestionStatus.FAILED,
                    "error_code": "INGESTION_FAILED",
                    "completed_at": NOW,
                }
            )
        )
        raise IngestionFailedError("temporary AWS failure")


def _pending() -> IngestionJob:
    return IngestionJob(
        tenant_id=TenantId("tenant-a"),
        job_id=JobId("job-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        status=IngestionStatus.PENDING,
        source_checksum="a" * 64,
        embedding_model_alias="server-profile",
        pipeline_version="pipeline-v1",
        started_at=NOW,
    )


def _received() -> ReceivedIngestionTask:
    return ReceivedIngestionTask(
        receipt_handle="receipt-a",
        task=IngestionTask(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            job_id=JobId("job-a"),
        ),
    )


@pytest.mark.asyncio
async def test_worker_executes_pending_job_with_server_owned_pipeline() -> None:
    jobs = InMemoryIngestionJobRepository()
    pending = _pending()
    await jobs.submit(pending)
    queue = FakeQueue(_received())
    ingestion = FakeIngestion()
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=ingestion,
        clock=lambda: NOW,
    )

    processed = await worker.run_once(wait_seconds=0)

    assert processed is True
    assert ingestion.commands == [
        IngestDocumentCommand(
            tenant_id=pending.tenant_id,
            document_id=pending.document_id,
            job_id=pending.job_id,
            embedding_model_alias="server-profile",
            pipeline_version="pipeline-v1",
            lease_ttl_seconds=900,
        )
    ]
    assert queue.acknowledged == [_received()]


@pytest.mark.asyncio
async def test_worker_does_not_acknowledge_busy_ingestion() -> None:
    jobs = InMemoryIngestionJobRepository()
    await jobs.submit(_pending())
    queue = FakeQueue(_received())
    ingestion = FakeIngestion()
    ingestion.raise_in_progress = True
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=ingestion,
        clock=lambda: NOW,
    )

    processed = await worker.run_once(wait_seconds=0)

    assert processed is False
    assert queue.acknowledged == []


@pytest.mark.asyncio
async def test_worker_acknowledges_already_terminal_job_without_reexecution() -> None:
    jobs = InMemoryIngestionJobRepository()
    terminal = _pending().model_copy(
        update={
            "status": IngestionStatus.SUCCEEDED,
            "completed_at": NOW,
        }
    )
    await jobs.save(terminal)
    queue = FakeQueue(_received())
    ingestion = FakeIngestion()
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=ingestion,
        clock=lambda: NOW,
    )

    processed = await worker.run_once(wait_seconds=0)

    assert processed is True
    assert ingestion.commands == []
    assert queue.acknowledged == [_received()]


@pytest.mark.asyncio
async def test_worker_reexecutes_running_job_after_redelivery() -> None:
    jobs = InMemoryIngestionJobRepository()
    running = _pending().model_copy(
        update={
            "status": IngestionStatus.RUNNING,
            "fingerprint": "f" * 64,
            "generation_id": "abandoned-generation",
            "fencing_token": 1,
        }
    )
    await jobs.save(running)
    queue = FakeQueue(_received())
    ingestion = FakeIngestion()
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=ingestion,
        clock=lambda: NOW,
    )

    processed = await worker.run_once(wait_seconds=0)

    assert processed is True
    assert ingestion.commands[0].job_id == running.job_id
    assert queue.acknowledged == [_received()]


@pytest.mark.asyncio
async def test_transient_ingestion_failure_remains_unacknowledged_for_retry() -> None:
    jobs = InMemoryIngestionJobRepository()
    await jobs.submit(_pending())
    queue = FakeQueue(_received())
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=TransientFailureIngestion(jobs),
        clock=lambda: NOW,
    )

    processed = await worker.run_once(wait_seconds=0)

    stored = await jobs.get(TenantId("tenant-a"), JobId("job-a"))
    assert processed is False
    assert stored is not None
    assert stored.status is IngestionStatus.FAILED
    assert stored.error_code == "INGESTION_FAILED"
    assert queue.acknowledged == []


@pytest.mark.asyncio
async def test_reused_canonical_result_preserves_generation_metadata() -> None:
    jobs = InMemoryIngestionJobRepository()
    pending = _pending()
    await jobs.submit(pending)
    queue = FakeQueue(_received())
    ingestion = FakeIngestion()
    ingestion.result = IngestionJob(
        tenant_id=pending.tenant_id,
        job_id=JobId("canonical-job"),
        document_id=pending.document_id,
        source_version=pending.source_version,
        status=IngestionStatus.SUCCEEDED,
        chunks_created=4,
        vectors_created=4,
        generation_id="canonical-generation",
        source_checksum="a" * 64,
        authorization_checksum="b" * 64,
        embedding_profile_revision="profile-v2",
        resolved_embedding_model_id="model-v2",
        embedding_dimensions=256,
        chunking_version="paragraph-v2",
        started_at=NOW,
        completed_at=NOW,
    )
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=ingestion,
        clock=lambda: NOW,
    )

    assert await worker.run_once(wait_seconds=0) is True

    alias = await jobs.get(pending.tenant_id, pending.job_id)
    assert alias is not None
    assert alias.status is IngestionStatus.SUCCEEDED
    assert alias.generation_id == "canonical-generation"
    assert alias.authorization_checksum == "b" * 64
    assert alias.resolved_embedding_model_id == "model-v2"


@pytest.mark.asyncio
async def test_worker_renews_lease_and_extends_visibility_for_long_ingestion() -> None:
    jobs = InMemoryIngestionJobRepository()
    pending = _pending()
    await jobs.submit(pending)
    leases = InMemoryDocumentIngestionLeaseRepository()
    await leases.acquire(
        tenant_id=pending.tenant_id,
        document_id=pending.document_id,
        source_version=pending.source_version,
        owner_token=str(pending.job_id),
        expires_at=NOW + timedelta(seconds=30),
        now=NOW,
    )
    queue = FakeQueue(_received())
    ingestion = FakeIngestion()
    ingestion.sleep_seconds = 1.1
    worker = DocumentIngestionWorker(
        jobs=jobs,
        leases=leases,
        queue=queue,
        ingestion=ingestion,
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=1,
        visibility_timeout_seconds=30,
        clock=lambda: NOW,
    )

    assert await worker.run_once(wait_seconds=0) is True
    assert queue.visibility_extensions == [30]
    assert queue.acknowledged == [_received()]


def test_worker_rejects_incoherent_heartbeat_timing() -> None:
    queue = FakeQueue(None)
    ingestion = FakeIngestion()
    jobs = InMemoryIngestionJobRepository()

    with pytest.raises(ValueError, match="visibility timeout"):
        DocumentIngestionWorker(
            jobs=jobs,
            queue=queue,
            ingestion=ingestion,
            lease_ttl_seconds=120,
            visibility_timeout_seconds=60,
        )
    with pytest.raises(ValueError, match="heartbeat interval"):
        DocumentIngestionWorker(
            jobs=jobs,
            queue=queue,
            ingestion=ingestion,
            lease_ttl_seconds=60,
            heartbeat_interval_seconds=60,
            visibility_timeout_seconds=120,
        )
