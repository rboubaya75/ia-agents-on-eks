from datetime import datetime
from typing import Annotated

from ia_domain import ChatSession, Classification, Role
from pydantic import BaseModel, ConfigDict, Field


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
