import asyncio
import base64
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Annotated, Protocol, cast

from ia_application import VectorMatch, VectorQuery, VectorRecord, VectorRepository
from ia_domain import ChunkId, Classification, DocumentId, Role, TenantId
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ia_aws_clients.s3_documents import VectorKeyManifestStore


class S3VectorError(RuntimeError):
    """Base class for S3 Vectors adapter failures."""


class InvalidS3VectorResponseError(S3VectorError):
    """Raised when S3 Vectors returns malformed or inconsistent data."""


class Boto3S3VectorsClient(Protocol):
    def put_vectors(self, **kwargs: object) -> dict[str, object]: ...

    def query_vectors(self, **kwargs: object) -> dict[str, object]: ...

    def delete_vectors(self, **kwargs: object) -> dict[str, object]: ...


class S3VectorIndexSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    vector_bucket_name: Annotated[str, Field(min_length=1, max_length=63)] | None = None
    index_name: Annotated[str, Field(min_length=1, max_length=63)] | None = None
    index_arn: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    write_batch_size: Annotated[int, Field(ge=1, le=500)] = 500
    delete_batch_size: Annotated[int, Field(ge=1, le=500)] = 500

    @model_validator(mode="after")
    def validate_index_reference(self) -> "S3VectorIndexSettings":
        by_name = self.vector_bucket_name is not None or self.index_name is not None
        if by_name and (
            self.vector_bucket_name is None or self.index_name is None or self.index_arn is not None
        ):
            msg = "configure either index_arn or both vector_bucket_name and index_name"
            raise ValueError(msg)
        if not by_name and self.index_arn is None:
            msg = "an S3 Vectors index reference is required"
            raise ValueError(msg)
        return self

    def request_reference(self) -> dict[str, object]:
        if self.index_arn is not None:
            return {"indexArn": self.index_arn}
        return {
            "vectorBucketName": cast(str, self.vector_bucket_name),
            "indexName": cast(str, self.index_name),
        }


