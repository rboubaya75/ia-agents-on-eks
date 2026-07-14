import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from ia_application import (
    CreateSourceUploadCommand,
    DeleteDocumentCommand,
    DocumentDeletionError,
    DocumentManagementService,
    DocumentPipelineSettings,
    DocumentSourceMetadata,
    DocumentStateConflictError,
    IngestionTask,
    InvalidDocumentSourceError,
    PresignedSourceUpload,
    ReceivedIngestionTask,
    RegisterDocumentCommand,
    StartDocumentIngestionCommand,
    Utf8DocumentExtractor,
    VectorMatch,
    VectorQuery,
    VectorRecord,
)
from ia_domain import (
    ChunkId,
    Classification,
    Document,
    DocumentChunk,
    DocumentId,
    DocumentStatus,
    IngestionStatus,
    JobId,
    Role,
    TenantId,
    UserId,
)
from test_support import (
    InMemoryDocumentIngestionLeaseRepository,
    InMemoryDocumentRepository,
    InMemoryIngestionJobRepository,
)

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
        self.promoted_session_id: str | None = None

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
        upload_session_id: str,
        size_bytes: int,
        expires_at: datetime,
    ) -> PresignedSourceUpload:
        self.uploaded_document = document
        return PresignedSourceUpload(
            upload_session_id=upload_session_id,
            url="https://upload.example",
            headers={"content-length": str(size_bytes)},
            expires_at=expires_at,
        )

    async def promote_upload(
        self,
        document: Document,
        upload_session_id: str,
    ) -> DocumentSourceMetadata:
        del document
        self.promoted_session_id = upload_session_id
        return self.metadata

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

    async def is_ready(self) -> bool:
        return True


class FakeQueue:
    def __init__(self) -> None:
        self.tasks: list[IngestionTask] = []
        self.received: ReceivedIngestionTask | None = None
        self.acknowledged: list[ReceivedIngestionTask] = []

    async def enqueue(self, task: IngestionTask) -> None:
        self.tasks.append(task)

    async def receive(self, *, wait_seconds: int) -> ReceivedIngestionTask | None:
        del wait_seconds
        return self.received

    async def acknowledge(self, received: ReceivedIngestionTask) -> None:
        self.acknowledged.append(received)

    async def is_ready(self) -> bool:
        return True


class FakeChunkStore:
    def __init__(self) -> None:
        self.deleted = False

    async def put_batch(self, chunks: Sequence[DocumentChunk]) -> None:
        del chunks

    async def get(
        self,
        tenant_id: TenantId,
        generation_id: str,
        chunk_id: ChunkId,
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
    InMemoryDocumentIngestionLeaseRepository,
    FakeSourceStore,
    FakeQueue,
    FakeChunkStore,
    FakeVectorRepository,
]:
    documents = InMemoryDocumentRepository()
    jobs = InMemoryIngestionJobRepository()
    leases = InMemoryDocumentIngestionLeaseRepository()
    sources = FakeSourceStore()
    queue = FakeQueue()
    chunks = FakeChunkStore()
    vectors = FakeVectorRepository()
    service = DocumentManagementService(
        documents=documents,
        jobs=jobs,
        leases=leases,
        sources=sources,
        queue=queue,
        chunks=chunks,
        vectors=vectors,
        pipeline=DocumentPipelineSettings(
            embedding_model_alias="server-profile",
            pipeline_version="pipeline-v1",
        ),
        max_source_bytes=1_000,
        clock=lambda: NOW,
    )
    return service, documents, jobs, leases, sources, queue, chunks, vectors


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
async def test_document_registration_upload_and_submission_lifecycle() -> None:
    service, documents, jobs, _, sources, queue, _, _ = _service()

    registered = await service.register(_register())
    upload = await service.create_upload(
        CreateSourceUploadCommand(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            upload_session_id="upload-a",
            size_bytes=len(PAYLOAD),
            expires_at=NOW + timedelta(minutes=5),
        )
    )
    job = await service.submit_ingestion(
        StartDocumentIngestionCommand(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            job_id=JobId("job-a"),
            upload_session_id="upload-a",
        )
    )

    assert registered.status is DocumentStatus.PENDING_UPLOAD
    assert upload.upload_session_id == "upload-a"
    assert sources.promoted_session_id == "upload-a"
    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None and stored.status is DocumentStatus.UPLOADED
    assert job.status is IngestionStatus.PENDING
    assert job.embedding_model_alias == "server-profile"
    assert job.pipeline_version == "pipeline-v1"
    assert queue.tasks == [
        IngestionTask(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            job_id=JobId("job-a"),
        )
    ]
    assert await jobs.get(TenantId("tenant-a"), JobId("job-a")) == job


@pytest.mark.asyncio
async def test_submission_is_idempotent_for_same_job_id() -> None:
    service, _, _, _, _, queue, _, _ = _service()
    await service.register(_register())
    command = StartDocumentIngestionCommand(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        job_id=JobId("job-a"),
        upload_session_id="upload-a",
    )

    first = await service.submit_ingestion(command)
    second = await service.submit_ingestion(
        command.model_copy(update={"upload_session_id": None})
    )

    assert second == first
    assert len(queue.tasks) == 2


@pytest.mark.asyncio
async def test_submission_rejects_mismatched_promoted_source_metadata() -> None:
    service, _, _, _, sources, queue, _, _ = _service()
    await service.register(_register())
    sources.metadata = sources.metadata.model_copy(update={"checksum_sha256": "0" * 64})

    with pytest.raises(InvalidDocumentSourceError, match="checksum"):
        await service.submit_ingestion(
            StartDocumentIngestionCommand(
                tenant_id=TenantId("tenant-a"),
                document_id=DocumentId("document-a"),
                job_id=JobId("job-a"),
                upload_session_id="upload-a",
            )
        )

    assert queue.tasks == []


@pytest.mark.asyncio
async def test_utf8_extractor_normalizes_text_and_rejects_binary_content() -> None:
    _, _, _, _, sources, _, _, _ = _service()
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
async def test_delete_is_rejected_while_ingestion_lease_is_held() -> None:
    service, documents, _, leases, _, _, _, _ = _service()
    registered = await service.register(_register())
    await leases.acquire(
        tenant_id=registered.tenant_id,
        document_id=registered.document_id,
        source_version=registered.source_version,
        owner_token="worker:job-a",
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )

    with pytest.raises(DocumentStateConflictError, match="busy"):
        await service.delete_document(
            DeleteDocumentCommand(
                tenant_id=TenantId("tenant-a"),
                document_id=DocumentId("document-a"),
                operation_id="delete-a",
            )
        )

    stored = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert stored is not None and stored.status is DocumentStatus.PENDING_UPLOAD


@pytest.mark.asyncio
async def test_partial_deletion_can_be_retried_to_tombstone() -> None:
    service, documents, _, _, sources, _, chunks, vectors = _service()
    await service.register(_register())
    sources.fail_delete = True
    command = DeleteDocumentCommand(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        operation_id="delete-a",
    )

    with pytest.raises(DocumentDeletionError, match="remains"):
        await service.delete_document(command)

    deleting = await documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert deleting is not None and deleting.status is DocumentStatus.DELETING

    sources.fail_delete = False
    deleted = await service.delete_document(command)

    assert deleted.status is DocumentStatus.DELETED
    assert sources.deleted is True
    assert chunks.deleted is True
    assert vectors.deleted is True
