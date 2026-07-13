import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Protocol, runtime_checkable

from ia_domain import (
    Classification,
    Document,
    DocumentId,
    DocumentStatus,
    IngestionJob,
    JobId,
    Role,
    TenantId,
    UserId,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ia_application.ingestion import DocumentIngestionService, IngestDocumentCommand
from ia_application.ports import (
    ChunkStore,
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
    """Raised when a document state or optimistic revision rejects an operation."""


class UnsupportedDocumentContentTypeError(DocumentManagementError):
    """Raised when the initial extraction boundary does not support a content type."""


class InvalidDocumentSourceError(DocumentManagementError):
    """Raised when an uploaded source does not match its registered metadata."""


class DocumentDeletionError(DocumentManagementError):
    """Raised when a document remains in DELETING after partial cleanup."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PresignedSourceUpload(StrictModel):
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
    embedding_model_alias: Annotated[str, Field(min_length=1, max_length=128)] = "default"
    pipeline_version: Annotated[str, Field(min_length=1, max_length=128)] = "ingestion-v1"


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
        size_bytes: int,
        expires_at: datetime,
    ) -> PresignedSourceUpload: ...

    async def inspect(self, document: Document) -> DocumentSourceMetadata: ...

    async def read(self, document: Document, *, max_bytes: int) -> bytes: ...

    async def delete_document(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> None: ...


@runtime_checkable
class DocumentManagement(Protocol):
    async def register(self, command: RegisterDocumentCommand) -> Document: ...

    async def create_upload(
        self, command: CreateSourceUploadCommand
    ) -> PresignedSourceUpload: ...

    async def get_document(
        self, tenant_id: TenantId, document_id: DocumentId
    ) -> Document: ...

    async def start_ingestion(
        self, command: StartDocumentIngestionCommand
    ) -> IngestionJob: ...

    async def get_job(
        self, tenant_id: TenantId, document_id: DocumentId, job_id: JobId
    ) -> IngestionJob: ...

    async def delete_document(
        self, tenant_id: TenantId, document_id: DocumentId
    ) -> Document: ...


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
        sources: DocumentSourceStore,
        ingestion: DocumentIngestionService,
        chunks: ChunkStore,
        vectors: VectorRepository,
        max_source_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_source_bytes <= 0:
            msg = "max_source_bytes must be positive"
            raise ValueError(msg)
        self._documents = documents
        self._jobs = jobs
        self._sources = sources
        self._ingestion = ingestion
        self._chunks = chunks
        self._vectors = vectors
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

    async def create_upload(
        self, command: CreateSourceUploadCommand
    ) -> PresignedSourceUpload:
        document = await self.get_document(command.tenant_id, command.document_id)
        if document.status is not DocumentStatus.PENDING_UPLOAD:
            raise DocumentStateConflictError("document state does not permit an upload")
        if command.size_bytes > self._max_source_bytes:
            raise InvalidDocumentSourceError("document source exceeds the configured size limit")
        if command.expires_at <= self._aware_now():
            raise InvalidDocumentSourceError("upload expiration must be in the future")
        return await self._sources.create_upload(
            document,
            size_bytes=command.size_bytes,
            expires_at=command.expires_at,
        )

    async def get_document(
        self, tenant_id: TenantId, document_id: DocumentId
    ) -> Document:
        document = await self._documents.get(tenant_id, document_id)
        if document is None or document.status is DocumentStatus.DELETED:
            raise ManagedDocumentNotFoundError("document was not found")
        return document

    async def start_ingestion(
        self, command: StartDocumentIngestionCommand
    ) -> IngestionJob:
        document = await self.get_document(command.tenant_id, command.document_id)
        if document.status in {DocumentStatus.DELETING, DocumentStatus.DELETED}:
            raise DocumentStateConflictError("document is being deleted")
        metadata = await self._sources.inspect(document)
        self._validate_source_metadata(document, metadata)
        if document.status is DocumentStatus.PENDING_UPLOAD:
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
                    "document changed before ingestion started"
                ) from error
        return await self._ingestion.ingest(
            IngestDocumentCommand(
                tenant_id=command.tenant_id,
                document_id=document.document_id,
                job_id=command.job_id,
                embedding_model_alias=command.embedding_model_alias,
                pipeline_version=command.pipeline_version,
            )
        )

    async def get_job(
        self, tenant_id: TenantId, document_id: DocumentId, job_id: JobId
    ) -> IngestionJob:
        job = await self._jobs.get(tenant_id, job_id)
        if job is None or job.document_id != document_id:
            raise ManagedDocumentNotFoundError("ingestion job was not found")
        return job

    async def delete_document(
        self, tenant_id: TenantId, document_id: DocumentId
    ) -> Document:
        document = await self.get_document(tenant_id, document_id)
        if document.status is DocumentStatus.DELETING:
            raise DocumentDeletionError("document deletion is incomplete and must be retried")
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
            self._sources.delete_document(tenant_id, document_id),
            self._chunks.delete_document(tenant_id, document_id),
            self._vectors.delete_document(tenant_id, document_id),
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
