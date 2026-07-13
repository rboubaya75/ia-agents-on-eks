import io
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast

import pytest
from ia_application import VectorQuery, VectorRecord
from ia_aws_clients.s3_chunk_store import _chunk_from_payload
from ia_aws_clients.s3_documents import (
    Boto3S3Client,
    S3ChunkStore,
    S3JsonStore,
    S3VectorKeyManifestStore,
    VectorKeyManifestStore,
)
from ia_aws_clients.s3_json_store import _component, _decode_component
from ia_aws_clients.s3_vectors import (
    Boto3S3VectorsClient,
    InvalidS3VectorResponseError,
    S3VectorIndexSettings,
    S3VectorRepository,
    _distance_score,
    _required_metadata_string,
)
from ia_domain import (
    ChunkId,
    Classification,
    DocumentChunk,
    DocumentId,
    Role,
    TenantId,
)
from pydantic import ValidationError

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class MissingKeyError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("missing")
        self.response: dict[str, object] = {"Error": {"Code": "NoSuchKey"}}


class EdgeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.delete_error = False
        self.unreadable_body = False
        self.pages: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> dict[str, object]:
        body = kwargs["Body"]
        if not isinstance(body, bytes):
            raise TypeError("Body must be bytes")
        self.objects[str(kwargs["Key"])] = body
        self.last_put = kwargs
        return {}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise MissingKeyError
        if self.unreadable_body:
            return {"Body": object()}
        return {"Body": io.BytesIO(self.objects[key])}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.objects.pop(str(kwargs["Key"]), None)
        return {}

    def delete_objects(self, **kwargs: object) -> dict[str, object]:
        if self.delete_error:
            return {"Errors": [{"Code": "AccessDenied"}]}
        delete = kwargs["Delete"]
        if not isinstance(delete, Mapping):
            raise TypeError("Delete must be a mapping")
        objects = delete.get("Objects")
        if not isinstance(objects, list):
            raise TypeError("Objects must be a list")
        for value in objects:
            if isinstance(value, Mapping):
                self.objects.pop(str(value["Key"]), None)
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        if self.pages:
            return self.pages.pop(0)
        prefix = str(kwargs["Prefix"])
        return {
            "Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)],
            "IsTruncated": False,
        }


class EdgeManifestStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], tuple[str, ...]] = {}
        self.deleted: list[tuple[str, str, str]] = []

    async def record_keys(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
        keys: Sequence[str],
    ) -> None:
        self.values[(str(tenant_id), str(document_id), generation_id)] = tuple(keys)

    async def load_keys(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> tuple[str, ...]:
        return self.values.get((str(tenant_id), str(document_id), generation_id), ())

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        key = (str(tenant_id), str(document_id), generation_id)
        self.deleted.append(key)
        self.values.pop(key, None)

    async def list_document_generations(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> tuple[str, ...]:
        return tuple(
            generation
            for tenant, document, generation in self.values
            if tenant == str(tenant_id) and document == str(document_id)
        )


class EdgeVectorsClient:
    def __init__(self) -> None:
        self.query_response: dict[str, object] = {
            "distanceMetric": "cosine",
            "vectors": [],
        }
        self.deleted: list[list[str]] = []

    def put_vectors(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {}

    def query_vectors(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return self.query_response

    def delete_vectors(self, **kwargs: object) -> dict[str, object]:
        keys = kwargs["keys"]
        if not isinstance(keys, list):
            raise TypeError("keys must be a list")
        self.deleted.append([str(key) for key in keys])
        return {}


def _chunk(
    *,
    document_id: str = "document-a",
    generation_id: str = "generation-a",
    chunk_id: str = "chunk-a",
) -> DocumentChunk:
    return DocumentChunk(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId(document_id),
        chunk_id=ChunkId(chunk_id),
        generation_id=generation_id,
        source_version="v1",
        source_uri="s3://bucket/key",
        title="Policy",
        section="Section",
        language="fr",
        classification=Classification.INTERNAL,
        allowed_roles=frozenset({Role.USER}),
        checksum="a" * 64,
        content="content",
        created_at=NOW,
        sequence=0,
        start_offset=0,
        end_offset=7,
        chunking_version="paragraph-v1",
    )


def _record(**updates: object) -> VectorRecord:
    record = VectorRecord(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        chunk_id=ChunkId("chunk-a"),
        generation_id="generation-a",
        classification=Classification.INTERNAL,
        allowed_roles=frozenset({Role.USER}),
        source_version="v1",
        checksum="a" * 64,
        vector=(1.0, 0.0),
        embedding_model_id="model-v1",
        embedding_dimensions=2,
        pipeline_version="pipeline-v1",
    )
    return record.model_copy(update=updates)


def _query() -> VectorQuery:
    return VectorQuery(
        tenant_id=TenantId("tenant-a"),
        allowed_classifications=frozenset({Classification.INTERNAL}),
        allowed_roles=frozenset({Role.USER}),
        query_vector=(1.0, 0.0),
        top_k=5,
        allowed_generation_ids=frozenset({"generation-a"}),
    )


def _vector_repository(
    client: EdgeVectorsClient,
    manifests: EdgeManifestStore,
) -> S3VectorRepository:
    return S3VectorRepository(
        cast(Boto3S3VectorsClient, client),
        settings=S3VectorIndexSettings(index_arn="arn:test:index"),
        manifests=cast(VectorKeyManifestStore, manifests),
    )


@pytest.mark.asyncio
async def test_json_store_default_encryption_missing_key_and_unreadable_body() -> None:
    client = EdgeS3Client()
    store = S3JsonStore(cast(Boto3S3Client, client), bucket_name="bucket", prefix="")
    await store.put_json("key", {"value": 1})
    assert client.last_put["ServerSideEncryption"] == "AES256"
    assert await store.get_json("key") == {"value": 1}
    assert await store.get_json("missing") is None

    client.unreadable_body = True
    with pytest.raises(RuntimeError, match="readable body"):
        await store.get_json("key")

    with pytest.raises(ValueError, match="bucket_name"):
        S3JsonStore(cast(Boto3S3Client, client), bucket_name="")


@pytest.mark.asyncio
async def test_json_store_delete_errors_and_pagination_validation() -> None:
    client = EdgeS3Client()
    store = S3JsonStore(cast(Boto3S3Client, client), bucket_name="bucket")
    client.delete_error = True
    with pytest.raises(RuntimeError, match="delete"):
        await store.delete_keys(("a", "b"))

    client.pages = [
        {
            "Contents": [{"Key": "a"}, {"Key": 42}, "invalid"],
            "IsTruncated": True,
        }
    ]
    with pytest.raises(RuntimeError, match="continuation token"):
        await store.list_keys("prefix")

    client.pages = [
        {
            "Contents": [{"Key": "a"}],
            "IsTruncated": True,
            "NextContinuationToken": "next",
        },
        {"Contents": [{"Key": "b"}], "IsTruncated": False},
    ]
    assert await store.list_keys("prefix") == ("a", "b")


def test_s3_key_components_round_trip_and_reject_empty_values() -> None:
    encoded = _component("tenant/a")
    assert _decode_component(encoded) == "tenant/a"
    with pytest.raises(ValueError, match="must not be empty"):
        _component("")


@pytest.mark.asyncio
async def test_chunk_store_validates_batches_payloads_and_manifests() -> None:
    client = EdgeS3Client()
    store = S3JsonStore(cast(Boto3S3Client, client), bucket_name="bucket")
    chunks = S3ChunkStore(store)

    await chunks.put_batch(())
    with pytest.raises(ValueError, match="one tenant"):
        await chunks.put_batch((_chunk(), _chunk(document_id="document-b")))

    with pytest.raises(ValueError, match="allowedRoles"):
        _chunk_from_payload({"tenantId": "tenant-a"})

    manifest_key = store.key(
        "chunk-manifests",
        "tenant-a",
        "document-a",
        "generation-a",
    )
    await store.put_json(
        manifest_key,
        {
            "tenantId": "tenant-b",
            "documentId": "document-a",
            "generationId": "generation-a",
            "keys": [],
        },
    )
    with pytest.raises(ValueError, match="identity"):
        await chunks.delete_generation(
            TenantId("tenant-a"),
            DocumentId("document-a"),
            "generation-a",
        )

    await store.put_json(
        manifest_key,
        {
            "tenantId": "tenant-a",
            "documentId": "document-a",
            "generationId": "generation-a",
            "keys": [42],
        },
    )
    with pytest.raises(ValueError, match="keys"):
        await chunks.delete_generation(
            TenantId("tenant-a"),
            DocumentId("document-a"),
            "generation-a",
        )


@pytest.mark.asyncio
async def test_chunk_store_delete_document_and_fallback_generation_listing() -> None:
    client = EdgeS3Client()
    store = S3JsonStore(cast(Boto3S3Client, client), bucket_name="bucket")
    chunks = S3ChunkStore(store)
    await chunks.put_batch((_chunk(generation_id="generation-a"),))
    await chunks.put_batch((_chunk(generation_id="generation-b", chunk_id="chunk-b"),))
    await chunks.delete_document(TenantId("tenant-a"), DocumentId("document-a"))
    assert client.objects == {}

    chunk_key = store.key("chunks", "tenant-a", "generation-c", "chunk-c")
    await store.put_json(chunk_key, {"orphan": True})
    await chunks.delete_generation(
        TenantId("tenant-a"),
        DocumentId("document-a"),
        "generation-c",
    )
    assert chunk_key not in client.objects


@pytest.mark.asyncio
async def test_vector_manifest_rejects_invalid_payloads_and_deletes() -> None:
    client = EdgeS3Client()
    store = S3JsonStore(cast(Boto3S3Client, client), bucket_name="bucket")
    manifests = S3VectorKeyManifestStore(store)
    key = store.key("vector-manifests", "tenant-a", "document-a", "generation-a")

    await store.put_json(key, ["invalid"])
    with pytest.raises(ValueError, match="JSON object"):
        await manifests.load_keys(TenantId("tenant-a"), DocumentId("document-a"), "generation-a")

    await store.put_json(
        key,
        {
            "tenantId": "tenant-b",
            "documentId": "document-a",
            "generationId": "generation-a",
            "keys": [],
        },
    )
    with pytest.raises(ValueError, match="identity"):
        await manifests.load_keys(TenantId("tenant-a"), DocumentId("document-a"), "generation-a")

    await store.put_json(
        key,
        {
            "tenantId": "tenant-a",
            "documentId": "document-a",
            "generationId": "generation-a",
            "keys": [1],
        },
    )
    with pytest.raises(ValueError, match="keys"):
        await manifests.load_keys(TenantId("tenant-a"), DocumentId("document-a"), "generation-a")

    await manifests.delete_generation(
        TenantId("tenant-a"), DocumentId("document-a"), "generation-a"
    )
    assert (
        await manifests.load_keys(TenantId("tenant-a"), DocumentId("document-a"), "generation-a")
        == ()
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"vector_bucket_name": "bucket"},
        {"index_name": "index"},
        {
            "vector_bucket_name": "bucket",
            "index_name": "index",
            "index_arn": "arn:test:index",
        },
    ],
)
def test_vector_index_settings_require_one_unambiguous_reference(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        S3VectorIndexSettings.model_validate(kwargs)


def test_vector_index_settings_support_arn_reference() -> None:
    settings = S3VectorIndexSettings(index_arn="arn:test:index")
    assert settings.request_reference() == {"indexArn": "arn:test:index"}


@pytest.mark.asyncio
async def test_vector_repository_deletes_all_document_generations() -> None:
    client = EdgeVectorsClient()
    manifests = EdgeManifestStore()
    manifests.values[("tenant-a", "document-a", "generation-a")] = ("a", "b")
    manifests.values[("tenant-a", "document-a", "generation-b")] = ("c",)
    repository = _vector_repository(client, manifests)

    await repository.delete_document(TenantId("tenant-a"), DocumentId("document-a"))

    assert client.deleted == [["a", "b"], ["c"]]
    assert len(manifests.deleted) == 2


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"embedding_dimensions": 3}, "dimensions"),
        ({"vector": (float("inf"), 0.0)}, "non-finite"),
        ({"pipeline_version": None}, "immutable"),
    ],
)
@pytest.mark.asyncio
async def test_vector_repository_rejects_invalid_records(
    updates: dict[str, object],
    message: str,
) -> None:
    repository = _vector_repository(EdgeVectorsClient(), EdgeManifestStore())
    with pytest.raises(ValueError, match=message):
        await repository.upsert((_record(**updates),))


