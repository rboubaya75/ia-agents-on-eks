from collections.abc import Sequence
from typing import cast

import pytest
from ia_application import VectorQuery, VectorRecord
from ia_aws_clients.s3_documents import VectorKeyManifestStore
from ia_aws_clients.s3_vectors import (
    Boto3S3VectorsClient,
    S3VectorIndexSettings,
    S3VectorRepository,
)
from ia_domain import ChunkId, Classification, DocumentId, Role, TenantId


class RecordingManifestStore:
    def __init__(self) -> None:
        self.keys: dict[tuple[str, str, str], tuple[str, ...]] = {}
        self.events: list[str] = []

    async def record_keys(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
        keys: Sequence[str],
    ) -> None:
        self.events.append("manifest")
        key = (str(tenant_id), str(document_id), generation_id)
        self.keys[key] = tuple(dict.fromkeys((*self.keys.get(key, ()), *keys)))

    async def load_keys(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> tuple[str, ...]:
        return self.keys.get((str(tenant_id), str(document_id), generation_id), ())

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        self.keys.pop((str(tenant_id), str(document_id), generation_id), None)

    async def list_document_generations(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> tuple[str, ...]:
        return tuple(
            generation
            for tenant, document, generation in self.keys
            if tenant == str(tenant_id) and document == str(document_id)
        )


class RecordingS3VectorsClient:
    def __init__(self, manifests: RecordingManifestStore) -> None:
        self.manifests = manifests
        self.put_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.query_calls: list[dict[str, object]] = []
        self.query_response: dict[str, object] = {
            "distanceMetric": "cosine",
            "vectors": [],
        }
        self.fail_put = False

    def put_vectors(self, **kwargs: object) -> dict[str, object]:
        self.manifests.events.append("put")
        self.put_calls.append(kwargs)
        if self.fail_put:
            raise RuntimeError("put failed")
        return {}

    def query_vectors(self, **kwargs: object) -> dict[str, object]:
        self.query_calls.append(kwargs)
        return self.query_response

    def delete_vectors(self, **kwargs: object) -> dict[str, object]:
        self.delete_calls.append(kwargs)
        return {}


def _record() -> VectorRecord:
    return VectorRecord(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        chunk_id=ChunkId("chunk-a"),
        generation_id="generation-active",
        classification=Classification.CONFIDENTIAL,
        allowed_roles=frozenset({Role.SUPPORT}),
        source_version="v1",
        checksum="a" * 64,
        vector=(1.0, 0.0),
        embedding_model_id="model-v1",
        embedding_dimensions=2,
        pipeline_version="pipeline-v1",
    )


def _repository() -> tuple[
    S3VectorRepository,
    RecordingS3VectorsClient,
    RecordingManifestStore,
]:
    manifests = RecordingManifestStore()
    client = RecordingS3VectorsClient(manifests)
    repository = S3VectorRepository(
        cast(Boto3S3VectorsClient, client),
        settings=S3VectorIndexSettings(
            vector_bucket_name="vector-bucket",
            index_name="documents",
        ),
        manifests=cast(VectorKeyManifestStore, manifests),
    )
    return repository, client, manifests


@pytest.mark.asyncio
async def test_vector_manifest_precedes_write_and_supports_failed_write_cleanup() -> (
    None
):
    repository, client, manifests = _repository()
    client.fail_put = True
    with pytest.raises(RuntimeError, match="put failed"):
        await repository.upsert((_record(),))

    assert manifests.events == ["manifest", "put"]
    assert len(next(iter(manifests.keys.values()))) == 1
    client.fail_put = False
    await repository.delete_generation(
        TenantId("tenant-a"),
        DocumentId("document-a"),
        "generation-active",
    )
    assert len(client.delete_calls) == 1
    assert manifests.keys == {}


@pytest.mark.asyncio
async def test_query_requires_and_filters_authoritative_active_generations() -> None:
    repository, client, _ = _repository()
    query = VectorQuery(
        tenant_id=TenantId("tenant-a"),
        allowed_classifications=frozenset({Classification.CONFIDENTIAL}),
        allowed_roles=frozenset({Role.SUPPORT}),
        query_vector=(1.0, 0.0),
        top_k=5,
        allowed_generation_ids=frozenset({"generation-active"}),
    )
    client.query_response = {
        "distanceMetric": "cosine",
        "vectors": [
            {
                "distance": 0.1,
                "metadata": {
                    "tenantId": "tenant-a",
                    "documentId": "document-a",
                    "chunkId": "chunk-a",
                    "generationId": "generation-active",
                    "classification": "confidential",
                    "allowedRoles": ["support"],
                },
            },
            {
                "distance": 0.0,
                "metadata": {
                    "tenantId": "tenant-a",
                    "documentId": "document-a",
                    "chunkId": "candidate",
                    "generationId": "generation-candidate",
                    "classification": "confidential",
                    "allowedRoles": ["support"],
                },
            },
        ],
    }

    matches = await repository.query(query)

    assert len(matches) == 1
    assert matches[0].generation_id == "generation-active"
    filter_value = client.query_calls[0]["filter"]
    assert isinstance(filter_value, dict)
    clauses = filter_value["$and"]
    assert isinstance(clauses, list)
    assert clauses[-1] == {"generationId": {"$in": ["generation-active"]}}

    missing = query.model_copy(update={"allowed_generation_ids": None})
    with pytest.raises(ValueError, match="active generation"):
        await repository.query(missing)


@pytest.mark.asyncio
async def test_vector_records_require_immutable_model_metadata() -> None:
    repository, _, _ = _repository()
    with pytest.raises(ValueError, match="immutable"):
        await repository.upsert(
            (_record().model_copy(update={"embedding_model_id": None}),)
        )
