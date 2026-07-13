import json
from collections.abc import Mapping
from typing import cast

import pytest
from ia_application import EmbeddingRequest
from ia_aws_clients.bedrock_embeddings import (
    BedrockRuntimeClient,
    BedrockTitanEmbeddingProvider,
    InvalidBedrockEmbeddingResponseError,
    TitanEmbeddingProfileSettings,
)
from pydantic import ValidationError


class StaticBedrockClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def invoke_model(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return self.response


class ByteBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


def _profile(
    *,
    alias: str = "default",
    dimensions: int = 256,
) -> TitanEmbeddingProfileSettings:
    return TitanEmbeddingProfileSettings(
        alias=alias,
        revision="revision-1",
        model_id="model-1",
        dimensions=dimensions,
    )


def _provider(response: dict[str, object]) -> BedrockTitanEmbeddingProvider:
    return BedrockTitanEmbeddingProvider(
        cast(BedrockRuntimeClient, StaticBedrockClient(response)),
        profiles=(_profile(),),
    )


@pytest.mark.parametrize("dimensions", [1, 255, 257, 4096])
def test_titan_profile_rejects_unsupported_dimensions(dimensions: int) -> None:
    with pytest.raises(ValidationError, match="256, 512, or 1024"):
        _profile(dimensions=dimensions)


@pytest.mark.parametrize(
    ("profiles", "max_concurrency", "max_text_characters", "message"),
    [
        ((), 4, 100, "at least one"),
        ((_profile(),), 0, 100, "max_concurrency"),
        ((_profile(),), 4, 0, "max_text_characters"),
        ((_profile(), _profile()), 4, 100, "aliases must be unique"),
    ],
)
def test_provider_constructor_validates_configuration(
    profiles: tuple[TitanEmbeddingProfileSettings, ...],
    max_concurrency: int,
    max_text_characters: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        BedrockTitanEmbeddingProvider(
            cast(BedrockRuntimeClient, StaticBedrockClient({})),
            profiles=profiles,
            max_concurrency=max_concurrency,
            max_text_characters=max_text_characters,
        )


@pytest.mark.parametrize(
    ("connect_timeout", "read_timeout", "attempts", "message"),
    [
        (0.0, 1.0, 1, "timeouts"),
        (1.0, 0.0, 1, "timeouts"),
        (1.0, 1.0, 0, "max_attempts"),
    ],
)
def test_from_profiles_rejects_invalid_runtime_configuration_before_aws_call(
    connect_timeout: float,
    read_timeout: float,
    attempts: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        BedrockTitanEmbeddingProvider.from_profiles(
            (_profile(),),
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
            max_attempts=attempts,
        )


@pytest.mark.parametrize(
    "response",
    [
        {"body": b"not-json"},
        {"body": json.dumps([1, 2, 3])},
    ],
)
@pytest.mark.asyncio
async def test_embed_rejects_invalid_json_shapes(response: dict[str, object]) -> None:
    provider = _provider(response)
    with pytest.raises(InvalidBedrockEmbeddingResponseError):
        await provider.embed(EmbeddingRequest(model_alias="default", texts=("text",)))


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"body": object()},
    ],
)
def test_response_body_requires_readable_payload(
    response: Mapping[str, object],
) -> None:
    with pytest.raises(InvalidBedrockEmbeddingResponseError, match="readable body"):
        BedrockTitanEmbeddingProvider._response_body(response)


def test_response_body_accepts_bytes_and_text() -> None:
    assert BedrockTitanEmbeddingProvider._response_body({"body": b"{}"}) == "{}"
    assert BedrockTitanEmbeddingProvider._response_body({"body": "{}"}) == "{}"
    assert BedrockTitanEmbeddingProvider._response_body({"body": ByteBody(b"{}")}) == "{}"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({}, "float embedding"),
        ({"embedding": [True] * 256}, "non-numeric"),
        ({"embedding": [0.1] * 255}, "dimensions"),
    ],
)
def test_vector_validation_rejects_malformed_payloads(
    response: Mapping[str, object],
    message: str,
) -> None:
    with pytest.raises(InvalidBedrockEmbeddingResponseError, match=message):
        BedrockTitanEmbeddingProvider._vector(response, 256)


@pytest.mark.parametrize("value", [True, -1, 1.5, "1"])
def test_token_count_must_be_a_non_negative_integer(value: object) -> None:
    with pytest.raises(InvalidBedrockEmbeddingResponseError, match="token count"):
        BedrockTitanEmbeddingProvider._token_count({"inputTextTokenCount": value})


def test_token_count_defaults_to_zero() -> None:
    assert BedrockTitanEmbeddingProvider._token_count({}) == 0
