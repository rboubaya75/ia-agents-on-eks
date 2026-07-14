from datetime import UTC, datetime

from fastapi.testclient import TestClient
from ia_application import (
    CreateSourceUploadCommand,
    DeleteDocumentCommand,
    PresignedSourceUpload,
    RegisterDocumentCommand,
    StartDocumentIngestionCommand,
)
from ia_backend_api import AppContainer, StaticReadinessProbe, create_app
from ia_domain import (
    ChatSession,
    Classification,
    Document,
    DocumentId,
    DocumentStatus,
    IngestionJob,
    IngestionStatus,
    JobId,
    Role,
    SessionId,
    TenantId,
    UserId,
)
from ia_security import Principal

NOW = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
CHECKSUM = "a" * 64


class StaticTokenVerifier:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    async def verify(self, access_token: str) -> Principal:
        del access_token
        return self._principal


class EmptyChatSessions:
    async def save(self, session: ChatSession) -> None:
        del session

    async def get(self, tenant_id: TenantId, session_id: SessionId) -> ChatSession | None:
        del tenant_id, session_id
        return None

    async def list_for_user(
        self,
        tenant_id: TenantId,
        user_id: UserId,
    ) -> tuple[ChatSession, ...]:
        del tenant_id, user_id
        return ()

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> bool:
        del tenant_id, session_id
        return False


