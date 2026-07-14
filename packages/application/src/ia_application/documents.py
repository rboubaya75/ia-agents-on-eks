import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol, runtime_checkable

from ia_domain import (
    Classification,
    Document,
    DocumentId,
    DocumentStatus,
    IngestionJob,
    IngestionStatus,
    JobId,
    Role,
    TenantId,
    UserId,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ia_application.ingestion import (
    DocumentNotFoundError,
    DocumentNotReadyError,
    IngestDocumentCommand,
    IngestionError,
    IngestionInProgressError,
)
from ia_application.ports import (
    ChunkStore,
    DocumentIngestionLeaseRepository,
    DocumentRepository,
    ExtractedDocument,
    ExtractedSection,
    IngestionJobRepository,
    RepositoryConflictError,
    TextExtractor,
    VectorRepository,
)

_SUPPORTED_CONTENT_TYPES = frozenset({"text/plain", "text/markdown"})


class DocumentManagementError(RuntimeError):
    """Base class for expected document-management failures."""


class ManagedDocumentNotFoundError(DocumentManagementError):
    """Raised when a document or ingestion job is not visible to the trusted tenant."""


class DocumentStateConflictError(DocumentManagementError):
    """Raised when a document state, lease or optimistic revision rejects an operation."""


class UnsupportedDocumentContentTypeError(DocumentManagementError):
    """Raised when the initial extraction boundary does not support a content type."""


class InvalidDocumentSourceError(DocumentManagementError):
    """Raised when an uploaded source does not match its registered metadata."""


class DocumentDeletionError(DocumentManagementError):
    """Raised when a document remains in DELETING after partial cleanup."""


class IngestionDispatchError(DocumentManagementError):
    """Raised when a persisted ingestion job cannot be dispatched."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DocumentPipelineSettings(StrictModel):
    embedding_model_alias: Annotated[str, Field(min_length=1, max_length=128)]
    pipeline_version: Annotated[str, Field(min_length=1, max_length=128)]
    submission_lease_ttl_seconds: Annotated[int, Field(ge=30, le=300)] = 60
    deletion_lease_ttl_seconds: Annotated[int, Field(ge=30, le=3600)] = 300


class PresignedSourceUpload(StrictModel):
    upload_session_id: Annotated[str, Field(min_length=1, max_length=128)]
    url: Annotated[str, Field(min_length=1, max_length=8192)]
    method: str = "PUT"
    headers: dict[str, str]
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "expires_at must include a timezone"
            raise ValueError(msg)
        return value


class DocumentSourceMetadata(StrictModel):
    content_type: Annotated[str, Field(min_length=1, max_length=255)]
    size_bytes: Annotated[int, Field(ge=1)]
    checksum_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class IngestionTask(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    job_id: Annotated[JobId, Field(min_length=1, max_length=128)]


class ReceivedIngestionTask(StrictModel):
    receipt_handle: Annotated[str, Field(min_length=1, max_length=4096)]
    task: IngestionTask


class RegisterDocumentCommand(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    owner_user_id: Annotated[UserId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    source_checksum: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    content_type: Annotated[str, Field(min_length=1, max_length=255)]
    language: Annotated[str, Field(min_length=2, max_length=35)]
    classification: Classification
    allowed_roles: Annotated[frozenset[Role], Field(min_length=1)]


class CreateSourceUploadCommand(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    upload_session_id: Annotated[str, Field(min_length=1, max_length=128)]
    size_bytes: Annotated[int, Field(ge=1)]
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "expires_at must include a timezone"
            raise ValueError(msg)
        return value


class StartDocumentIngestionCommand(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    job_id: Annotated[JobId, Field(min_length=1, max_length=128)]
    upload_session_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class DeleteDocumentCommand(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    operation_id: Annotated[str, Field(min_length=1, max_length=128)]


@runtime_checkable
class DocumentSourceStore(Protocol):
    def source_uri(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> str: ...

    async def create_upload(
        self,
        document: Document,
        *,
        upload_session_id: str,
        size_bytes: int,
        expires_at: datetime,
    ) -> PresignedSourceUpload: ...

    async def promote_upload(
        self,
        document: Document,
        upload_session_id: str,
    ) -> DocumentSourceMetadata: ...

    async def inspect(self, document: Document) -> DocumentSourceMetadata: ...

    async def read(self, document: Document, *, max_bytes: int) -> bytes: ...

    async def delete_document(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> None: ...

    async def is_ready(self) -> bool: ...


@runtime_checkable
class IngestionTaskQueue(Protocol):
    async def enqueue(self, task: IngestionTask) -> None: ...

    async def receive(self, *, wait_seconds: int) -> ReceivedIngestionTask | None: ...

    async def acknowledge(self, received: ReceivedIngestionTask) -> None: ...

    async def is_ready(self) -> bool: ...


@runtime_checkable
class DocumentIngestion(Protocol):
    async def ingest(self, command: IngestDocumentCommand) -> IngestionJob: ...


@runtime_checkable
class DocumentManagement(Protocol):
    async def register(self, command: RegisterDocumentCommand) -> Document: ...

    async def create_upload(self, command: CreateSourceUploadCommand) -> PresignedSourceUpload: ...

    async def get_document(self, tenant_id: TenantId, document_id: DocumentId) -> Document: ...

    async def submit_ingestion(self, command: StartDocumentIngestionCommand) -> IngestionJob: ...

    async def get_job(
        self, tenant_id: TenantId, document_id: DocumentId, job_id: JobId
    ) -> IngestionJob: ...

    async def delete_document(self, command: DeleteDocumentCommand) -> Document: ...


class Utf8DocumentExtractor(TextExtractor):
    def __init__(self, sources: DocumentSourceStore, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            msg = "max_bytes must be positive"
            raise ValueError(msg)
        self._sources = sources
        self._max_bytes = max_bytes

    async def extract(self, document: Document) -> ExtractedDocument:
        if document.content_type not in _SUPPORTED_CONTENT_TYPES:
            raise UnsupportedDocumentContentTypeError(
                f"unsupported document content type: {document.content_type}"
            )
        payload = await self._sources.read(document, max_bytes=self._max_bytes)
        if not payload or len(payload) > self._max_bytes:
            raise InvalidDocumentSourceError("document source size is invalid")
        checksum = hashlib.sha256(payload).hexdigest()
        if checksum != document.source_checksum:
            raise InvalidDocumentSourceError("document source checksum does not match metadata")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise InvalidDocumentSourceError("document source is not valid UTF-8") from error
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if any(
            (ord(character) < 32 and character not in {"\n", "\t", "\f"})
            or ord(character) == 127
            for character in normalized
        ):
            raise InvalidDocumentSourceError(
                "document source contains unsupported control characters"
            )
        normalized = normalized.strip()
        if not normalized:
            raise InvalidDocumentSourceError("document source contains no indexable text")
        return ExtractedDocument(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            source_version=document.source_version,
            sections=(ExtractedSection(title=document.title, content=normalized),),
        )


class DocumentManagementService(DocumentManagement):
    def __init__(
        self,
        *,
        documents: DocumentRepository,
        jobs: IngestionJobRepository,
        leases: DocumentIngestionLeaseRepository,
        sources: DocumentSourceStore,
        queue: IngestionTaskQueue,
        chunks: ChunkStore,
        vectors: VectorRepository,
        pipeline: DocumentPipelineSettings,
        max_source_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_source_bytes <= 0:
            msg = "max_source_bytes must be positive"
            raise ValueError(msg)
        self._documents = documents
        self._jobs = jobs
        self._leases = leases
        self._sources = sources
        self._queue = queue
        self._chunks = chunks
        self._vectors = vectors
        self._pipeline = pipeline
        self._max_source_bytes = max_source_bytes
        self._clock = clock

    async def register(self, command: RegisterDocumentCommand) -> Document:
        if command.content_type not in _SUPPORTED_CONTENT_TYPES:
            raise UnsupportedDocumentContentTypeError(
                f"unsupported document content type: {command.content_type}"
            )
        now = self._aware_now()
        source_version = command.source_checksum
        document = Document(
            tenant_id=command.tenant_id,
            document_id=command.document_id,
            owner_user_id=command.owner_user_id,
            title=command.title,
            source_uri=self._sources.source_uri(
                command.tenant_id,
                command.document_id,
                source_version,
            ),
            source_version=source_version,
            source_checksum=command.source_checksum,
            content_type=command.content_type,
            language=command.language,
            classification=command.classification,
            allowed_roles=command.allowed_roles,
            status=DocumentStatus.PENDING_UPLOAD,
            created_at=now,
            updated_at=now,
        )
        try:
            return await self._documents.save(document)
        except RepositoryConflictError as error:
            raise DocumentStateConflictError("document already exists") from error

    async def create_upload(self, command: CreateSourceUploadCommand) -> PresignedSourceUpload:
        document = await self.get_document(command.tenant_id, command.document_id)
        if document.status is not DocumentStatus.PENDING_UPLOAD:
            raise DocumentStateConflictError("document state does not permit an upload")
        if command.size_bytes > self._max_source_bytes:
            raise InvalidDocumentSourceError("document source exceeds the configured size limit")
        if command.expires_at <= self._aware_now():
            raise InvalidDocumentSourceError("upload expiration must be in the future")
        return await self._sources.create_upload(
            document,
            upload_session_id=command.upload_session_id,
            size_bytes=command.size_bytes,
            expires_at=command.expires_at,
        )

    async def get_document(self, tenant_id: TenantId, document_id: DocumentId) -> Document:
        document = await self._documents.get(tenant_id, document_id)
        if document is None or document.status is DocumentStatus.DELETED:
            raise ManagedDocumentNotFoundError("document was not found")
        return document

    async def submit_ingestion(self, command: StartDocumentIngestionCommand) -> IngestionJob:
        document = await self.get_document(command.tenant_id, command.document_id)
        if document.status in {DocumentStatus.DELETING, DocumentStatus.DELETED}:
            raise DocumentStateConflictError("document is being deleted")
        now = self._aware_now()
        claim = await self._leases.acquire(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            source_version=document.source_version,
            owner_token=f"submit:{command.job_id}",
            expires_at=now
            + timedelta(seconds=self._pipeline.submission_lease_ttl_seconds),
            now=now,
        )
        if not claim.acquired:
            raise DocumentStateConflictError("document version is busy")
        try:
            if document.status is DocumentStatus.PENDING_UPLOAD:
                if command.upload_session_id is None:
                    raise InvalidDocumentSourceError("an upload session is required")
                metadata = await self._sources.promote_upload(
                    document,
                    command.upload_session_id,
                )
                self._validate_source_metadata(document, metadata)
                try:
                    document = await self._documents.save(
                        document.model_copy(
                            update={
                                "status": DocumentStatus.UPLOADED,
                                "updated_at": self._aware_now(),
                            }
                        ),
                        expected_revision=document.revision,
                    )
                except RepositoryConflictError as error:
                    raise DocumentStateConflictError(
                        "document changed before ingestion submission"
                    ) from error
            elif command.upload_session_id is not None:
                raise DocumentStateConflictError(
                    "an upload session cannot replace an immutable source"
                )
            else:
                self._validate_source_metadata(
                    document,
                    await self._sources.inspect(document),
                )

            pending = IngestionJob(
                tenant_id=document.tenant_id,
                job_id=command.job_id,
                document_id=document.document_id,
                source_version=document.source_version,
                status=IngestionStatus.PENDING,
                source_checksum=document.source_checksum,
                embedding_model_alias=self._pipeline.embedding_model_alias,
                pipeline_version=self._pipeline.pipeline_version,
                started_at=now,
            )
            submission = await self._jobs.submit(pending)
            if submission.job.status is IngestionStatus.PENDING:
                try:
                    await self._queue.enqueue(
                        IngestionTask(
                            tenant_id=document.tenant_id,
                            document_id=document.document_id,
                            job_id=submission.job.job_id,
                        )
                    )
                except Exception as error:
                    raise IngestionDispatchError(
                        "ingestion job was persisted but dispatch failed"
                    ) from error
            return submission.job
        finally:
            await self._leases.release(claim.lease)

    async def get_job(
        self, tenant_id: TenantId, document_id: DocumentId, job_id: JobId
    ) -> IngestionJob:
        job = await self._jobs.get(tenant_id, job_id)
        if job is None or job.document_id != document_id:
            raise ManagedDocumentNotFoundError("ingestion job was not found")
        return job

    async def delete_document(self, command: DeleteDocumentCommand) -> Document:
        document = await self.get_document(command.tenant_id, command.document_id)
        now = self._aware_now()
        claim = await self._leases.acquire(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            source_version=document.source_version,
            owner_token=f"delete:{command.operation_id}",
            expires_at=now + timedelta(seconds=self._pipeline.deletion_lease_ttl_seconds),
            now=now,
        )
        if not claim.acquired:
            raise DocumentStateConflictError("document version is busy")
        try:
            if document.status is DocumentStatus.DELETING:
                deleting = document
            else:
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
                        "document changed before deletion started"
                    ) from error
            results = await asyncio.gather(
                self._sources.delete_document(document.tenant_id, document.document_id),
                self._chunks.delete_document(document.tenant_id, document.document_id),
                self._vectors.delete_document(document.tenant_id, document.document_id),
                return_exceptions=True,
            )
            if any(isinstance(result, BaseException) for result in results):
                raise DocumentDeletionError(
                    "document cleanup failed; the document remains in deleting state"
                )
            try:
                return await self._documents.save(
                    deleting.model_copy(
                        update={
                            "status": DocumentStatus.DELETED,
                            "updated_at": self._aware_now(),
                        }
                    ),
                    expected_revision=deleting.revision,
                )
            except RepositoryConflictError as error:
                raise DocumentDeletionError(
                    "document cleanup completed but tombstone persistence failed"
                ) from error
        finally:
            await self._leases.release(claim.lease)

    def _validate_source_metadata(
        self, document: Document, metadata: DocumentSourceMetadata
    ) -> None:
        if metadata.content_type != document.content_type:
            raise InvalidDocumentSourceError("uploaded content type does not match metadata")
        if metadata.size_bytes > self._max_source_bytes:
            raise InvalidDocumentSourceError("uploaded source exceeds the configured size limit")
        if metadata.checksum_sha256 != document.source_checksum:
            raise InvalidDocumentSourceError("uploaded checksum does not match metadata")

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value


class DocumentIngestionWorker:
    def __init__(
        self,
        *,
        jobs: IngestionJobRepository,
        queue: IngestionTaskQueue,
        ingestion: DocumentIngestion,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._jobs = jobs
        self._queue = queue
        self._ingestion = ingestion
        self._clock = clock

    async def run_once(self, *, wait_seconds: int = 20) -> bool:
        received = await self._queue.receive(wait_seconds=wait_seconds)
        if received is None:
            return False
        task = received.task
        job = await self._jobs.get(task.tenant_id, task.job_id)
        if job is None or job.document_id != task.document_id:
            await self._queue.acknowledge(received)
            return True
        if job.status in {IngestionStatus.SUCCEEDED, IngestionStatus.FAILED}:
            await self._queue.acknowledge(received)
            return True
        if job.status is not IngestionStatus.PENDING:
            return False
        if job.embedding_model_alias is None or job.pipeline_version is None:
            await self._fail_pending(job, "INVALID_PENDING_JOB")
            await self._queue.acknowledge(received)
            return True
        try:
            result = await self._ingestion.ingest(
                IngestDocumentCommand(
                    tenant_id=job.tenant_id,
                    document_id=job.document_id,
                    job_id=job.job_id,
                    embedding_model_alias=job.embedding_model_alias,
                    pipeline_version=job.pipeline_version,
                )
            )
        except IngestionInProgressError:
            return False
        except (DocumentNotFoundError, DocumentNotReadyError):
            await self._fail_pending(job, "DOCUMENT_NOT_INGESTABLE")
            await self._queue.acknowledge(received)
            return True
        except IngestionError:
            current = await self._jobs.get(job.tenant_id, job.job_id)
            if current is None or current.status is IngestionStatus.PENDING:
                await self._fail_pending(job, "INGESTION_FAILED")
            await self._queue.acknowledge(received)
            return True
        except Exception:
            return False

        if result.job_id != job.job_id:
            await self._jobs.save(
                job.model_copy(
                    update={
                        "status": result.status,
                        "chunks_created": result.chunks_created,
                        "vectors_created": result.vectors_created,
                        "error_code": result.error_code,
                        "embedding_profile_revision": result.embedding_profile_revision,
                        "resolved_embedding_model_id": result.resolved_embedding_model_id,
                        "embedding_dimensions": result.embedding_dimensions,
                        "chunking_version": result.chunking_version,
                        "completed_at": result.completed_at or self._aware_now(),
                    }
                )
            )
        await self._queue.acknowledge(received)
        return True

    async def _fail_pending(self, job: IngestionJob, error_code: str) -> None:
        await self._jobs.save(
            job.model_copy(
                update={
                    "status": IngestionStatus.FAILED,
                    "error_code": error_code,
                    "completed_at": self._aware_now(),
                }
            )
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value
