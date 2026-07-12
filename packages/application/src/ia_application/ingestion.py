import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from ia_domain import (
    Document,
    DocumentChunk,
    DocumentId,
    DocumentStatus,
    IngestionJob,
    IngestionStatus,
    JobId,
    TenantId,
)
from pydantic import BaseModel, ConfigDict, Field

from ia_application.chunking import ParagraphChunker
from ia_application.ports import (
    ChunkStore,
    DocumentRepository,
    EmbeddingProvider,
    EmbeddingRequest,
    ExtractedDocument,
    IngestionJobRepository,
    TextExtractor,
    VectorRecord,
    VectorRepository,
)


class IngestionError(RuntimeError):
    """Base class for expected ingestion failures."""


class DocumentNotFoundError(IngestionError):
    """Raised when the trusted tenant cannot access the requested document."""


class DocumentNotReadyError(IngestionError):
    """Raised when document state does not permit ingestion."""


class IngestionInProgressError(IngestionError):
    """Raised when an identical pipeline execution is already running."""


class InvalidExtractionError(IngestionError):
    """Raised when extraction output is empty or belongs to another document."""


class InvalidEmbeddingResponseError(IngestionError):
    """Raised when an embedding provider returns an inconsistent response."""


class IngestionFailedError(IngestionError):
    """Raised after failed ingestion has been cleaned up and recorded."""


class IngestDocumentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    document_id: Annotated[DocumentId, Field(min_length=1, max_length=128)]
    job_id: Annotated[JobId, Field(min_length=1, max_length=128)]
    embedding_model_alias: Annotated[str, Field(min_length=1, max_length=128)]
    pipeline_version: Annotated[str, Field(min_length=1, max_length=128)] = "ingestion-v1"
    embedding_batch_size: Annotated[int, Field(ge=1, le=256)] = 32