class S3VectorRepository(VectorRepository):
    def __init__(
        self,
        client: Boto3S3VectorsClient,
        *,
        settings: S3VectorIndexSettings,
        manifests: VectorKeyManifestStore,
    ) -> None:
        self._client = client
        self._settings = settings
        self._manifests = manifests

    @classmethod
    def from_settings(
        cls,
        settings: S3VectorIndexSettings,
        *,
        manifests: VectorKeyManifestStore,
        region_name: str | None = None,
    ) -> "S3VectorRepository":
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("s3vectors", region_name=region_name)
        return cls(
            cast(Boto3S3VectorsClient, client),
            settings=settings,
            manifests=manifests,
        )

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        grouped: dict[tuple[TenantId, DocumentId, str], list[VectorRecord]] = defaultdict(list)
        for record in records:
            self._validate_record(record)
            grouped[(record.tenant_id, record.document_id, record.generation_id)].append(record)
        for (tenant_id, document_id, generation_id), group in grouped.items():
            await self._upsert_generation(
                tenant_id,
                document_id,
                generation_id,
                group,
            )

    async def delete_document(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> None:
        generation_ids = await self._manifests.list_document_generations(
            tenant_id,
            document_id,
        )
        for generation_id in generation_ids:
            await self.delete_generation(tenant_id, document_id, generation_id)

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        keys = await self._manifests.load_keys(tenant_id, document_id, generation_id)
        for offset in range(0, len(keys), self._settings.delete_batch_size):
            batch = keys[offset : offset + self._settings.delete_batch_size]
            if not batch:
                continue
            await asyncio.to_thread(
                self._client.delete_vectors,
                **self._settings.request_reference(),
                keys=list(batch),
            )
        await self._manifests.delete_generation(tenant_id, document_id, generation_id)

    async def query(self, query: VectorQuery) -> tuple[VectorMatch, ...]:
        if query.allowed_generation_ids is None:
            msg = "S3 Vectors queries require authoritative active generation IDs"
            raise ValueError(msg)
        response = await asyncio.to_thread(
            self._client.query_vectors,
            **self._settings.request_reference(),
            topK=query.top_k,
            queryVector={"float32": list(query.query_vector)},
            filter=self._filter(query),
            returnMetadata=True,
            returnDistance=True,
        )
        metric = response.get("distanceMetric")
        if metric not in {"cosine", "euclidean"}:
            msg = "S3 Vectors returned an unsupported distance metric"
            raise InvalidS3VectorResponseError(msg)
        raw_vectors = response.get("vectors")
        if not isinstance(raw_vectors, list):
            msg = "S3 Vectors response does not contain a vectors list"
            raise InvalidS3VectorResponseError(msg)
        matches: list[VectorMatch] = []
        for raw in raw_vectors:
            if not isinstance(raw, Mapping):
                msg = "S3 Vectors returned an invalid vector match"
                raise InvalidS3VectorResponseError(msg)
            match = self._match(raw, query, metric)
            if match is not None:
                matches.append(match)
        return tuple(matches[: query.top_k])

    async def _upsert_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
        records: Sequence[VectorRecord],
    ) -> None:
        for offset in range(0, len(records), self._settings.write_batch_size):
            batch = records[offset : offset + self._settings.write_batch_size]
            vectors = [self._put_vector(record) for record in batch]
            keys = tuple(cast(str, vector["key"]) for vector in vectors)
            await self._manifests.record_keys(
                tenant_id,
                document_id,
                generation_id,
                keys,
            )
            await asyncio.to_thread(
                self._client.put_vectors,
                **self._settings.request_reference(),
                vectors=vectors,
            )

    @staticmethod
    def _validate_record(record: VectorRecord) -> None:
        if (
            record.embedding_model_id is None
            or record.embedding_dimensions is None
            or record.pipeline_version is None
        ):
            msg = "vector records require immutable embedding and pipeline metadata"
            raise ValueError(msg)
        if len(record.vector) != record.embedding_dimensions:
            msg = "vector dimensions do not match record metadata"
            raise ValueError(msg)
        if any(not math.isfinite(value) for value in record.vector):
            msg = "vector contains a non-finite value"
            raise ValueError(msg)

    def _put_vector(self, record: VectorRecord) -> dict[str, object]:
        key = self._vector_key(record)
        return {
            "key": key,
            "data": {"float32": list(record.vector)},
            "metadata": {
                "tenantId": str(record.tenant_id),
                "documentId": str(record.document_id),
                "chunkId": str(record.chunk_id),
                "generationId": record.generation_id,
                "classification": record.classification.value,
                "allowedRoles": sorted(role.value for role in record.allowed_roles),
                "sourceVersion": record.source_version,
                "checksum": record.checksum,
                "embeddingModelId": record.embedding_model_id,
                "embeddingDimensions": record.embedding_dimensions,
                "pipelineVersion": record.pipeline_version,
            },
        }

    @staticmethod
    def _filter(query: VectorQuery) -> dict[str, object]:
        return {
            "$and": [
                {"tenantId": {"$eq": str(query.tenant_id)}},
                {
                    "classification": {
                        "$in": sorted(value.value for value in query.allowed_classifications)
                    }
                },
                {"allowedRoles": {"$in": sorted(role.value for role in query.allowed_roles)}},
                {"generationId": {"$in": sorted(query.allowed_generation_ids or frozenset())}},
            ]
        }

    @staticmethod
    def _match(
        raw: Mapping[str, object],
        query: VectorQuery,
        metric: str,
    ) -> VectorMatch | None:
        metadata = raw.get("metadata")
        distance = raw.get("distance")
        if not isinstance(metadata, Mapping):
            msg = "S3 Vectors match does not contain metadata"
            raise InvalidS3VectorResponseError(msg)
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            msg = "S3 Vectors match distance is invalid"
            raise InvalidS3VectorResponseError(msg)
        tenant_id = TenantId(_required_metadata_string(metadata, "tenantId"))
        document_id = DocumentId(_required_metadata_string(metadata, "documentId"))
        chunk_id = ChunkId(_required_metadata_string(metadata, "chunkId"))
        generation_id = _required_metadata_string(metadata, "generationId")
        classification = Classification(_required_metadata_string(metadata, "classification"))
        raw_roles = metadata.get("allowedRoles")
        if not isinstance(raw_roles, list):
            msg = "S3 Vectors allowedRoles metadata is invalid"
            raise InvalidS3VectorResponseError(msg)
        roles = frozenset(Role(str(role)) for role in raw_roles)
        if tenant_id != query.tenant_id:
            return None
        if classification not in query.allowed_classifications:
            return None
        if roles.isdisjoint(query.allowed_roles):
            return None
        if (
            query.allowed_generation_ids is not None
            and generation_id not in query.allowed_generation_ids
        ):
            return None
        return VectorMatch(
            tenant_id=tenant_id,
            document_id=document_id,
            chunk_id=chunk_id,
            generation_id=generation_id,
            score=_distance_score(float(distance), metric),
        )

    @staticmethod
    def _vector_key(record: VectorRecord) -> str:
        parts = (
            "v1",
            str(record.tenant_id),
            str(record.document_id),
            record.generation_id,
            str(record.chunk_id),
        )
        key = ".".join(_component(part) for part in parts)
        if len(key) > 1024:
            msg = "S3 Vectors key exceeds the service limit"
            raise ValueError(msg)
        return key


def _component(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _required_metadata_string(metadata: Mapping[str, object], name: str) -> str:
    value = metadata.get(name)
    if not isinstance(value, str) or not value:
        msg = f"S3 Vectors metadata is missing a string field: {name}"
        raise InvalidS3VectorResponseError(msg)
    return value


def _distance_score(distance: float, metric: str) -> float:
    if not math.isfinite(distance) or distance < 0:
        msg = "S3 Vectors returned an invalid distance"
        raise InvalidS3VectorResponseError(msg)
    if metric == "cosine":
        return max(0.0, min(1.0, 1.0 - distance))
    return 1.0 / (1.0 + distance)
