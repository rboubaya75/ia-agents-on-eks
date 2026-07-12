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
    UnknownEmbeddingProfileError,
)


class ResponseBody:
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = dict(value)

    def read(self) -> bytes:
        return json.dumps(self._value).encode("utf-8")


class RecordingBedrockClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def invoke_model(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"body": ResponseBody(self.responses.pop(0))}


def _profile(dimensions: int = 256) -> TitanEmbeddingProfileSettings:
    return TitanEmbeddingProfileSettings(
        alias="default",
        revision="2026-07-12",
        model_id="configured-model-id",
        dimensions=dimensions,
    )


@pytest.mark.asyncio
async def test_titan_provider_resolves_profile_and_embeds_each_text() -> None:
    client = RecordingBedrockClient(
        [
            {"embedding": [0.1] * 256, "inputTextTokenCount": 2},
            {
                "embeddingsByType": {"float": [0.2] * 256},
                "inputTextTokenCount": 3,
            },
        ]
    )
    provider = BedrockTitanEmbeddingProvider(
        cast(BedrockRuntimeClient, client),
        profiles=(_profile(),),
    )

    resolved = await provider.resolve_profile("default")
    result = await provider.embed(
        EmbeddingRequest(model_alias="default", texts=("first", "second"))
    )

    assert resolved.model_id == "configured-model-id"
    assert result.dimensions == 256
    assert result.input_tokens == 5
    assert len(result.vectors) == 2
    raw_body = client.calls[0]["body"]
    assert isinstance(raw_body, bytes)
    request = json.loads(raw_body)
    assert request == {
        "inputText": "first",
        "dimensions": 256,
        "normalize": True,
        "embeddingTypes": ["float"],
    }
    assert client.calls[0]["modelId"] == "configured-model-id"


@pytest.mark.asyncio
async def test_titan_provider_rejects_unknown_alias_and_invalid_response() -> None:
    provider = BedrockTitanEmbeddingProvider(
        cast(BedrockRuntimeClient, RecordingBedrockClient([])),
        profiles=(_profile(),),
    )
    with pytest.raises(UnknownEmbeddingProfileError):
        await provider.resolve_profile("unknown")

    invalid_client = RecordingBedrockClient([{"embedding": [float("nan")] * 256}])
    invalid = BedrockTitanEmbeddingProvider(
        cast(BedrockRuntimeClient, invalid_client),
        profiles=(_profile(),),
    )
    with pytest.raises(InvalidBedrockEmbeddingResponseError, match="non-finite"):
        await invalid.embed(EmbeddingRequest(model_alias="default", texts=("text",)))


@pytest.mark.asyncio
async def test_titan_provider_rejects_blank_and_oversized_text_without_aws_call() -> None:
    client = RecordingBedrockClient([])
    provider = BedrockTitanEmbeddingProvider(
        cast(BedrockRuntimeClient, client),
        profiles=(_profile(),),
        max_text_characters=5,
    )
    with pytest.raises(ValueError, match="whitespace"):
        await provider.embed(EmbeddingRequest(model_alias="default", texts=("   ",)))
    with pytest.raises(ValueError, match="character limit"):
        await provider.embed(EmbeddingRequest(model_alias="default", texts=("123456",)))
    assert client.calls == []