class DocumentIngestionService:
    def __init__(
        self,
        *,
        documents: DocumentRepository,
        jobs: IngestionJobRepository,
        extractor: TextExtractor,
        chunker: ParagraphChunker,
        embeddings: EmbeddingProvider,
        chunks: ChunkStore,
        vectors: VectorRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._documents = documents
        self._jobs = jobs
        self._extractor = extractor
        self._chunker = chunker
        self._embeddings = embeddings
        self._chunks = chunks
        self._vectors = vectors
        self._clock = clock

    async def ingest(self, command: IngestDocumentCommand) -> IngestionJob:
        document = await self._documents.get(command.tenant_id, command.document_id)
        if document is None:
            raise DocumentNotFoundError("document was not found")
        fingerprint = self._fingerprint(document, command)
        existing = await self._jobs.find_by_fingerprint(command.tenant_id, fingerprint)
        if existing is not None:
            if existing.status is IngestionStatus.SUCCEEDED:
                return existing
            if existing.status is IngestionStatus.RUNNING:
                raise IngestionInProgressError("identical ingestion is already running")

        self._ensure_ingestable(document)
        started_at = self._aware_now()
        running = IngestionJob(
            tenant_id=document.tenant_id,
            job_id=command.job_id,
            document_id=document.document_id,
            source_version=document.source_version,
            status=IngestionStatus.RUNNING,
            fingerprint=fingerprint,
            source_checksum=document.source_checksum,
            embedding_model_alias=command.embedding_model_alias,
            chunking_version=self._chunker.version,
            pipeline_version=command.pipeline_version,
            started_at=started_at,
        )
        claim = await self._jobs.claim(running)
        if not claim.acquired:
            if claim.job.status is IngestionStatus.SUCCEEDED:
                return claim.job
            raise IngestionInProgressError("identical ingestion is already running")

        replacement_started = False
        try:
            await self._documents.save(
                document.model_copy(
                    update={"status": DocumentStatus.PROCESSING, "updated_at": started_at}
                )
            )
            extracted = await self._extractor.extract(document)
            self._validate_extraction_identity(document, extracted)
            document_chunks = self._chunker.chunk(
                document,
                extracted,
                created_at=started_at,
            )
            if not document_chunks:
                raise InvalidExtractionError("extraction produced no indexable content")

            records = await self._embed_chunks(document_chunks, command)
            replacement_started = True
            await self._cleanup_version(document)
            for chunk in document_chunks:
                await self._chunks.put(chunk)
            await self._vectors.upsert(records)

            completed_at = self._aware_now()
            succeeded = running.model_copy(
                update={
                    "status": IngestionStatus.SUCCEEDED,
                    "chunks_created": len(document_chunks),
                    "vectors_created": len(records),
                    "completed_at": completed_at,
                }
            )
            await self._documents.save(
                document.model_copy(
                    update={"status": DocumentStatus.INDEXED, "updated_at": completed_at}
                )
            )
            await self._jobs.save(succeeded)
            return succeeded
        except Exception as error:
            if replacement_started:
                await self._best_effort_cleanup_version(document)
            completed_at = self._aware_now()
            failed = running.model_copy(
                update={
                    "status": IngestionStatus.FAILED,
                    "error_code": type(error).__name__[:128],
                    "completed_at": completed_at,
                }
            )
            failure_status = (
                DocumentStatus.INDEXED
                if document.status is DocumentStatus.INDEXED and not replacement_started
                else DocumentStatus.FAILED
            )
            await self._documents.save(
                document.model_copy(update={"status": failure_status, "updated_at": completed_at})
            )
            await self._jobs.save(failed)
            if isinstance(error, IngestionError):
                raise
            raise IngestionFailedError("document ingestion failed") from error

    async def _embed_chunks(
        self,
        document_chunks: tuple[DocumentChunk, ...],
        command: IngestDocumentCommand,
    ) -> tuple[VectorRecord, ...]:
        records: list[VectorRecord] = []
        expected_dimensions: int | None = None
        for offset in range(0, len(document_chunks), command.embedding_batch_size):
            batch = document_chunks[offset : offset + command.embedding_batch_size]
            response = await self._embeddings.embed(
                EmbeddingRequest(
                    model_alias=command.embedding_model_alias,
                    texts=tuple(chunk.content for chunk in batch),
                )
            )
            if len(response.vectors) != len(batch):
                raise InvalidEmbeddingResponseError(
                    "embedding provider returned an unexpected vector count"
                )
            if expected_dimensions is None:
                expected_dimensions = response.dimensions
            elif response.dimensions != expected_dimensions:
                raise InvalidEmbeddingResponseError("embedding dimensions changed during ingestion")
            for chunk, vector in zip(batch, response.vectors, strict=True):
                if len(vector) != response.dimensions:
                    raise InvalidEmbeddingResponseError(
                        "embedding vector does not match declared dimensions"
                    )
                records.append(
                    VectorRecord(
                        tenant_id=chunk.tenant_id,
                        document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                        classification=chunk.classification,
                        allowed_roles=chunk.allowed_roles,
                        source_version=chunk.source_version,
                        checksum=chunk.checksum,
                        vector=vector,
                        embedding_model_id=response.model_id,
                        embedding_dimensions=response.dimensions,
                        pipeline_version=command.pipeline_version,
                    )
                )
        return tuple(records)

    async def _cleanup_version(self, document: Document) -> None:
        await asyncio.gather(
            self._chunks.delete_version(
                document.tenant_id,
                document.document_id,
                document.source_version,
            ),
            self._vectors.delete_version(
                document.tenant_id,
                document.document_id,
                document.source_version,
            ),
        )

    async def _best_effort_cleanup_version(self, document: Document) -> None:
        await asyncio.gather(
            self._chunks.delete_version(
                document.tenant_id,
                document.document_id,
                document.source_version,
            ),
            self._vectors.delete_version(
                document.tenant_id,
                document.document_id,
                document.source_version,
            ),
            return_exceptions=True,
        )

    @staticmethod
    def _validate_extraction_identity(document: Document, extracted: ExtractedDocument) -> None:
        if (
            document.tenant_id != extracted.tenant_id
            or document.document_id != extracted.document_id
            or document.source_version != extracted.source_version
        ):
            raise InvalidExtractionError(
                "extracted document identity does not match document metadata"
            )

    @staticmethod
    def _ensure_ingestable(document: Document) -> None:
        if document.status not in {
            DocumentStatus.UPLOADED,
            DocumentStatus.FAILED,
            DocumentStatus.INDEXED,
        }:
            raise DocumentNotReadyError("document state does not permit ingestion")

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value

    def _fingerprint(self, document: Document, command: IngestDocumentCommand) -> str:
        material = "\x00".join(
            (
                str(document.tenant_id),
                str(document.document_id),
                document.source_version,
                document.source_checksum,
                self._chunker.version,
                command.embedding_model_alias,
                command.pipeline_version,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