class StubDocuments:
    def __init__(self) -> None:
        self.documents: dict[tuple[TenantId, DocumentId], Document] = {}
        self.jobs: dict[tuple[TenantId, JobId], IngestionJob] = {}
        self.last_register: RegisterDocumentCommand | None = None
        self.last_submit: StartDocumentIngestionCommand | None = None
        self.last_delete: DeleteDocumentCommand | None = None

    async def register(self, command: RegisterDocumentCommand) -> Document:
        self.last_register = command
        document = Document(
            tenant_id=command.tenant_id,
            document_id=command.document_id,
            owner_user_id=command.owner_user_id,
            title=command.title,
            source_uri="s3://bucket/source",
            source_version=command.source_checksum,
            source_checksum=command.source_checksum,
            content_type=command.content_type,
            language=command.language,
            classification=command.classification,
            allowed_roles=command.allowed_roles,
            status=DocumentStatus.PENDING_UPLOAD,
            created_at=NOW,
            updated_at=NOW,
        )
        self.documents[(document.tenant_id, document.document_id)] = document
        return document

    async def create_upload(
        self,
        command: CreateSourceUploadCommand,
    ) -> PresignedSourceUpload:
        return PresignedSourceUpload(
            upload_session_id=command.upload_session_id,
            url="https://upload.example",
            headers={"content-length": str(command.size_bytes)},
            expires_at=command.expires_at,
        )

    async def get_document(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> Document:
        return self.documents[(tenant_id, document_id)]

    async def submit_ingestion(
        self,
        command: StartDocumentIngestionCommand,
    ) -> IngestionJob:
        self.last_submit = command
        job = IngestionJob(
            tenant_id=command.tenant_id,
            job_id=command.job_id,
            document_id=command.document_id,
            source_version=CHECKSUM,
            status=IngestionStatus.PENDING,
            started_at=NOW,
        )
        self.jobs[(job.tenant_id, job.job_id)] = job
        return job

    async def get_job(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        job_id: JobId,
    ) -> IngestionJob:
        job = self.jobs[(tenant_id, job_id)]
        assert job.document_id == document_id
        return job

    async def delete_document(self, command: DeleteDocumentCommand) -> Document:
        self.last_delete = command
        current = self.documents[(command.tenant_id, command.document_id)]
        deleted = current.model_copy(update={"status": DocumentStatus.DELETED, "updated_at": NOW})
        self.documents[(command.tenant_id, command.document_id)] = deleted
        return deleted


def _principal(
    *,
    roles: frozenset[Role],
    maximum_classification: Classification = Classification.CONFIDENTIAL,
) -> Principal:
    return Principal(
        user_id=UserId("admin-a"),
        tenant_id=TenantId("tenant-a"),
        email="admin-a@example.com",
        roles=roles,
        scopes=frozenset(
            {
                "platform/documents.read",
                "platform/documents.write",
                "platform/profile.read",
            }
        ),
        maximum_classification=maximum_classification,
    )


def _client(
    *,
    roles: frozenset[Role],
    maximum_classification: Classification = Classification.CONFIDENTIAL,
) -> tuple[TestClient, StubDocuments]:
    documents = StubDocuments()
    identifiers = iter(("document-a", "upload-a", "delete-a"))
    app = create_app(
        AppContainer(
            token_verifier=StaticTokenVerifier(
                _principal(
                    roles=roles,
                    maximum_classification=maximum_classification,
                )
            ),
            chat_sessions=EmptyChatSessions(),
            readiness=StaticReadinessProbe(),
            documents=documents,
            now=lambda: NOW,
            new_id=lambda: next(identifiers),
        )
    )
    return TestClient(app, raise_server_exceptions=False), documents


def _headers(*, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": "Bearer token"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _stored_document(
    *,
    allowed_roles: frozenset[Role],
    classification: Classification = Classification.INTERNAL,
) -> Document:
    return Document(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        owner_user_id=UserId("admin-a"),
        title="Policy",
        source_uri="s3://bucket/source",
        source_version=CHECKSUM,
        source_checksum=CHECKSUM,
        content_type="text/plain",
        language="fr",
        classification=classification,
        allowed_roles=allowed_roles,
        status=DocumentStatus.INDEXED,
        created_at=NOW,
        updated_at=NOW,
    )


def test_document_api_lifecycle_derives_tenant_and_returns_202() -> None:
    client, documents = _client(roles=frozenset({Role.TENANT_ADMIN}))

    invalid = client.post(
        "/api/v1/documents",
        headers=_headers(),
        json={
            "title": "Policy",
            "sourceChecksum": CHECKSUM,
            "contentType": "text/markdown",
            "language": "fr",
            "classification": "internal",
            "allowedRoles": ["user"],
            "tenantId": "tenant-b",
        },
    )
    created = client.post(
        "/api/v1/documents",
        headers=_headers(),
        json={
            "title": "Policy",
            "sourceChecksum": CHECKSUM,
            "contentType": "text/markdown",
            "language": "fr",
            "classification": "internal",
            "allowedRoles": ["user"],
        },
    )
    upload = client.post(
        "/api/v1/documents/document-a/upload-url",
        headers=_headers(),
        json={"sizeBytes": 128, "expiresInSeconds": 300},
    )
    ingested = client.post(
        "/api/v1/documents/document-a/ingestions",
        headers=_headers(idempotency_key="request-a"),
        json={"uploadSessionId": "upload-a"},
    )
    job_id = ingested.json()["job"]["jobId"]
    job = client.get(
        f"/api/v1/documents/document-a/ingestions/{job_id}",
        headers=_headers(),
    )
    deleted = client.delete("/api/v1/documents/document-a", headers=_headers())

    assert invalid.status_code == 422
    assert created.status_code == 201
    assert documents.last_register is not None
    assert documents.last_register.tenant_id == TenantId("tenant-a")
    assert upload.status_code == 200
    assert upload.json()["upload"]["uploadSessionId"] == "upload-a"
    assert ingested.status_code == 202
    assert ingested.json()["job"]["status"] == "pending"
    assert documents.last_submit is not None
    assert documents.last_submit.upload_session_id == "upload-a"
    assert job.status_code == 200
    assert deleted.json()["deleted"] is True
    assert documents.last_delete is not None
    assert documents.last_delete.operation_id == "delete-a"


def test_ingestion_requires_idempotency_key() -> None:
    client, documents = _client(roles=frozenset({Role.TENANT_ADMIN}))
    document = _stored_document(allowed_roles=frozenset({Role.TENANT_ADMIN}))
    documents.documents[(document.tenant_id, document.document_id)] = document

    response = client.post(
        "/api/v1/documents/document-a/ingestions",
        headers=_headers(),
        json={},
    )

    assert response.status_code == 422


def test_client_cannot_select_embedding_or_pipeline() -> None:
    client, documents = _client(roles=frozenset({Role.TENANT_ADMIN}))
    document = _stored_document(allowed_roles=frozenset({Role.TENANT_ADMIN}))
    documents.documents[(document.tenant_id, document.document_id)] = document

    response = client.post(
        "/api/v1/documents/document-a/ingestions",
        headers=_headers(idempotency_key="request-a"),
        json={
            "embeddingModelAlias": "expensive-profile",
            "pipelineVersion": "force-reindex",
        },
    )

    assert response.status_code == 422
    assert documents.last_submit is None


def test_document_write_requires_admin_role() -> None:
    client, _ = _client(roles=frozenset({Role.USER}))

    response = client.post(
        "/api/v1/documents",
        headers=_headers(),
        json={
            "title": "Policy",
            "sourceChecksum": CHECKSUM,
            "contentType": "text/plain",
            "classification": "internal",
            "allowedRoles": ["user"],
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "document_management_forbidden"


def test_document_classification_is_limited_at_creation() -> None:
    client, _ = _client(roles=frozenset({Role.TENANT_ADMIN}))

    response = client.post(
        "/api/v1/documents",
        headers=_headers(),
        json={
            "title": "Restricted",
            "sourceChecksum": CHECKSUM,
            "contentType": "text/plain",
            "classification": "restricted",
            "allowedRoles": ["tenant-admin"],
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "classification_forbidden"


def test_all_existing_document_mutations_hide_above_clearance_document() -> None:
    client, documents = _client(
        roles=frozenset({Role.TENANT_ADMIN}),
        maximum_classification=Classification.INTERNAL,
    )
    document = _stored_document(
        allowed_roles=frozenset({Role.TENANT_ADMIN}),
        classification=Classification.RESTRICTED,
    )
    documents.documents[(document.tenant_id, document.document_id)] = document

    responses = (
        client.post(
            "/api/v1/documents/document-a/upload-url",
            headers=_headers(),
            json={"sizeBytes": 8},
        ),
        client.post(
            "/api/v1/documents/document-a/ingestions",
            headers=_headers(idempotency_key="request-a"),
            json={},
        ),
        client.delete(
            "/api/v1/documents/document-a",
            headers=_headers(),
        ),
    )

    assert all(response.status_code == 404 for response in responses)
    assert all(response.json()["error"]["code"] == "document_not_found" for response in responses)
    assert documents.last_submit is None
    assert documents.last_delete is None


def test_ingestion_status_requires_document_read_access() -> None:
    client, documents = _client(roles=frozenset({Role.USER}))
    document = _stored_document(allowed_roles=frozenset({Role.SUPPORT}))
    job = IngestionJob(
        tenant_id=document.tenant_id,
        job_id=JobId("job-a"),
        document_id=document.document_id,
        source_version=document.source_version,
        status=IngestionStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW,
    )
    documents.documents[(document.tenant_id, document.document_id)] = document
    documents.jobs[(job.tenant_id, job.job_id)] = job

    response = client.get(
        "/api/v1/documents/document-a/ingestions/job-a",
        headers=_headers(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"


def test_openapi_hides_tenant_and_pipeline_configuration() -> None:
    client, _ = _client(roles=frozenset({Role.TENANT_ADMIN}))

    schema = client.get("/api/openapi.json").json()
    create_properties = schema["components"]["schemas"]["CreateDocumentRequest"]["properties"]
    ingestion_properties = schema["components"]["schemas"]["StartIngestionRequest"]["properties"]

    assert "tenantId" not in create_properties
    assert "embeddingModelAlias" not in ingestion_properties
    assert "pipelineVersion" not in ingestion_properties
