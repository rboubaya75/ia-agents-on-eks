from typing import cast

import pytest
from ia_application import EmbeddingProfile, EmbeddingProvider, EmbeddingRequest, EmbeddingResponse
from ia_aws_clients import (
    DynamoControlReadinessProbe,
    EmbeddingProfileReadinessProbe,
    S3VectorIndexReadinessProbe,
    S3VectorIndexSettings,
)
from ia_aws_clients.dynamodb_control import DynamoControlTable
from ia_aws_clients.readiness import Boto3S3VectorsReadinessClient
from ia_backend_api import CompositeReadinessProbe, StaticReadinessProbe


class FakeControlTable:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def get_item(
        self,
        key: dict[str, object],
        *,
        consistent_read: bool = False,
    ) -> dict[str, object] | None:
        del key, consistent_read
        if self.fail:
            raise RuntimeError("DynamoDB unavailable")
        return None


class FakeVectorClient:
    def __init__(self, *, fail: bool = False, empty: bool = False) -> None:
        self.fail = fail
        self.empty = empty
        self.calls: list[dict[str, object]] = []

    def get_index(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("S3 Vectors unavailable")
        return {} if self.empty else {"index": {"indexName": "documents"}}


class FakeEmbeddingProvider:
    def __init__(self, *, alias: str = "default", fail: bool = False) -> None:
        self.alias = alias
        self.fail = fail

    async def resolve_profile(self, model_alias: str) -> EmbeddingProfile:
        if self.fail:
            raise RuntimeError("profile unavailable")
        return EmbeddingProfile(
            alias=self.alias,
            revision="profile-v1",
            model_id="model-v1",
            dimensions=2,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        del request
        return EmbeddingResponse(
            model_id="model-v1",
            dimensions=2,
            vectors=((0.0, 1.0),),
        )


@pytest.mark.asyncio
async def test_dynamo_control_readiness_handles_success_and_failure() -> None:
    ready = DynamoControlReadinessProbe(cast(DynamoControlTable, FakeControlTable()))
    failed = DynamoControlReadinessProbe(cast(DynamoControlTable, FakeControlTable(fail=True)))

    assert await ready.is_ready() is True
    assert await failed.is_ready() is False


@pytest.mark.asyncio
async def test_vector_readiness_uses_configured_index_reference() -> None:
    client = FakeVectorClient()
    settings = S3VectorIndexSettings(
        vector_bucket_name="vector-bucket",
        index_name="documents",
    )
    probe = S3VectorIndexReadinessProbe(
        cast(Boto3S3VectorsReadinessClient, client),
        settings=settings,
    )

    assert await probe.is_ready() is True
    assert client.calls == [{"vectorBucketName": "vector-bucket", "indexName": "documents"}]

    client.fail = True
    assert await probe.is_ready() is False


@pytest.mark.asyncio
async def test_vector_readiness_rejects_empty_response() -> None:
    probe = S3VectorIndexReadinessProbe(
        cast(Boto3S3VectorsReadinessClient, FakeVectorClient(empty=True)),
        settings=S3VectorIndexSettings(index_arn="arn:aws:s3vectors:region:account:index/example"),
    )

    assert await probe.is_ready() is False


@pytest.mark.asyncio
async def test_embedding_profile_readiness_checks_expected_alias() -> None:
    ready = EmbeddingProfileReadinessProbe(
        cast(EmbeddingProvider, FakeEmbeddingProvider()),
        model_alias="default",
    )
    wrong = EmbeddingProfileReadinessProbe(
        cast(EmbeddingProvider, FakeEmbeddingProvider(alias="other")),
        model_alias="default",
    )
    failed = EmbeddingProfileReadinessProbe(
        cast(EmbeddingProvider, FakeEmbeddingProvider(fail=True)),
        model_alias="default",
    )

    assert await ready.is_ready() is True
    assert await wrong.is_ready() is False
    assert await failed.is_ready() is False


@pytest.mark.asyncio
async def test_composite_readiness_fails_closed() -> None:
    assert (
        await CompositeReadinessProbe((StaticReadinessProbe(), StaticReadinessProbe())).is_ready()
        is True
    )
    assert (
        await CompositeReadinessProbe(
            (StaticReadinessProbe(), StaticReadinessProbe(False))
        ).is_ready()
        is False
    )

    class RaisingProbe:
        async def is_ready(self) -> bool:
            raise RuntimeError("probe failed")

    assert (
        await CompositeReadinessProbe((StaticReadinessProbe(), RaisingProbe())).is_ready() is False
    )


def test_composite_readiness_requires_at_least_one_probe() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CompositeReadinessProbe(())
