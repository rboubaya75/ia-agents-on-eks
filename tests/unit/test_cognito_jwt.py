import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from ia_domain import Classification, Role
from ia_security import (
    CachedJwkProvider,
    CognitoTokenVerifier,
    CognitoTokenVerifierSettings,
    ExpiredTokenError,
    InsufficientScopeError,
    InvalidTokenError,
)
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://cognito-idp.eu-west-3.amazonaws.com/eu-west-3_example"
CLIENT_ID = "client-123"
KID = "key-1"


class StaticFetcher:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    async def fetch(self, uri: str) -> dict[str, object]:
        assert uri == f"{ISSUER}/.well-known/jwks.json"
        self.calls += 1
        return self.payload


class SequenceFetcher:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = payloads
        self.calls = 0

    async def fetch(self, uri: str) -> dict[str, object]:
        assert uri == f"{ISSUER}/.well-known/jwks.json"
        index = min(self.calls, len(self._payloads) - 1)
        self.calls += 1
        return self._payloads[index]


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture(scope="module")
def signing_material() -> Generator[
    tuple[RSAPrivateKey, dict[str, object]], None, None
]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = cast(
        dict[str, object], json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    )
    jwk["kid"] = KID
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    yield private_key, jwk


def _jwk(private_key: RSAPrivateKey, kid: str) -> dict[str, object]:
    value = cast(
        dict[str, object], json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    )
    value["kid"] = kid
    value["alg"] = "RS256"
    value["use"] = "sig"
    return value


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "client_id": CLIENT_ID,
        "token_use": "access",
        "sub": "user-123",
        "email": "user@example.com",
        "custom:tenant_id": "tenant-a",
        "cognito:groups": ["user"],
        "scope": "platform/profile.read platform/chat.read platform/chat.write",
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": "token-123",
    }
    claims.update(overrides)
    return claims


def _token(
    private_key: RSAPrivateKey, claims: dict[str, object], *, kid: str = KID
) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _verifier(
    jwk: dict[str, object], *, required_scopes: frozenset[str] = frozenset()
) -> tuple[CognitoTokenVerifier, StaticFetcher]:
    fetcher = StaticFetcher({"keys": [jwk]})
    settings = CognitoTokenVerifierSettings(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        required_scopes=required_scopes,
    )
    provider = CachedJwkProvider(
        jwks_uri=settings.jwks_uri,
        fetcher=fetcher,
        ttl_seconds=60,
    )
    return CognitoTokenVerifier(settings=settings, jwk_provider=provider), fetcher


@pytest.mark.asyncio
async def test_verifies_cognito_access_token_and_derives_principal(
    signing_material: tuple[RSAPrivateKey, dict[str, object]],
) -> None:
    private_key, jwk = signing_material
    verifier, fetcher = _verifier(
        jwk, required_scopes=frozenset({"platform/profile.read"})
    )

    principal = await verifier.verify(_token(private_key, _claims()))

    assert principal.user_id == "user-123"
    assert principal.tenant_id == "tenant-a"
    assert principal.roles == frozenset({Role.USER})
    assert principal.maximum_classification is Classification.INTERNAL
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_cached_key_avoids_second_fetch(
    signing_material: tuple[RSAPrivateKey, dict[str, object]],
) -> None:
    private_key, jwk = signing_material
    verifier, fetcher = _verifier(jwk)
    token = _token(private_key, _claims())

    await verifier.verify(token)
    await verifier.verify(token)

    assert fetcher.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "error_type"),
    [
        ({"iss": "https://wrong.example.com"}, InvalidTokenError),
        ({"client_id": "wrong-client"}, InvalidTokenError),
        ({"token_use": "id"}, InvalidTokenError),
        ({"custom:tenant_id": ""}, InvalidTokenError),
        ({"email": ""}, InvalidTokenError),
        ({"cognito:groups": ["unknown"]}, InvalidTokenError),
    ],
)
async def test_rejects_untrusted_claims(
    signing_material: tuple[RSAPrivateKey, dict[str, object]],
    overrides: dict[str, object],
    error_type: type[Exception],
) -> None:
    private_key, jwk = signing_material
    verifier, _ = _verifier(jwk)

    with pytest.raises(error_type):
        await verifier.verify(_token(private_key, _claims(**overrides)))


@pytest.mark.asyncio
async def test_rejects_expired_token(
    signing_material: tuple[RSAPrivateKey, dict[str, object]],
) -> None:
    private_key, jwk = signing_material
    verifier, _ = _verifier(jwk)
    expired = datetime.now(UTC) - timedelta(minutes=5)

    with pytest.raises(ExpiredTokenError):
        await verifier.verify(_token(private_key, _claims(exp=expired)))


@pytest.mark.asyncio
async def test_rejects_missing_required_scope(
    signing_material: tuple[RSAPrivateKey, dict[str, object]],
) -> None:
    private_key, jwk = signing_material
    verifier, _ = _verifier(jwk, required_scopes=frozenset({"platform/admin"}))

    with pytest.raises(InsufficientScopeError):
        await verifier.verify(_token(private_key, _claims()))


@pytest.mark.asyncio
async def test_unknown_kids_are_negative_cached(
    signing_material: tuple[RSAPrivateKey, dict[str, object]],
) -> None:
    private_key, jwk = signing_material
    verifier, fetcher = _verifier(jwk)

    with pytest.raises(InvalidTokenError):
        await verifier.verify(_token(private_key, _claims(), kid="unknown-a"))
    with pytest.raises(InvalidTokenError):
        await verifier.verify(_token(private_key, _claims(), kid="unknown-b"))

    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_new_signing_key_is_refreshed_after_minimum_interval(
    signing_material: tuple[RSAPrivateKey, dict[str, object]],
) -> None:
    first_private_key, first_jwk = signing_material
    second_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second_jwk = _jwk(second_private_key, "key-2")
    fetcher = SequenceFetcher(
        [
            {"keys": [first_jwk]},
            {"keys": [first_jwk, second_jwk]},
        ]
    )
    clock = MutableClock()
    settings = CognitoTokenVerifierSettings(issuer=ISSUER, client_id=CLIENT_ID)
    verifier = CognitoTokenVerifier(
        settings=settings,
        jwk_provider=CachedJwkProvider(
            jwks_uri=settings.jwks_uri,
            fetcher=fetcher,
            ttl_seconds=3600,
            min_refresh_interval_seconds=60,
            monotonic=clock,
        ),
    )

    await verifier.verify(_token(first_private_key, _claims()))
    with pytest.raises(InvalidTokenError):
        await verifier.verify(_token(second_private_key, _claims(), kid="key-2"))
    assert fetcher.calls == 1

    clock.value = 61.0
    principal = await verifier.verify(
        _token(second_private_key, _claims(), kid="key-2")
    )

    assert principal.tenant_id == "tenant-a"
    assert fetcher.calls == 2