@pytest.mark.parametrize(
    "response",
    [
        {"distanceMetric": "dot", "vectors": []},
        {"distanceMetric": "cosine", "vectors": "invalid"},
        {"distanceMetric": "cosine", "vectors": ["invalid"]},
        {
            "distanceMetric": "cosine",
            "vectors": [{"distance": 0.1, "metadata": "invalid"}],
        },
        {
            "distanceMetric": "cosine",
            "vectors": [{"distance": True, "metadata": {}}],
        },
        {
            "distanceMetric": "cosine",
            "vectors": [
                {
                    "distance": 0.1,
                    "metadata": {
                        "tenantId": "tenant-a",
                        "documentId": "document-a",
                        "chunkId": "chunk-a",
                        "generationId": "generation-a",
                        "classification": "internal",
                        "allowedRoles": "user",
                    },
                }
            ],
        },
    ],
)
@pytest.mark.asyncio
async def test_vector_query_rejects_invalid_service_responses(
    response: dict[str, object],
) -> None:
    client = EdgeVectorsClient()
    client.query_response = response
    repository = _vector_repository(client, EdgeManifestStore())
    with pytest.raises(InvalidS3VectorResponseError):
        await repository.query(_query())


@pytest.mark.parametrize(
    "metadata_update",
    [
        {"tenantId": "tenant-b"},
        {"classification": "confidential"},
        {"allowedRoles": ["support"]},
        {"generationId": "candidate"},
    ],
)
@pytest.mark.asyncio
async def test_vector_query_defensively_drops_unauthorized_matches(
    metadata_update: dict[str, object],
) -> None:
    metadata: dict[str, object] = {
        "tenantId": "tenant-a",
        "documentId": "document-a",
        "chunkId": "chunk-a",
        "generationId": "generation-a",
        "classification": "internal",
        "allowedRoles": ["user"],
    }
    metadata.update(metadata_update)
    client = EdgeVectorsClient()
    client.query_response = {
        "distanceMetric": "cosine",
        "vectors": [{"distance": 0.1, "metadata": metadata}],
    }
    repository = _vector_repository(client, EdgeManifestStore())
    assert await repository.query(_query()) == ()


def test_vector_metadata_and_distance_helpers_reject_invalid_values() -> None:
    with pytest.raises(InvalidS3VectorResponseError, match="missing"):
        _required_metadata_string({}, "tenantId")
    with pytest.raises(InvalidS3VectorResponseError, match="distance"):
        _distance_score(-1.0, "cosine")
    assert _distance_score(1.0, "euclidean") == 0.5
