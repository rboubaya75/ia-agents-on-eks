import hashlib
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request, status
from ia_application import (
    CreateSourceUploadCommand,
    DeleteDocumentCommand,
    DocumentDeletionError,
    DocumentManagement,
    DocumentManagementError,
    DocumentStateConflictError,
    IngestionDispatchError,
    IngestionError,
    InvalidDocumentSourceError,
    ManagedDocumentNotFoundError,
    RegisterDocumentCommand,
    StartDocumentIngestionCommand,
    UnsupportedDocumentContentTypeError,
)
from ia_domain import Classification, Document, DocumentId, JobId, Role, SessionId
from ia_security import Principal

from ia_backend_api.auth import ensure_scopes, get_container, get_principal
from ia_backend_api.container import AppContainer
from ia_backend_api.errors import ApiError, install_exception_handlers
from ia_backend_api.middleware import install_request_context_middleware
from ia_backend_api.schemas import (
    CreateDocumentRequest,
    CreateSessionRequest,
    CreateUploadRequest,
    DeleteDocumentResponse,
    DeleteSessionResponse,
    DocumentResponse,
    DocumentView,
    HealthResponse,
    IngestionJobResponse,
    IngestionJobView,
    MeResponse,
    SessionListResponse,
    SessionResponse,
    SessionView,
    SourceUploadResponse,
    SourceUploadView,
    StartIngestionRequest,
)
from ia_backend_api.sessions import ChatSessionService

_CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}
_DOCUMENT_MANAGERS = frozenset({Role.TENANT_ADMIN, Role.PLATFORM_ADMIN})


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _trace_id(request: Request) -> str:
    return str(request.state.trace_id)


def _document_service(container: AppContainer) -> DocumentManagement:
    if container.documents is None:
        raise ApiError(
            status_code=503,
            code="document_service_unavailable",
            message="The document service is not configured.",
        )
    return container.documents


def _ensure_document_manager(principal: Principal) -> None:
    if principal.roles.isdisjoint(_DOCUMENT_MANAGERS):
        raise ApiError(
            status_code=403,
            code="document_management_forbidden",
            message="A document administrator role is required.",
        )


def _ensure_classification_allowed(
    principal: Principal,
    classification: Classification,
) -> None:
    if (
        _CLASSIFICATION_RANK[classification]
        > _CLASSIFICATION_RANK[principal.maximum_classification]
    ):
        raise ApiError(
            status_code=403,
            code="classification_forbidden",
            message="The requested document classification is not permitted.",
        )


def _ensure_document_readable(principal: Principal, document: Document) -> None:
    if (
        _CLASSIFICATION_RANK[document.classification]
        > _CLASSIFICATION_RANK[principal.maximum_classification]
        or principal.roles.isdisjoint(document.allowed_roles | _DOCUMENT_MANAGERS)
    ):
        raise ApiError(
            status_code=404,
            code="document_not_found",
            message="The document was not found.",
        )


def _document_api_error(error: Exception) -> ApiError:
    if isinstance(error, ManagedDocumentNotFoundError):
        return ApiError(
            status_code=404,
            code="document_not_found",
            message="The document was not found.",
        )
    if isinstance(error, UnsupportedDocumentContentTypeError):
        return ApiError(
            status_code=415,
            code="unsupported_document_type",
            message=str(error),
        )
    if isinstance(error, InvalidDocumentSourceError | FileNotFoundError):
        return ApiError(
            status_code=409,
            code="invalid_document_source",
            message=str(error),
        )
    if isinstance(error, DocumentStateConflictError):
        return ApiError(
            status_code=409,
            code="document_state_conflict",
            message=str(error),
        )
    if isinstance(error, DocumentDeletionError | IngestionDispatchError):
        return ApiError(
            status_code=503,
            code="document_operation_incomplete",
            message=str(error),
        )
    if isinstance(error, IngestionError):
        return ApiError(
            status_code=409,
            code="document_ingestion_failed",
            message=str(error),
        )
    if isinstance(error, DocumentManagementError):
        return ApiError(
            status_code=400,
            code="document_operation_failed",
            message=str(error),
        )
    return ApiError(
        status_code=500,
        code="internal_error",
        message="An unexpected error occurred.",
    )


def _ingestion_job_id(
    principal: Principal,
    document_id: str,
    idempotency_key: str,
) -> JobId:
    material = "\x00".join(
        (str(principal.tenant_id), document_id, idempotency_key)
    ).encode("utf-8")
    return JobId(hashlib.sha256(material).hexdigest())


