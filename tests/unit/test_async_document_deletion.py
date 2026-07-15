from datetime import timedelta

import pytest
from ia_application import (
    AsyncDocumentManagement,
    DeleteDocumentCommand,
    DocumentDeletionError,
    DocumentDeletionTask,
    DocumentDeletionWorker,
    DocumentPurgeService,
    ReceivedDocumentDeletionTask,
)
from ia_domain import DocumentId, DocumentStatus, TenantId

from tests.unit.test_document_management import NOW, _register, _service


class FakeDeletionQueue:
    def __init__(self) -> None:
        self.tasks: list[DocumentDeletionTask] = []
        self.received: ReceivedDocumentDeletionTask | None = None
        self.acknowledged: list[ReceivedDocumentDeletionTask] = []
        self.fail_enqueue = False

    async def enqueue(self, task: DocumentDeletionTask) -> None:
        if self.fail_enqueue:
            raise RuntimeError("queue unavailable")
        self.tasks.append(task)

    async def receive(
        self,
        *,
        wait_seconds: int,
    ) -> ReceivedDocumentDeletionTask | None:
        del wait_seconds
        return self.received

    async def acknowledge(self, received: ReceivedDocumentDeletionTask) -> None:
        self.acknowledged.append(received)

    async def is_ready(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_delete_dispatches_without_synchronous_storage_cleanup() -> None:
    synchronous, documents, _, leases, sources, _, chunks, vectors = _service()
    await synchronous.register(_register())
    queue = FakeDeletionQueue()
    management = AsyncDocumentManagement(
        delegate=synchronous,
        documents=documents,
        leases=leases,
        deletion_queue=queue,
        clock=lambda: NOW,
    )

    deleting = await management.delete_document(
        DeleteDocumentCommand(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            operation_id="delete-a",
        )
    )

    assert deleting.status is DocumentStatus.DELETING
    assert sources.deleted is False
    assert chunks.deleted is False
    assert vectors.deleted is False
    assert queue.tasks == [
        DocumentDeletionTask(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            operation_id="delete-a",
        )
    ]


@pytest.mark.asyncio
async def test_deletion_worker_purges_and_persists_tombstone() -> None:
    synchronous, documents, _, leases, sources, _, chunks, vectors = _service()
    await synchronous.register(_register())
    queue = FakeDeletionQueue()
    management = AsyncDocumentManagement(
        delegate=synchronous,
        documents=documents,
        leases=leases,
        deletion_queue=queue,
        clock=lambda: NOW,
    )
    deleting = await management.delete_document(
        DeleteDocumentCommand(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            operation_id="delete-a",
        )
    )
    received = ReceivedDocumentDeletionTask(
        receipt_handle="receipt-a",
        task=queue.tasks[0],
    )
    queue.received = received
    worker = DocumentDeletionWorker(
        queue=queue,
        purger=DocumentPurgeService(
            documents=documents,
            leases=leases,
            sources=sources,
            chunks=chunks,
            vectors=vectors,
            lease_ttl_seconds=300,
            clock=lambda: NOW + timedelta(seconds=1),
        ),
    )

    assert deleting.status is DocumentStatus.DELETING
    assert await worker.run_once(wait_seconds=0) is True

    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None and stored.status is DocumentStatus.DELETED
    assert sources.deleted is True
    assert chunks.deleted is True
    assert vectors.deleted is True
    assert queue.acknowledged == [received]


@pytest.mark.asyncio
async def test_dispatch_failure_leaves_recoverable_deleting_state() -> None:
    synchronous, documents, _, leases, _, _, _, _ = _service()
    await synchronous.register(_register())
    queue = FakeDeletionQueue()
    queue.fail_enqueue = True
    management = AsyncDocumentManagement(
        delegate=synchronous,
        documents=documents,
        leases=leases,
        deletion_queue=queue,
        clock=lambda: NOW,
    )

    with pytest.raises(DocumentDeletionError, match="dispatch"):
        await management.delete_document(
            DeleteDocumentCommand(
                tenant_id=TenantId("tenant-a"),
                document_id=DocumentId("document-a"),
                operation_id="delete-a",
            )
        )

    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None and stored.status is DocumentStatus.DELETING


@pytest.mark.asyncio
async def test_cleanup_failure_is_retried_without_acknowledgement() -> None:
    synchronous, documents, _, leases, sources, _, chunks, vectors = _service()
    await synchronous.register(_register())
    queue = FakeDeletionQueue()
    management = AsyncDocumentManagement(
        delegate=synchronous,
        documents=documents,
        leases=leases,
        deletion_queue=queue,
        clock=lambda: NOW,
    )
    await management.delete_document(
        DeleteDocumentCommand(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            operation_id="delete-a",
        )
    )
    received = ReceivedDocumentDeletionTask(
        receipt_handle="receipt-a",
        task=queue.tasks[0],
    )
    queue.received = received
    sources.fail_delete = True
    worker = DocumentDeletionWorker(
        queue=queue,
        purger=DocumentPurgeService(
            documents=documents,
            leases=leases,
            sources=sources,
            chunks=chunks,
            vectors=vectors,
            lease_ttl_seconds=300,
            clock=lambda: NOW + timedelta(seconds=1),
        ),
    )

    assert await worker.run_once(wait_seconds=0) is False
    assert queue.acknowledged == []
    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None and stored.status is DocumentStatus.DELETING
