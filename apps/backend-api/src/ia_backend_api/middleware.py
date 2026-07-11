import re
import secrets
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TRACEPARENT_PATTERN = re.compile(r"^00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def _request_id(header_value: str | None) -> str:
    if header_value is not None and _REQUEST_ID_PATTERN.fullmatch(header_value):
        return header_value
    return str(uuid4())


def _trace_id(traceparent: str | None) -> str:
    if traceparent is not None:
        match = _TRACEPARENT_PATTERN.fullmatch(traceparent.lower())
        if match is not None and match.group(1) != "0" * 32:
            return match.group(1)
    return secrets.token_hex(16)


def install_request_context_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = _request_id(request.headers.get("x-request-id"))
        request.state.trace_id = _trace_id(request.headers.get("traceparent"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response
