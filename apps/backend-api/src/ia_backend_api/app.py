from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from ia_domain import SessionId
from ia_security import Principal

from ia_backend_api.auth import ensure_scopes, get_container, get_principal
from ia_backend_api.container import AppContainer
from ia_backend_api.errors import ApiError, install_exception_handlers
from ia_backend_api.middleware import install_request_context_middleware
from ia_backend_api.schemas import (
    CreateSessionRequest,
    DeleteSessionResponse,
    HealthResponse,
    MeResponse,
    SessionListResponse,
    SessionResponse,
    SessionView,
)
from ia_backend_api.sessions import ChatSessionService


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _trace_id(request: Request) -> str:
    return str(request.state.trace_id)


def create_app(container: AppContainer) -> FastAPI:
    app = FastAPI(
        title="IA Agents Platform API",
        version="0.2.0",
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
        service = ChatSessionService(dependencies.chat_sessions)
        session = await service.create(
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

    return app
