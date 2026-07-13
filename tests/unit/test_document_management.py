import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from ia_application import (
    CreateSourceUploadCommand,
    DocumentDeletionError,
    DocumentIngestionService,
    DocumentManagementService,
    DocumentSourceMetadata,
    InvalidDocumentSourceError,
    PresignedSourceUpload,
    RegisterDocumentCommand,
    StartDocumentIngestionCommand,
    Utf8DocumentExtractor,
    VectorMatch,
    VectorQuery,
    VectorRecord,
)
from ia_domain import (
    Classification,
    Document,
    DocumentChunk,
    DocumentId,
    DocumentStatus,
    IngestionJob,
    IngestionStatus,
    JobId,
    Role,
    TenantId,
    UserId,
)
from test_support import InMemoryDocumentRepository, InMemoryIngestionJobRepository

NOW = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
PAYLOAD = b"# Policy\r\n\r\nRefunds are allowed."
CHECKSUM = hashlib.sha256(PAYLOAD).hexdigest()


class FakeSourceStore:
    def __init__(self) -> None:
        self.payload = PAYLOAD
        self.metadata = DocumentSourceMetadata(
            content_type="text/markdown",
            size_bytes=len(PAYLOAD),
            checksum_sha256=CHECKSUM,
        )
        self.deleted = False
        self.fail_delete = False
        self.uploaded_document: Document | None = None

    def source_uri(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> str:
        return f"s3://bucket/{tenant_id}/{document_id}/{source_version}"

    async def create_upload(
        self,
        document: Document,
        *,
        size_bytes: int,
        expires_at: datetime,
    ) -> PresignedSourceUpload:
        self.uploaded_document = document
        return PresignedSourceUpload(
            url="https://upload.example",
            headers={"content-length": str(size_bytes)},
            expires_at=expires_at,
        )

    async def inspect(self, document: Document) -> DocumentSourceMetadata:
        del document
        return self.metadata

    async def read(self, document: Document, *, max_bytes: int) -> bytes:
        del document
        return self.payload[: max_bytes + 1]

    async def delete_document(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> None:
        del tenant_id, document_id
        if self.fail_delete:
            raise RuntimeError("source delete failed")
        self.deleted = True


class FakeIngestion:
    def __init__(self, jobs: InMemoryIngestionJobRepository) -> None:
        self.jobs = jobs
        self.command: object | None = None

    async def ingest(self, command: object) -> IngestionJob:
        from ia_application import IngestDocumentCommand

        typed = cast(IngestDocumentCommand, command)
        self.command = typed
        job = IngestionJob(
            tenant_id=typed.tenant_id,
            job_id=typed.job_id,
            document_id=typed.document_id,
            source_version=CHECKSUM,
            status=IngestionStatus.SUCCEEDED,
            started_at=NOW,
            completed_at=NOW,
        )
        await self.jobs.save(job)
        return job


class FakeChunkStore:
    def __init__(self) -> None:
        self.deleted = False

    async def put_batch(self, chunks: Sequence[DocumentChunk]) -> None:
        del chunks

    async def get(
        self,
        tenant_id: TenantId,
        generation_id: str,
        chunk_id: object,
    ) -> DocumentChunk | None:
        del tenant_id, generation_id, chunk_id
        return None

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None:
        del tenant_id, document_id
        self.deleted = True

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        del tenant_id, document_id, generation_id


class FakeVectorRepository:
    def __init__(self) -> None:
        self.deleted = False

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        del records

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None:
        del tenant_id, document_id
        self.deleted = True

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        del tenant_id, document_id, generation_id

    async def query(self, query: VectorQuery) -> tuple[VectorMatch, ...]:
        del query
        return ()


def _service() -> tuple[
    DocumentManagementService,
    InMemoryDocumentRepository,
    InMemoryIngestionJobRepository,
    FakeSourceStore,
    FakeChunkStore,
    FakeVectorRepository,
]:
    documents = InMemoryDocumentRepository()
    jobs = InMemoryIngestionJobRepository()
    sources = FakeSourceStore()
    chunks = FakeChunkStore()
    vectors = FakeVectorRepository()
    ingestion = FakeIngestion(jobs)
    service = DocumentManagementService(
        documents=documents,
        jobs=jobs,
        sources=sources,
        ingestion=cast(DocumentIngestionService, ingestion),
        chunks=chunks,
        vectors=vectors,
        max_source_bytes=1_000,
        clock=lambda: NOW,
    )
    return service, documents, jobs, sources, chunks, vectors


def _register() -> RegisterDocumentCommand:
    return RegisterDocumentCommand(
        tenant_id=TenantId("tenant-a"),
        owner_user_id=UserId("admin-a"),
        document_id=DocumentId("document-a"),
        title="Policy",
        source_checksum=CHECKSUM,
        content_type="text/markdown",
        language="fr",
        classification=Classification.INTERNAL,
        allowed_roles=frozenset({Role.USER}),
    )


@pytest.mark.asyncio
async def test_document_registration_upload_and_ingestion_lifecycle() -> None:
    service, documents, jobs, sources, _, _ = _service()

    registered = await service.register(_register())
    upload = await service.create_upload(
        CreateSourceUploadCommand(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            size_bytes=len(PAYLOAD),
            expires_at=NOW + timedelta(minutes=5),
        )
    )
    job = await service.start_ingestion(
        StartDocumentIngestionCommand(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            job_id=JobId("job-a"),
        )
    )

    assert registered.status is DocumentStatus.PENDING_UPLOAD
    assert registered.source_uri.startswith("s3://bucket/tenant-a/document-a/")
    assert upload.method == "PUT"
    assert sources.uploaded_document == registered
    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None and stored.status is DocumentStatus.UPLOADED
    assert job.status is IngestionStatus.SUCCEEDED
    assert await service.get_job(
        TenantId("tenant-a"), DocumentId("document-a"), JobId("job-a")
    ) == await jobs.get(TenantId("tenant-a"), JobId("job-a"))


@pytest.mark.asyncio
async def test_ingestion_rejects_mismatched_source_metadata() -> None:
    service, _, _, sources, _, _ = _service()
    await service.register(_register())
    sources.metadata = sources.metadata.model_copy(update={"checksum_sha256": "0" * 64})

    with pytest.raises(InvalidDocumentSourceError, match="checksum"):
        await service.start_ingestion(
            StartDocumentIngestionCommand(
                tenant_id=TenantId("tenant-a"),
                document_id=DocumentId("document-a"),
                job_id=JobId("job-a"),
            )
        )


@pytest.mark.asyncio
async def test_utf8_extractor_normalizes_text_and_rejects_binary_content() -> None:
    _, _, _, sources, _, _ = _service()
    document = Document(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        owner_user_id=UserId("admin-a"),
        title="Policy",
        source_uri="s3://bucket/key",
        source_version=CHECKSUM,
        source_checksum=CHECKSUM,
        content_type="text/markdown",
        language="fr",
        classification=Classification.INTERNAL,
        allowed_roles=frozenset({Role.USER}),
        status=DocumentStatus.UPLOADED,
        created_at=NOW,
        updated_at=NOW,
    )
    extractor = Utf8DocumentExtractor(sources, max_bytes=1_000)

    extracted = await extractor.extract(document)
    assert "\r" not in extracted.sections[0].content

    sources.payload = b"valid\x00invalid"
    invalid = document.model_copy(
        update={"source_checksum": hashlib.sha256(sources.payload).hexdigest()}
    )
    with pytest.raises(InvalidDocumentSourceError, match="control"):
        await extractor.extract(invalid)


@pytest.mark.asyncio
async def test_partial_deletion_leaves_recoverable_deleting_state() -> None:
    service, documents, _, sources, _, _ = _service()
    await service.register(_register())
    sources.fail_delete = True

    with pytest.raises(DocumentDeletionError, match="remains"):
        await service.delete_document(TenantId("tenant-a"), DocumentId("document-a"))

    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None and stored.status is DocumentStatus.DELETING
