import asyncio
import json
import time
from collections.abc import Callable, Mapping
from typing import Protocol, cast
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from ia_domain import Classification, Role, TenantId, UserId
from jwt import PyJWTError
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ia_security.errors import (
    ExpiredTokenError,
    InsufficientScopeError,
    InvalidTokenError,
)
from ia_security.principal import Principal

JsonObject = Mapping[str, object]


class CognitoTokenVerifierSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    issuer: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=256)
    tenant_claim: str = Field(default="custom:tenant_id", min_length=1, max_length=128)
    email_claim: str = Field(default="email", min_length=1, max_length=128)
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    clock_skew_seconds: int = Field(default=30, ge=0, le=300)

    @field_validator("issuer")
    @classmethod
    def validate_issuer(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            msg = "issuer must be an HTTPS origin without query or fragment"
            raise ValueError(msg)
        return value.rstrip("/")

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


class JwkSetFetcher(Protocol):
    async def fetch(self, uri: str) -> JsonObject: ...


class HttpJwkSetFetcher:
    def __init__(self, timeout_seconds: float = 3.0) -> None:
        if timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        self._timeout_seconds = timeout_seconds

    async def fetch(self, uri: str) -> JsonObject:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = await client.get(uri, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            msg = "JWKS response must be a JSON object"
            raise InvalidTokenError(msg)
        return cast(JsonObject, payload)


class CachedJwkProvider:
    def __init__(
        self,
        *,
        jwks_uri: str,
        fetcher: JwkSetFetcher,
        ttl_seconds: int = 3600,
        min_refresh_interval_seconds: int = 60,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            msg = "ttl_seconds must be positive"
            raise ValueError(msg)
        if min_refresh_interval_seconds <= 0:
            msg = "min_refresh_interval_seconds must be positive"
            raise ValueError(msg)
        parsed = urlparse(jwks_uri)
        if parsed.scheme != "https" or not parsed.netloc:
            msg = "jwks_uri must use HTTPS"
            raise ValueError(msg)
        self._jwks_uri = jwks_uri
        self._fetcher = fetcher
        self._ttl_seconds = ttl_seconds
        self._min_refresh_interval_seconds = min_refresh_interval_seconds
        self._monotonic = monotonic
        self._keys: dict[str, JsonObject] = {}
        self._expires_at = 0.0
        self._last_refresh_at = float("-inf")
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> JsonObject | None:
        if not kid:
            return None
        now = self._monotonic()
        cached_key = self._keys.get(kid)
        if now < self._expires_at and cached_key is not None:
            return cached_key
        async with self._lock:
            now = self._monotonic()
            cached_key = self._keys.get(kid)
            if now < self._expires_at and cached_key is not None:
                return cached_key
            cache_expired = now >= self._expires_at
            refresh_interval_elapsed = (
                now - self._last_refresh_at >= self._min_refresh_interval_seconds
            )
            if cache_expired or refresh_interval_elapsed:
                await self._refresh(now)
            return self._keys.get(kid)

    async def _refresh(self, now: float) -> None:
        try:
            payload = await self._fetcher.fetch(self._jwks_uri)
        except (httpx.HTTPError, ValueError, TypeError) as error:
            msg = "unable to refresh Cognito signing keys"
            raise InvalidTokenError(msg) from error
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list):
            msg = "JWKS payload does not contain a keys array"
            raise InvalidTokenError(msg)
        parsed_keys: dict[str, JsonObject] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, dict):
                continue
            kid = raw_key.get("kid")
            if isinstance(kid, str) and kid:
                parsed_keys[kid] = cast(JsonObject, raw_key)
        if not parsed_keys:
            msg = "JWKS payload contains no usable signing keys"
            raise InvalidTokenError(msg)
        self._keys = parsed_keys
        self._expires_at = now + self._ttl_seconds
        self._last_refresh_at = now


class CognitoTokenVerifier:
    def __init__(
        self,
        *,
        settings: CognitoTokenVerifierSettings,
        jwk_provider: CachedJwkProvider,
    ) -> None:
        self._settings = settings
        self._jwk_provider = jwk_provider

    async def verify(self, access_token: str) -> Principal:
        if not access_token or len(access_token) > 16_384:
            msg = "access token is missing or exceeds the accepted size"
            raise InvalidTokenError(msg)
        try:
            header = jwt.get_unverified_header(access_token)
        except PyJWTError as error:
            msg = "access token header is invalid"
            raise InvalidTokenError(msg) from error
        if header.get("alg") != "RS256":
            msg = "access token algorithm is not allowed"
            raise InvalidTokenError(msg)
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            msg = "access token key identifier is missing"
            raise InvalidTokenError(msg)
        jwk = await self._jwk_provider.get_key(kid)
        if jwk is None:
            msg = "access token signing key is unknown"
            raise InvalidTokenError(msg)
        try:
            public_key = cast(
                RSAPublicKey, RSAAlgorithm.from_jwk(json.dumps(dict(jwk)))
            )
            claims = jwt.decode(
                access_token,
                key=public_key,
                algorithms=["RS256"],
                issuer=self._settings.issuer,
                leeway=self._settings.clock_skew_seconds,
                options={
                    "verify_aud": False,
                    "require": [
                        "client_id",
                        "exp",
                        "iat",
                        "iss",
                        "jti",
                        "sub",
                        "token_use",
                    ],
                },
            )
        except jwt.ExpiredSignatureError as error:
            msg = "access token has expired"
            raise ExpiredTokenError(msg) from error
        except (PyJWTError, ValueError, TypeError) as error:
            msg = "access token signature or registered claims are invalid"
            raise InvalidTokenError(msg) from error
        return self._build_principal(cast(Mapping[str, object], claims))

    def _build_principal(self, claims: Mapping[str, object]) -> Principal:
        if claims.get("token_use") != "access":
            msg = "token_use must be access"
            raise InvalidTokenError(msg)
        if claims.get("client_id") != self._settings.client_id:
            msg = "access token client_id is not trusted"
            raise InvalidTokenError(msg)

        subject = self._required_string(claims, "sub")
        tenant_id = self._required_string(claims, self._settings.tenant_claim)
        email = self._required_string(claims, self._settings.email_claim)
        token_id = self._required_string(claims, "jti")
        scopes = self._parse_scopes(claims.get("scope"))
        missing_scopes = self._settings.required_scopes.difference(scopes)
        if missing_scopes:
            msg = "access token does not contain all required scopes"
            raise InsufficientScopeError(msg)
        roles = self._parse_roles(claims.get("cognito:groups"))
        if not roles:
            msg = "access token does not contain a recognized role"
            raise InvalidTokenError(msg)

        return Principal(
            user_id=UserId(subject),
            tenant_id=TenantId(tenant_id),
            email=email,
            roles=roles,
            scopes=scopes,
            maximum_classification=self._maximum_classification(roles),
            token_id=token_id,
        )

    @staticmethod
    def _required_string(claims: Mapping[str, object], name: str) -> str:
        value = claims.get(name)
        if not isinstance(value, str) or not value.strip():
            msg = f"required claim is missing or invalid: {name}"
            raise InvalidTokenError(msg)
        return value.strip()

    @staticmethod
    def _parse_scopes(value: object) -> frozenset[str]:
        if value is None:
            return frozenset()
        if not isinstance(value, str):
            msg = "scope claim must be a space-delimited string"
            raise InvalidTokenError(msg)
        return frozenset(scope for scope in value.split() if scope)

    @staticmethod
    def _parse_roles(value: object) -> frozenset[Role]:
        if not isinstance(value, list):
            return frozenset()
        known_roles = {role.value: role for role in Role}
        return frozenset(
            known_roles[group]
            for group in value
            if isinstance(group, str) and group in known_roles
        )

    @staticmethod
    def _maximum_classification(roles: frozenset[Role]) -> Classification:
        if Role.PLATFORM_ADMIN in roles:
            return Classification.RESTRICTED
        if Role.TENANT_ADMIN in roles or Role.SUPPORT in roles:
            return Classification.CONFIDENTIAL
        return Classification.INTERNAL
