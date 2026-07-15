import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from ia_application import (
    CreateSourceUploadCommand,
    DocumentManagementService,
    DocumentPipelineSettings,
    DocumentSourceMetadata,
    IngestionTask,
    InvalidDocumentSourceError,
    PresignedSourceUpload,
    ReceivedIngestionTask,
    RegisterDocumentCommand,
    StartDocumentIngestionCommand,
    Utf8DocumentExtractor,
)
from ia_domain import (
    Classification,
    Document,
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


def _service() -> tuple[
    DocumentManagementService,
    InMemoryDocumentRepository,
    InMemoryIngestionJobRepository,
    InMemoryDocumentIngestionLeaseRepository,
    FakeSourceStore,
    FakeQueue,
]:
    documents = InMemoryDocumentRepository()
    jobs = InMemoryIngestionJobRepository()
    leases = InMemoryDocumentIngestionLeaseRepository()
    sources = FakeSourceStore()
    queue = FakeQueue()
    service = DocumentManagementService(
        documents=documents,
        jobs=jobs,
        leases=leases,
        sources=sources,
        queue=queue,
        pipeline=DocumentPipelineSettings(
            embedding_model_alias="server-profile",
            pipeline_version="pipeline-v1",
        ),
        max_source_bytes=1_000,
        clock=lambda: NOW,
    )
    return service, documents, jobs, leases, sources, queue


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
    service, documents, jobs, _, sources, queue = _service()
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
    service, _, _, _, _, queue = _service()
    await service.register(_register())
    command = StartDocumentIngestionCommand(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        job_id=JobId("job-a"),
        upload_session_id="upload-a",
    )

    first = await service.submit_ingestion(command)
    second = await service.submit_ingestion(command.model_copy(update={"upload_session_id": None}))

    assert second == first
    assert len(queue.tasks) == 2


@pytest.mark.asyncio
async def test_submission_rejects_mismatched_promoted_source_metadata() -> None:
    service, _, _, _, sources, queue = _service()
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
    _, _, _, _, sources, _ = _service()
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
