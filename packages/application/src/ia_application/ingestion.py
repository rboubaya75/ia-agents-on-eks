import asyncio
import hashlib
import json
import math
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from ia_domain import (
    Document,
    DocumentChunk,
    DocumentId,
    DocumentStatus,
    IndexGeneration,
    IndexGenerationStatus,
    IngestionJob,
    IngestionStatus,
    JobId,
    TenantId,
)
from pydantic import BaseModel, ConfigDict, Field

from ia_application.ports import (
    ChunkingStrategy,
    ChunkStore,
    DocumentIngestionLeaseRepository,
    DocumentRepository,
    EmbeddingProfile,
    EmbeddingProvider,
    EmbeddingRequest,
    ExtractedDocument,
    IndexActivationRepository,
    IndexGenerationRepository,
    IngestionJobRepository,
    RepositoryConflictError,
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
    """Raised when another worker owns the document-version lease."""


class ConcurrentDocumentUpdateError(IngestionError):
    """Raised when metadata changed before index activation."""


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
    lease_ttl_seconds: Annotated[int, Field(ge=30, le=3600)] = 300
    execution_token: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        default_factory=lambda: uuid4().hex
    )


class DocumentIngestionService:
    def __init__(
        self,
        *,
        documents: DocumentRepository,
        jobs: IngestionJobRepository,
        leases: DocumentIngestionLeaseRepository,
        generations: IndexGenerationRepository,
        activations: IndexActivationRepository,
        extractor: TextExtractor,
        chunker: ChunkingStrategy,
        embeddings: EmbeddingProvider,
        chunks: ChunkStore,
        vectors: VectorRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._documents = documents
        self._jobs = jobs
        self._leases = leases
        self._generations = generations
        self._activations = activations
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
        self._ensure_ingestable(document)

        profile = await self._embeddings.resolve_profile(command.embedding_model_alias)
        authorization_checksum = self._authorization_checksum(document)
        fingerprint = self._fingerprint(
            document,
            command,
            profile=profile,
            authorization_checksum=authorization_checksum,
        )
        existing = await self._jobs.find_by_fingerprint(command.tenant_id, fingerprint)
        if existing is not None and existing.status is IngestionStatus.SUCCEEDED:
            return existing

        started_at = self._aware_now()
        lease_claim = await self._leases.acquire(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            source_version=document.source_version,
            owner_token=str(command.job_id),
            execution_token=command.execution_token,
            expires_at=started_at + timedelta(seconds=command.lease_ttl_seconds),
            now=started_at,
        )
        if not lease_claim.acquired:
            raise IngestionInProgressError("document version is already being ingested")

        lease = lease_claim.lease
        try:
            generation_id = self._generation_id(command, fingerprint, lease.fencing_token)
            running = IngestionJob(
                tenant_id=document.tenant_id,
                job_id=command.job_id,
                document_id=document.document_id,
                source_version=document.source_version,
                status=IngestionStatus.RUNNING,
                fingerprint=fingerprint,
                generation_id=generation_id,
                source_checksum=document.source_checksum,
                authorization_checksum=authorization_checksum,
                embedding_model_alias=command.embedding_model_alias,
                embedding_profile_revision=profile.revision,
                resolved_embedding_model_id=profile.model_id,
                embedding_dimensions=profile.dimensions,
                chunking_version=self._chunker.version,
                pipeline_version=command.pipeline_version,
                fencing_token=lease.fencing_token,
                started_at=started_at,
            )

            job_claim = await self._jobs.claim(running)
            if not job_claim.acquired:
                if job_claim.job.status is IngestionStatus.SUCCEEDED:
                    return job_claim.job
                raise IngestionInProgressError("identical ingestion is already running")

            generation = IndexGeneration(
                tenant_id=document.tenant_id,
                document_id=document.document_id,
                source_version=document.source_version,
                generation_id=generation_id,
                fingerprint=fingerprint,
                authorization_checksum=authorization_checksum,
                embedding_profile_revision=profile.revision,
                embedding_model_id=profile.model_id,
                embedding_dimensions=profile.dimensions,
                status=IndexGenerationStatus.BUILDING,
                fencing_token=lease.fencing_token,
                created_at=started_at,
            )
            await self._generations.save(generation)

            working_document = document
            if document.active_generation_id is None:
                working_document = await self._documents.save(
                    document.model_copy(
                        update={
                            "status": DocumentStatus.PROCESSING,
                            "updated_at": started_at,
                        }
                    ),
                    expected_revision=document.revision,
                )

            try:
                extracted = await self._extractor.extract(document)
                self._validate_extraction_identity(document, extracted)
                document_chunks = self._chunker.chunk(
                    document,
                    extracted,
                    generation_id=generation_id,
                    created_at=started_at,
                )
                if not document_chunks:
                    raise InvalidExtractionError("extraction produced no indexable content")

                records = await self._embed_chunks(document_chunks, command, profile)
                await self._chunks.put_batch(document_chunks)
                await self._vectors.upsert(records)

                ready_at = self._aware_now()
                ready_generation = generation.model_copy(
                    update={
                        "status": IndexGenerationStatus.READY,
                        "chunk_count": len(document_chunks),
                        "vector_count": len(records),
                        "ready_at": ready_at,
                    }
                )
                await self._generations.save(ready_generation)

                completed_at = self._aware_now()
                succeeded = running.model_copy(
                    update={
                        "status": IngestionStatus.SUCCEEDED,
                        "chunks_created": len(document_chunks),
                        "vectors_created": len(records),
                        "completed_at": completed_at,
                    }
                )
                try:
                    activated_document = await self._activations.activate(
                        generation=ready_generation,
                        succeeded_job=succeeded,
                        expected_document_revision=working_document.revision,
                        activated_at=completed_at,
                    )
                except RepositoryConflictError as error:
                    raise ConcurrentDocumentUpdateError(
                        "document metadata changed before index activation"
                    ) from error

                if activated_document.active_generation_id != generation_id:
                    raise ConcurrentDocumentUpdateError(
                        "index activation did not select the candidate"
                    )
                previous_generation_id = document.active_generation_id
                if previous_generation_id is not None and previous_generation_id != generation_id:
                    await self._best_effort_supersede_generation(
                        tenant_id=document.tenant_id,
                        document_id=document.document_id,
                        generation_id=previous_generation_id,
                    )
                return succeeded
            except Exception as error:
                await self._best_effort_cleanup_generation(document, generation_id)
                await self._best_effort_fail_generation(generation)
                completed_at = self._aware_now()
                failed = running.model_copy(
                    update={
                        "status": IngestionStatus.FAILED,
                        "error_code": self._failure_code(error),
                        "completed_at": completed_at,
                    }
                )
                await self._best_effort_restore_document_state(document)
                await self._jobs.save(failed)
                if isinstance(error, IngestionError):
                    raise
                raise IngestionFailedError("document ingestion failed") from error
        finally:
            await self._leases.release(lease)

    async def _embed_chunks(
        self,
        document_chunks: tuple[DocumentChunk, ...],
        command: IngestDocumentCommand,
        profile: EmbeddingProfile,
    ) -> tuple[VectorRecord, ...]:
        records: list[VectorRecord] = []
        for offset in range(0, len(document_chunks), command.embedding_batch_size):
            batch = document_chunks[offset : offset + command.embedding_batch_size]
            response = await self._embeddings.embed(
                EmbeddingRequest(
                    model_alias=command.embedding_model_alias,
                    texts=tuple(chunk.content for chunk in batch),
                )
            )
            if response.model_id != profile.model_id:
                raise InvalidEmbeddingResponseError("embedding model changed during ingestion")
            if response.dimensions != profile.dimensions:
                raise InvalidEmbeddingResponseError(
                    "embedding dimensions differ from the resolved profile"
                )
            if len(response.vectors) != len(batch):
                raise InvalidEmbeddingResponseError(
                    "embedding provider returned an unexpected vector count"
                )
            for chunk, vector in zip(batch, response.vectors, strict=True):
                if len(vector) != profile.dimensions:
                    raise InvalidEmbeddingResponseError(
                        "embedding vector does not match declared dimensions"
                    )
                if any(not math.isfinite(value) for value in vector):
                    raise InvalidEmbeddingResponseError(
                        "embedding vector contains a non-finite value"
                    )
                records.append(
                    VectorRecord(
                        tenant_id=chunk.tenant_id,
                        document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                        generation_id=chunk.generation_id,
                        classification=chunk.classification,
                        allowed_roles=chunk.allowed_roles,
                        source_version=chunk.source_version,
                        checksum=chunk.checksum,
                        vector=vector,
                        embedding_model_id=profile.model_id,
                        embedding_dimensions=profile.dimensions,
                        pipeline_version=command.pipeline_version,
                    )
                )
        return tuple(records)

    async def _best_effort_cleanup_generation(
        self,
        document: Document,
        generation_id: str,
    ) -> None:
        await asyncio.gather(
            self._chunks.delete_generation(
                document.tenant_id,
                document.document_id,
                generation_id,
            ),
            self._vectors.delete_generation(
                document.tenant_id,
                document.document_id,
                generation_id,
            ),
            return_exceptions=True,
        )

    async def _best_effort_fail_generation(
        self,
        generation: IndexGeneration,
    ) -> None:
        with suppress(Exception):
            await self._generations.save(
                generation.model_copy(
                    update={
                        "status": IndexGenerationStatus.FAILED,
                        "ready_at": None,
                        "activated_at": None,
                    }
                )
            )

    async def _best_effort_restore_document_state(
        self,
        original: Document,
    ) -> None:
        with suppress(Exception):
            current = await self._documents.get(original.tenant_id, original.document_id)
            if (
                current is None
                or current.status is not DocumentStatus.PROCESSING
                or current.active_generation_id is not None
            ):
                return
            await self._documents.save(
                current.model_copy(
                    update={"status": DocumentStatus.FAILED, "updated_at": self._aware_now()}
                ),
                expected_revision=current.revision,
            )

    async def _best_effort_supersede_generation(
        self,
        *,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        with suppress(Exception):
            previous = await self._generations.get(tenant_id, document_id, generation_id)
            if previous is not None:
                await self._generations.save(
                    previous.model_copy(update={"status": IndexGenerationStatus.SUPERSEDED})
                )
        await asyncio.gather(
            self._chunks.delete_generation(tenant_id, document_id, generation_id),
            self._vectors.delete_generation(tenant_id, document_id, generation_id),
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

    @staticmethod
    def _authorization_checksum(document: Document) -> str:
        payload = {
            "allowedRoles": sorted(role.value for role in document.allowed_roles),
            "classification": document.classification.value,
        }
        return hashlib.sha256(DocumentIngestionService._canonical_json(payload)).hexdigest()

    def _fingerprint(
        self,
        document: Document,
        command: IngestDocumentCommand,
        *,
        profile: EmbeddingProfile,
        authorization_checksum: str,
    ) -> str:
        payload = {
            "authorizationChecksum": authorization_checksum,
            "chunkingVersion": self._chunker.version,
            "contentType": document.content_type,
            "documentId": str(document.document_id),
            "embeddingProfile": profile.model_dump(mode="json"),
            "language": document.language,
            "pipelineVersion": command.pipeline_version,
            "sourceChecksum": document.source_checksum,
            "sourceUri": document.source_uri,
            "sourceVersion": document.source_version,
            "tenantId": str(document.tenant_id),
            "title": document.title,
        }
        return hashlib.sha256(self._canonical_json(payload)).hexdigest()

    @staticmethod
    def _canonical_json(payload: object) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def _generation_id(
        command: IngestDocumentCommand,
        fingerprint: str,
        fencing_token: int,
    ) -> str:
        material = "\x00".join((fingerprint, str(command.job_id), str(fencing_token)))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _failure_code(error: Exception) -> str:
        if isinstance(error, InvalidExtractionError):
            return "INVALID_EXTRACTION"
        if isinstance(error, InvalidEmbeddingResponseError):
            return "INVALID_EMBEDDING_RESPONSE"
        if isinstance(error, ConcurrentDocumentUpdateError):
            return "INDEX_ACTIVATION_CONFLICT"
        if isinstance(error, RepositoryConflictError):
            return "REPOSITORY_CONFLICT"
        return "INGESTION_FAILED"
