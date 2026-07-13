import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Protocol, cast

from ia_application import (
    EmbeddingProfile,
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BedrockEmbeddingError(RuntimeError):
    """Base class for Bedrock embedding adapter failures."""


class UnknownEmbeddingProfileError(BedrockEmbeddingError):
    """Raised when an embedding alias has no configured immutable profile."""


class InvalidBedrockEmbeddingResponseError(BedrockEmbeddingError):
    """Raised when Bedrock returns malformed or inconsistent embedding data."""


class BedrockRuntimeBody(Protocol):
    def read(self) -> bytes: ...


class BedrockRuntimeClient(Protocol):
    def invoke_model(self, **kwargs: object) -> dict[str, object]: ...


class TitanEmbeddingProfileSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    alias: Annotated[str, Field(min_length=1, max_length=128)]
    revision: Annotated[str, Field(min_length=1, max_length=128)]
    model_id: Annotated[str, Field(min_length=1, max_length=300)]
    dimensions: Annotated[int, Field(ge=1, le=4096)]
    normalize: bool = True

    @model_validator(mode="after")
    def validate_titan_dimensions(self) -> "TitanEmbeddingProfileSettings":
        if self.dimensions not in {256, 512, 1024}:
            msg = "Titan Text Embeddings V2 dimensions must be 256, 512, or 1024"
            raise ValueError(msg)
        return self

    def to_profile(self) -> EmbeddingProfile:
        return EmbeddingProfile(
            alias=self.alias,
            revision=self.revision,
            model_id=self.model_id,
            dimensions=self.dimensions,
        )


class BedrockTitanEmbeddingProvider(EmbeddingProvider):
    """Amazon Titan Text Embeddings V2 adapter using Bedrock InvokeModel."""

    def __init__(
        self,
        client: BedrockRuntimeClient,
        *,
        profiles: Sequence[TitanEmbeddingProfileSettings],
        max_concurrency: int = 4,
        max_text_characters: int = 50_000,
    ) -> None:
        if max_concurrency <= 0:
            msg = "max_concurrency must be positive"
            raise ValueError(msg)
        if max_text_characters <= 0:
            msg = "max_text_characters must be positive"
            raise ValueError(msg)
        profile_map = {profile.alias: profile for profile in profiles}
        if not profile_map:
            msg = "at least one embedding profile is required"
            raise ValueError(msg)
        if len(profile_map) != len(profiles):
            msg = "embedding profile aliases must be unique"
            raise ValueError(msg)
        self._client = client
        self._profiles = profile_map
        self._max_concurrency = max_concurrency
        self._max_text_characters = max_text_characters

    @classmethod
    def from_profiles(
        cls,
        profiles: Sequence[TitanEmbeddingProfileSettings],
        *,
        region_name: str | None = None,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        max_concurrency: int = 4,
        max_text_characters: int = 50_000,
    ) -> "BedrockTitanEmbeddingProvider":
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            msg = "Bedrock timeouts must be positive"
            raise ValueError(msg)
        if max_attempts <= 0:
            msg = "max_attempts must be positive"
            raise ValueError(msg)
        import boto3  # type: ignore[import-untyped]
        from botocore.config import Config  # type: ignore[import-untyped]

        client = boto3.client(
            "bedrock-runtime",
            region_name=region_name,
            config=Config(
                connect_timeout=connect_timeout_seconds,
                read_timeout=read_timeout_seconds,
                retries={"max_attempts": max_attempts, "mode": "standard"},
            ),
        )
        return cls(
            cast(BedrockRuntimeClient, client),
            profiles=profiles,
            max_concurrency=max_concurrency,
            max_text_characters=max_text_characters,
        )

    async def resolve_profile(self, model_alias: str) -> EmbeddingProfile:
        return self._settings(model_alias).to_profile()

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        settings = self._settings(request.model_alias)
        for text in request.texts:
            if not text.strip():
                msg = "embedding text must contain non-whitespace content"
                raise ValueError(msg)
            if len(text) > self._max_text_characters:
                msg = "embedding text exceeds the configured character limit"
                raise ValueError(msg)
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def embed_one(text: str) -> tuple[tuple[float, ...], int]:
            async with semaphore:
                return await self._embed_one(settings, text)

        results = await asyncio.gather(*(embed_one(text) for text in request.texts))
        vectors = tuple(vector for vector, _ in results)
        input_tokens = sum(token_count for _, token_count in results)
        return EmbeddingResponse(
            model_id=settings.model_id,
            dimensions=settings.dimensions,
            vectors=vectors,
            input_tokens=input_tokens,
        )

    async def _embed_one(
        self,
        settings: TitanEmbeddingProfileSettings,
        text: str,
    ) -> tuple[tuple[float, ...], int]:
        payload = json.dumps(
            {
                "inputText": text,
                "dimensions": settings.dimensions,
                "normalize": settings.normalize,
                "embeddingTypes": ["float"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        response = await asyncio.to_thread(
            self._client.invoke_model,
            body=payload,
            modelId=settings.model_id,
            accept="application/json",
            contentType="application/json",
        )
        body = self._response_body(response)
        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            msg = "Bedrock embedding response is not valid JSON"
            raise InvalidBedrockEmbeddingResponseError(msg) from error
        if not isinstance(decoded, Mapping):
            msg = "Bedrock embedding response must be a JSON object"
            raise InvalidBedrockEmbeddingResponseError(msg)
        vector = self._vector(decoded, settings.dimensions)
        token_count = self._token_count(decoded)
        return vector, token_count

    def _settings(self, alias: str) -> TitanEmbeddingProfileSettings:
        try:
            return self._profiles[alias]
        except KeyError as error:
            msg = f"embedding profile is not configured: {alias}"
            raise UnknownEmbeddingProfileError(msg) from error

    @staticmethod
    def _response_body(response: Mapping[str, object]) -> str:
        raw_body = response.get("body")
        if isinstance(raw_body, bytes):
            return raw_body.decode("utf-8")
        if isinstance(raw_body, str):
            return raw_body
        if raw_body is not None and hasattr(raw_body, "read"):
            payload = cast(BedrockRuntimeBody, raw_body).read()
            return payload.decode("utf-8")
        msg = "Bedrock response does not contain a readable body"
        raise InvalidBedrockEmbeddingResponseError(msg)

    @staticmethod
    def _vector(
        response: Mapping[str, object],
        expected_dimensions: int,
    ) -> tuple[float, ...]:
        raw_vector = response.get("embedding")
        if not isinstance(raw_vector, list):
            embeddings_by_type = response.get("embeddingsByType")
            if isinstance(embeddings_by_type, Mapping):
                raw_vector = embeddings_by_type.get("float")
        if not isinstance(raw_vector, list):
            msg = "Bedrock response does not contain a float embedding"
            raise InvalidBedrockEmbeddingResponseError(msg)
        vector: list[float] = []
        for value in raw_vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                msg = "Bedrock embedding contains a non-numeric value"
                raise InvalidBedrockEmbeddingResponseError(msg)
            converted = float(value)
            if not math.isfinite(converted):
                msg = "Bedrock embedding contains a non-finite value"
                raise InvalidBedrockEmbeddingResponseError(msg)
            vector.append(converted)
        if len(vector) != expected_dimensions:
            msg = "Bedrock embedding dimensions differ from the configured profile"
            raise InvalidBedrockEmbeddingResponseError(msg)
        return tuple(vector)

    @staticmethod
    def _token_count(response: Mapping[str, object]) -> int:
        value = response.get("inputTextTokenCount", 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            msg = "Bedrock input token count is invalid"
            raise InvalidBedrockEmbeddingResponseError(msg)
        return value
