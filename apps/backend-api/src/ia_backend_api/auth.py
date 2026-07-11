from collections.abc import Iterable
from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ia_security import (
    AuthenticationError,
    ExpiredTokenError,
    InsufficientScopeError,
    Principal,
)

from ia_backend_api.container import AppContainer
from ia_backend_api.errors import ApiError

_BEARER = HTTPBearer(auto_error=False)


def get_container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(
            status_code=401,
            code="authentication_required",
            message="A valid bearer access token is required.",
        )
    try:
        return await container.token_verifier.verify(credentials.credentials)
    except ExpiredTokenError as error:
        raise ApiError(
            status_code=401,
            code="token_expired",
            message="The access token has expired.",
        ) from error
    except InsufficientScopeError as error:
        raise ApiError(
            status_code=403,
            code="insufficient_scope",
            message="The access token does not grant this operation.",
        ) from error
    except AuthenticationError as error:
        raise ApiError(
            status_code=401,
            code="invalid_token",
            message="The access token is invalid.",
        ) from error


def ensure_scopes(principal: Principal, required_scopes: Iterable[str]) -> None:
    missing = frozenset(required_scopes).difference(principal.scopes)
    if missing:
        raise ApiError(
            status_code=403,
            code="insufficient_scope",
            message="The access token does not grant this operation.",
        )
