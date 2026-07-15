import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol, runtime_checkable

from ia_domain import Document, DocumentId, DocumentStatus, IngestionJob, JobId, TenantId
from pydantic import BaseModel, ConfigDict, Field

from ia_application.documents import (
    CreateSourceUploadCommand,
    DeleteDocumentCommand,
    DocumentDeletionError,
    DocumentManagement,
    DocumentStateConflictError,
    PresignedSourceUpload,
    RegisterDocumentCommand,
    StartDocumentIngestionCommand,
)
from ia_application.ports import (
    ChunkStore,
    DocumentIngestionLeaseRepository,
    DocumentRepository,
    RepositoryConflictError,
    VectorRepository,
)


class StrictDeletionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DocumentDeletionTask(StrictDeletionModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    operation_id: Annotated[str, Field(min_length=1, max_length=128)]


class ReceivedDocumentDeletionTask(StrictDeletionModel):
    receipt_handle: Annotated[str, Field(min_length=1, max_length=4096)]
    task: DocumentDeletionTask


@runtime_checkable
class DocumentDeletionTaskQueue(Protocol):
    async def enqueue(self, task: DocumentDeletionTask) -> None: ...

    async def receive(
        self,
        *,
        wait_seconds: int,
    ) -> ReceivedDocumentDeletionTask | None: ...

    async def acknowledge(self, received: ReceivedDocumentDeletionTask) -> None: ...

    async def is_ready(self) -> bool: ...


class AsyncDocumentManagement(DocumentManagement):
    """Document management facade that dispatches destructive cleanup asynchronously."""

    def __init__(
        self,
        *,
        delegate: DocumentManagement,
        documents: DocumentRepository,
        leases: DocumentIngestionLeaseRepository,
        deletion_queue: DocumentDeletionTaskQueue,
        submission_lease_ttl_seconds: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if submission_lease_ttl_seconds < 30 or submission_lease_ttl_seconds > 300:
            msg = "deletion submission lease TTL must be between 30 and 300 seconds"
            raise ValueError(msg)
        self._delegate = delegate
        self._documents = documents
        self._leases = leases
        self._deletion_queue = deletion_queue
        self._submission_lease_ttl_seconds = submission_lease_ttl_seconds
        self._clock = clock

    async def register(self, command: RegisterDocumentCommand) -> Document:
        return await self._delegate.register(command)

    async def create_upload(self, command: CreateSourceUploadCommand) -> PresignedSourceUpload:
        return await self._delegate.create_upload(command)

    async def get_document(self, tenant_id: TenantId, document_id: DocumentId) -> Document:
        return await self._delegate.get_document(tenant_id, document_id)

    async def submit_ingestion(self, command: StartDocumentIngestionCommand) -> IngestionJob:
        return await self._delegate.submit_ingestion(command)

    async def get_job(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        job_id: JobId,
    ) -> IngestionJob:
        return await self._delegate.get_job(tenant_id, document_id, job_id)

    async def delete_document(self, command: DeleteDocumentCommand) -> Document:
        document = await self._delegate.get_document(command.tenant_id, command.document_id)
        now = self._aware_now()
        claim = await self._leases.acquire(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            source_version=document.source_version,
            owner_token=f"delete-submit:{command.operation_id}",
            expires_at=now + timedelta(seconds=self._submission_lease_ttl_seconds),
            now=now,
        )
        if not claim.acquired:
            if document.status is DocumentStatus.DELETING:
                return document
            raise DocumentStateConflictError("document version is busy")
        try:
            deleting = document
            if document.status is not DocumentStatus.DELETING:
                try:
                    deleting = await self._documents.save(
                        document.model_copy(
                            update={
                                "status": DocumentStatus.DELETING,
                                "updated_at": self._aware_now(),
                            }
                        ),
                        expected_revision=document.revision,
                    )
                except RepositoryConflictError as error:
                    raise DocumentStateConflictError(
                        "document changed before deletion dispatch"
                    ) from error
            try:
                await self._deletion_queue.enqueue(
                    DocumentDeletionTask(
                        tenant_id=deleting.tenant_id,
                        document_id=deleting.document_id,
                        operation_id=command.operation_id,
                    )
                )
            except Exception as error:
                raise DocumentDeletionError(
                    "document deletion was persisted but dispatch failed"
                ) from error
            return deleting
        finally:
            await self._leases.release(claim.lease)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value


class DocumentPurgeService:
    """Worker-side purge service with the only destructive storage permissions."""

    def __init__(
        self,
        *,
        documents: DocumentRepository,
        leases: DocumentIngestionLeaseRepository,
        sources: object,
        chunks: ChunkStore,
        vectors: VectorRepository,
        lease_ttl_seconds: int = 900,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if lease_ttl_seconds < 30 or lease_ttl_seconds > 3600:
            msg = "deletion lease TTL must be between 30 and 3600 seconds"
            raise ValueError(msg)
        if not hasattr(sources, "delete_document"):
            msg = "document source store must support document deletion"
            raise TypeError(msg)
        self._documents = documents
        self._leases = leases
        self._sources = sources
        self._chunks = chunks
        self._vectors = vectors
        self._lease_ttl_seconds = lease_ttl_seconds
        self._clock = clock

    async def purge(self, command: DeleteDocumentCommand) -> Document | None:
        document = await self._documents.get(command.tenant_id, command.document_id)
        if document is None or document.status is DocumentStatus.DELETED:
            return document
        if document.status is not DocumentStatus.DELETING:
            raise DocumentStateConflictError("document is not pending deletion")

        now = self._aware_now()
        claim = await self._leases.acquire(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            source_version=document.source_version,
            owner_token=f"delete:{command.operation_id}",
            expires_at=now + timedelta(seconds=self._lease_ttl_seconds),
            now=now,
        )
        if not claim.acquired:
            raise DocumentStateConflictError("document version is busy")
        try:
            source_delete = getattr(self._sources, "delete_document")
            results = await asyncio.gather(
                source_delete(document.tenant_id, document.document_id),
                self._chunks.delete_document(document.tenant_id, document.document_id),
                self._vectors.delete_document(document.tenant_id, document.document_id),
                return_exceptions=True,
            )
            if any(isinstance(result, BaseException) for result in results):
                raise DocumentDeletionError(
                    "document cleanup failed; the document remains in deleting state"
                )
            current = await self._documents.get(document.tenant_id, document.document_id)
            if current is None or current.status is DocumentStatus.DELETED:
                return current
            try:
                return await self._documents.save(
                    current.model_copy(
                        update={
                            "status": DocumentStatus.DELETED,
                            "updated_at": self._aware_now(),
                        }
                    ),
                    expected_revision=current.revision,
                )
            except RepositoryConflictError as error:
                raise DocumentDeletionError(
                    "document cleanup completed but tombstone persistence failed"
                ) from error
        finally:
            await self._leases.release(claim.lease)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value


class DocumentDeletionWorker:
    def __init__(
        self,
        *,
        queue: DocumentDeletionTaskQueue,
        purger: DocumentPurgeService,
    ) -> None:
        self._queue = queue
        self._purger = purger

    async def run_once(self, *, wait_seconds: int = 20) -> bool:
        received = await self._queue.receive(wait_seconds=wait_seconds)
        if received is None:
            return False
        try:
            await self._purger.purge(
                DeleteDocumentCommand(
                    tenant_id=received.task.tenant_id,
                    document_id=received.task.document_id,
                    operation_id=received.task.operation_id,
                )
            )
        except (DocumentStateConflictError, DocumentDeletionError):
            return False
        except Exception:
            return False
        await self._queue.acknowledge(received)
        return True