async def _authorized_document(
    service: DocumentManagement,
    principal: Principal,
    document_id: str,
) -> Document:
    document = await service.get_document(
        principal.tenant_id,
        DocumentId(document_id),
    )
    _ensure_document_readable(principal, document)
    return document


def create_app(container: AppContainer) -> FastAPI:
    app = FastAPI(
        title="IA Agents Platform API",
        version="0.3.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.container = container
    install_request_context_middleware(app)
    install_exception_handlers(app)

    @app.get("/api/v1/health/live", response_model=HealthResponse, tags=["health"])
    async def live(request: Request) -> HealthResponse:
        return HealthResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            status="live",
        )

    @app.get(
        "/api/v1/health/ready",
        response_model=HealthResponse,
        responses={503: {"description": "A required dependency is unavailable."}},
        tags=["health"],
    )
    async def ready(
        request: Request,
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> HealthResponse:
        if not await dependencies.readiness.is_ready():
            raise ApiError(
                status_code=503,
                code="not_ready",
                message="The service is not ready.",
            )
        return HealthResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            status="ready",
        )

    @app.get("/api/v1/me", response_model=MeResponse, tags=["identity"])
    async def me(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> MeResponse:
        ensure_scopes(principal, {dependencies.scopes.profile_read})
        return MeResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            user_id=str(principal.user_id),
            tenant_id=str(principal.tenant_id),
            email=principal.email,
            roles=tuple(sorted(principal.roles, key=lambda role: role.value)),
            scopes=tuple(sorted(principal.scopes)),
            maximum_classification=principal.maximum_classification,
        )

    @app.post(
        "/api/v1/chat/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["chat-sessions"],
    )
    async def create_session(
        payload: CreateSessionRequest,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> SessionResponse:
        ensure_scopes(principal, {dependencies.scopes.chat_write})
        session = await ChatSessionService(dependencies.chat_sessions).create(
            principal=principal,
            session_id=SessionId(dependencies.new_id()),
            title=payload.title,
            now=dependencies.now(),
        )
        return SessionResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            session=SessionView.from_domain(session),
        )

    @app.get(
        "/api/v1/chat/sessions",
        response_model=SessionListResponse,
        tags=["chat-sessions"],
    )
    async def list_sessions(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> SessionListResponse:
        ensure_scopes(principal, {dependencies.scopes.chat_read})
        sessions = await ChatSessionService(dependencies.chat_sessions).list_for_principal(
            principal
        )
        return SessionListResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            sessions=tuple(SessionView.from_domain(session) for session in sessions),
        )

    @app.get(
        "/api/v1/chat/sessions/{session_id}",
        response_model=SessionResponse,
        tags=["chat-sessions"],
    )
    async def get_session(
        session_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> SessionResponse:
        ensure_scopes(principal, {dependencies.scopes.chat_read})
        session = await ChatSessionService(dependencies.chat_sessions).get_for_principal(
            principal=principal,
            session_id=SessionId(session_id),
        )
        return SessionResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            session=SessionView.from_domain(session),
        )

    @app.delete(
        "/api/v1/chat/sessions/{session_id}",
        response_model=DeleteSessionResponse,
        tags=["chat-sessions"],
    )
    async def delete_session(
        session_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> DeleteSessionResponse:
        ensure_scopes(principal, {dependencies.scopes.chat_write})
        await ChatSessionService(dependencies.chat_sessions).delete_for_principal(
            principal=principal,
            session_id=SessionId(session_id),
        )
        return DeleteSessionResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            session_id=session_id,
            deleted=True,
        )

    @app.post(
        "/api/v1/documents",
        response_model=DocumentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["documents"],
    )
    async def create_document(
        payload: CreateDocumentRequest,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> DocumentResponse:
        ensure_scopes(principal, {dependencies.scopes.document_write})
        _ensure_document_manager(principal)
        _ensure_classification_allowed(principal, payload.classification)
        try:
            document = await _document_service(dependencies).register(
                RegisterDocumentCommand(
                    tenant_id=principal.tenant_id,
                    owner_user_id=principal.user_id,
                    document_id=DocumentId(dependencies.new_id()),
                    title=payload.title,
                    source_checksum=payload.source_checksum,
                    content_type=payload.content_type,
                    language=payload.language,
                    classification=payload.classification,
                    allowed_roles=payload.allowed_roles,
                )
            )
        except Exception as error:
            raise _document_api_error(error) from error
        return DocumentResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            document=DocumentView.from_domain(document),
        )

    @app.post(
        "/api/v1/documents/{document_id}/upload-url",
        response_model=SourceUploadResponse,
        tags=["documents"],
    )
    async def create_document_upload(
        document_id: str,
        payload: CreateUploadRequest,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> SourceUploadResponse:
        ensure_scopes(principal, {dependencies.scopes.document_write})
        _ensure_document_manager(principal)
        service = _document_service(dependencies)
        try:
            await _authorized_document(service, principal, document_id)
            upload = await service.create_upload(
                CreateSourceUploadCommand(
                    tenant_id=principal.tenant_id,
                    document_id=DocumentId(document_id),
                    upload_session_id=dependencies.new_id(),
                    size_bytes=payload.size_bytes,
                    expires_at=dependencies.now()
                    + timedelta(seconds=payload.expires_in_seconds),
                )
            )
        except ApiError:
            raise
        except Exception as error:
            raise _document_api_error(error) from error
        return SourceUploadResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            document_id=document_id,
            upload=SourceUploadView.from_application(upload),
        )

    @app.get(
        "/api/v1/documents/{document_id}",
        response_model=DocumentResponse,
        tags=["documents"],
    )
    async def get_document(
        document_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> DocumentResponse:
        ensure_scopes(principal, {dependencies.scopes.document_read})
        try:
            document = await _authorized_document(
                _document_service(dependencies),
                principal,
                document_id,
            )
        except ApiError:
            raise
        except Exception as error:
            raise _document_api_error(error) from error
        return DocumentResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            document=DocumentView.from_domain(document),
        )

    @app.post(
        "/api/v1/documents/{document_id}/ingestions",
        response_model=IngestionJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["documents"],
    )
    async def start_document_ingestion(
        document_id: str,
        payload: StartIngestionRequest,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=128),
        ],
    ) -> IngestionJobResponse:
        ensure_scopes(principal, {dependencies.scopes.document_write})
        _ensure_document_manager(principal)
        service = _document_service(dependencies)
        try:
            await _authorized_document(service, principal, document_id)
            job = await service.submit_ingestion(
                StartDocumentIngestionCommand(
                    tenant_id=principal.tenant_id,
                    document_id=DocumentId(document_id),
                    job_id=_ingestion_job_id(principal, document_id, idempotency_key),
                    upload_session_id=payload.upload_session_id,
                )
            )
        except ApiError:
            raise
        except Exception as error:
            raise _document_api_error(error) from error
        return IngestionJobResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            job=IngestionJobView.from_domain(job),
        )

    @app.get(
        "/api/v1/documents/{document_id}/ingestions/{job_id}",
        response_model=IngestionJobResponse,
        tags=["documents"],
    )
    async def get_document_ingestion(
        document_id: str,
        job_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> IngestionJobResponse:
        ensure_scopes(principal, {dependencies.scopes.document_read})
        service = _document_service(dependencies)
        try:
            await _authorized_document(service, principal, document_id)
            job = await service.get_job(
                principal.tenant_id,
                DocumentId(document_id),
                JobId(job_id),
            )
        except ApiError:
            raise
        except Exception as error:
            raise _document_api_error(error) from error
        return IngestionJobResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            job=IngestionJobView.from_domain(job),
        )

    @app.delete(
        "/api/v1/documents/{document_id}",
        response_model=DeleteDocumentResponse,
        tags=["documents"],
    )
    async def delete_document(
        document_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        dependencies: Annotated[AppContainer, Depends(get_container)],
    ) -> DeleteDocumentResponse:
        ensure_scopes(principal, {dependencies.scopes.document_write})
        _ensure_document_manager(principal)
        service = _document_service(dependencies)
        try:
            await _authorized_document(service, principal, document_id)
            deleted = await service.delete_document(
                DeleteDocumentCommand(
                    tenant_id=principal.tenant_id,
                    document_id=DocumentId(document_id),
                    operation_id=dependencies.new_id(),
                )
            )
        except ApiError:
            raise
        except Exception as error:
            raise _document_api_error(error) from error
        return DeleteDocumentResponse(
            request_id=_request_id(request),
            trace_id=_trace_id(request),
            document_id=str(deleted.document_id),
            deleted=True,
        )

    return app
