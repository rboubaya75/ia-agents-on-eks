import asyncio
from typing import Protocol, cast

from ia_application import EmbeddingProvider

from ia_aws_clients.dynamodb_control import DynamoControlTable
from ia_aws_clients.s3_vectors import S3VectorIndexSettings


class Boto3S3VectorsReadinessClient(Protocol):
    def get_index(self, **kwargs: object) -> dict[str, object]: ...


class DynamoControlReadinessProbe:
    def __init__(self, table: DynamoControlTable) -> None:
        self._table = table

    async def is_ready(self) -> bool:
        try:
            await self._table.get_item({"pk": "HEALTH", "sk": "HEALTH"})
        except Exception:
            return False
        return True


class S3VectorIndexReadinessProbe:
    def __init__(
        self,
        client: Boto3S3VectorsReadinessClient,
        *,
        settings: S3VectorIndexSettings,
    ) -> None:
        self._client = client
        self._settings = settings

    @classmethod
    def from_settings(
        cls,
        settings: S3VectorIndexSettings,
        *,
        region_name: str | None = None,
    ) -> "S3VectorIndexReadinessProbe":
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("s3vectors", region_name=region_name)
        return cls(
            cast(Boto3S3VectorsReadinessClient, client),
            settings=settings,
        )

    async def is_ready(self) -> bool:
        try:
            response = await asyncio.to_thread(
                self._client.get_index,
                **self._settings.request_reference(),
            )
        except Exception:
            return False
        return isinstance(response.get("index"), dict) or bool(response)


class EmbeddingProfileReadinessProbe:
    def __init__(self, provider: EmbeddingProvider, *, model_alias: str) -> None:
        self._provider = provider
        self._model_alias = model_alias

    async def is_ready(self) -> bool:
        try:
            profile = await self._provider.resolve_profile(self._model_alias)
        except Exception:
            return False
        return profile.alias == self._model_alias
