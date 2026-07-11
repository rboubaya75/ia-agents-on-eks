import hashlib
import hmac
from contextvars import ContextVar, Token
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class RequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    trace_id: Annotated[str, Field(min_length=1, max_length=128)]
    tenant_pseudonym: Annotated[str, Field(min_length=1, max_length=128)]
    user_pseudonym: Annotated[str, Field(min_length=1, max_length=128)]
    session_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    agent_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None


_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def set_request_context(context: RequestContext) -> Token[RequestContext | None]:
    return _REQUEST_CONTEXT.set(context)


def get_request_context() -> RequestContext | None:
    return _REQUEST_CONTEXT.get()


def clear_request_context(token: Token[RequestContext | None]) -> None:
    _REQUEST_CONTEXT.reset(token)


def pseudonymize(identifier: str, key: bytes) -> str:
    if not identifier:
        msg = "identifier must not be empty"
        raise ValueError(msg)
    if not key:
        msg = "pseudonymization key must not be empty"
        raise ValueError(msg)
    return hmac.new(key, identifier.encode(), hashlib.sha256).hexdigest()[:32]
