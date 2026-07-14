from datetime import UTC, datetime

import pytest
from ia_application import (
    DocumentIngestionWorker,
    IngestDocumentCommand,
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
from test_support import InMemoryIngestionJobRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


class FakeQueue:
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


class FakeIngestion:
    def __init__(self) -> None:
        self.commands: list[IngestDocumentCommand] = []
        self.raise_in_progress = False

    async def ingest(self, command: IngestDocumentCommand) -> IngestionJob:
        self.commands.append(command)
        if self.raise_in_progress:
            raise IngestionInProgressError("busy")
        return IngestionJob(
            tenant_id=command.tenant_id,
            job_id=command.job_id,
            document_id=command.document_id,
            source_version="source-a",
            status=IngestionStatus.SUCCEEDED,
            chunks_created=2,
            vectors_created=2,
            started_at=NOW,
            completed_at=NOW,
        )


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
