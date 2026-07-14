from datetime import datetime
from typing import Annotated, Literal

from ia_application import PresignedSourceUpload
from ia_domain import (
    ChatSession,
    Classification,
    Document,
    IngestionJob,
    Role,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.capitalize() for word in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class HealthResponse(ApiModel):
    request_id: str
    trace_id: str
    status: str


class MeResponse(ApiModel):
    request_id: str
    trace_id: str
    user_id: str
    tenant_id: str
    email: str
    roles: tuple[Role, ...]
    scopes: tuple[str, ...]
    maximum_classification: Classification


class CreateSessionRequest(ApiModel):
    title: Annotated[str, Field(min_length=1, max_length=300)] = "New conversation"


class SessionView(ApiModel):
    session_id: str
    user_id: str
    status: str
    title: str
    created_at: datetime
    last_activity: datetime
    message_count: int

    @classmethod
    def from_domain(cls, session: ChatSession) -> "SessionView":
        return cls(
            session_id=str(session.session_id),
            user_id=str(session.user_id),
            status=session.status,
            title=session.title,
            created_at=session.created_at,
            last_activity=session.last_activity,
            message_count=session.message_count,
        )


class SessionResponse(ApiModel):
    request_id: str
    trace_id: str
    session: SessionView


class SessionListResponse(ApiModel):
    request_id: str
    trace_id: str
    sessions: tuple[SessionView, ...]


class DeleteSessionResponse(ApiModel):
    request_id: str
    trace_id: str
    session_id: str
    deleted: bool


class CreateDocumentRequest(ApiModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    source_checksum: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    content_type: Literal["text/plain", "text/markdown"]
    language: Annotated[str, Field(min_length=2, max_length=35)] = "fr"
    classification: Classification
    allowed_roles: Annotated[frozenset[Role], Field(min_length=1)]

    @field_validator("classification", mode="before")
    @classmethod
    def parse_classification(cls, value: object) -> Classification:
        if isinstance(value, Classification):
            return value
        if isinstance(value, str):
            return Classification(value)
        raise ValueError("classification must be a supported string value")

    @field_validator("allowed_roles", mode="before")
    @classmethod
    def parse_allowed_roles(cls, value: object) -> frozenset[Role]:
        if not isinstance(value, list | tuple | set | frozenset):
            raise ValueError("allowedRoles must be an array")
        roles: set[Role] = set()
        for item in value:
            if isinstance(item, Role):
                roles.add(item)
            elif isinstance(item, str):
                roles.add(Role(item))
            else:
                raise ValueError("allowedRoles contains an invalid role")
        return frozenset(roles)


class CreateUploadRequest(ApiModel):
    size_bytes: Annotated[int, Field(ge=1)]
    expires_in_seconds: Annotated[int, Field(ge=60, le=900)] = 300


class StartIngestionRequest(ApiModel):
    upload_session_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class DocumentView(ApiModel):
    document_id: str
    owner_user_id: str
    title: str
    source_version: str
    content_type: str
    language: str
    classification: Classification
    allowed_roles: tuple[Role, ...]
    status: str
    revision: int
    active_generation_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, document: Document) -> "DocumentView":
        return cls(
            document_id=str(document.document_id),
            owner_user_id=str(document.owner_user_id),
            title=document.title,
            source_version=document.source_version,
            content_type=document.content_type,
            language=document.language,
            classification=document.classification,
            allowed_roles=tuple(sorted(document.allowed_roles, key=lambda role: role.value)),
            status=document.status.value,
            revision=document.revision,
            active_generation_id=document.active_generation_id,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class DocumentResponse(ApiModel):
    request_id: str
    trace_id: str
    document: DocumentView


class SourceUploadView(ApiModel):
    upload_session_id: str
    url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime

    @classmethod
    def from_application(cls, upload: PresignedSourceUpload) -> "SourceUploadView":
        return cls(
            upload_session_id=upload.upload_session_id,
            url=upload.url,
            method=upload.method,
            headers=upload.headers,
            expires_at=upload.expires_at,
        )


class SourceUploadResponse(ApiModel):
    request_id: str
    trace_id: str
    document_id: str
    upload: SourceUploadView


class IngestionJobView(ApiModel):
    job_id: str
    document_id: str
    source_version: str
    status: str
    chunks_created: int
    vectors_created: int
    error_code: str | None
    generation_id: str | None
    started_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_domain(cls, job: IngestionJob) -> "IngestionJobView":
        return cls(
            job_id=str(job.job_id),
            document_id=str(job.document_id),
            source_version=job.source_version,
            status=job.status.value,
            chunks_created=job.chunks_created,
            vectors_created=job.vectors_created,
            error_code=job.error_code,
            generation_id=job.generation_id,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )


class IngestionJobResponse(ApiModel):
    request_id: str
    trace_id: str
    job: IngestionJobView


class DeleteDocumentResponse(ApiModel):
    request_id: str
    trace_id: str
    document_id: str
    deleted: bool
