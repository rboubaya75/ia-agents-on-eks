from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from ia_application import (
    CreateSourceUploadCommand,
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
        self, tenant_id: TenantId, user_id: UserId
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
        self, command: CreateSourceUploadCommand
    ) -> PresignedSourceUpload:
        return PresignedSourceUpload(
            url="https://upload.example",
            headers={"content-length": str(command.size_bytes)},
            expires_at=command.expires_at,
        )

    async def get_document(
        self, tenant_id: TenantId, document_id: DocumentId
    ) -> Document:
        return self.documents[(tenant_id, document_id)]

    async def start_ingestion(
        self, command: StartDocumentIngestionCommand
    ) -> IngestionJob:
        job = IngestionJob(
            tenant_id=command.tenant_id,
            job_id=command.job_id,
            document_id=command.document_id,
            source_version=CHECKSUM,
            status=IngestionStatus.SUCCEEDED,
            started_at=NOW,
            completed_at=NOW,
        )
        self.jobs[(job.tenant_id, job.job_id)] = job
        return job

    async def get_job(
        self, tenant_id: TenantId, document_id: DocumentId, job_id: JobId
    ) -> IngestionJob:
        job = self.jobs[(tenant_id, job_id)]
        assert job.document_id == document_id
        return job

    async def delete_document(
        self, tenant_id: TenantId, document_id: DocumentId
    ) -> Document:
        current = self.documents[(tenant_id, document_id)]
        deleted = current.model_copy(
            update={"status": DocumentStatus.DELETED, "updated_at": NOW}
        )
        self.documents[(tenant_id, document_id)] = deleted
        return deleted


def _principal(*, roles: frozenset[Role]) -> Principal:
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
        maximum_classification=Classification.CONFIDENTIAL,
    )


def _client(*, roles: frozenset[Role]) -> tuple[TestClient, StubDocuments]:
    documents = StubDocuments()
    identifiers = iter(("document-a", "job-a"))
    app = create_app(
        AppContainer(
            token_verifier=StaticTokenVerifier(_principal(roles=roles)),
            chat_sessions=EmptyChatSessions(),
            readiness=StaticReadinessProbe(),
            documents=documents,
            now=lambda: NOW,
            new_id=lambda: next(identifiers),
        )
    )
    return TestClient(app, raise_server_exceptions=False), documents


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer token"}


def test_document_api_lifecycle_derives_tenant_from_principal() -> None:
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
    fetched = client.get("/api/v1/documents/document-a", headers=_headers())
    ingested = client.post(
        "/api/v1/documents/document-a/ingestions",
        headers=_headers(),
        json={"embeddingModelAlias": "default"},
    )
    job = client.get(
        "/api/v1/documents/document-a/ingestions/job-a",
        headers=_headers(),
    )
    deleted = client.delete("/api/v1/documents/document-a", headers=_headers())

    assert invalid.status_code == 422
    assert created.status_code == 201
    assert documents.last_register is not None
    assert documents.last_register.tenant_id == TenantId("tenant-a")
    assert upload.status_code == 200
    assert upload.json()["upload"]["method"] == "PUT"
    assert fetched.status_code == 200
    assert ingested.json()["job"]["status"] == "succeeded"
    assert job.status_code == 200
    assert deleted.json()["deleted"] is True


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


def test_document_classification_is_limited_by_trusted_principal() -> None:
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
