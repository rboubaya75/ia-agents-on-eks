from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    location: tuple[str, ...] = ()
    type: str = Field(min_length=1, max_length=128)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1000)
    details: tuple[ErrorDetail, ...] = ()


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _trace_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", "unknown"))


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: tuple[ErrorDetail, ...] = (),
) -> JSONResponse:
    body = {
        "requestId": _request_id(request),
        "traceId": _trace_id(request),
        "error": ErrorBody(code=code, message=message, details=details).model_dump(mode="json"),
    }
    return JSONResponse(status_code=status_code, content=body)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return _response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.message,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        details = tuple(
            ErrorDetail(
                location=tuple(str(item) for item in issue.get("loc", ())),
                type=str(issue.get("type", "validation_error")),
            )
            for issue in error.errors()
        )
        return _response(
            request,
            status_code=422,
            code="validation_error",
            message="The request payload is invalid.",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        return _response(
            request,
            status_code=error.status_code,
            code="http_error",
            message="The requested operation could not be completed.",
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _error: Exception) -> JSONResponse:
        return _response(
            request,
            status_code=500,
            code="internal_error",
            message="An internal error occurred.",
        )
