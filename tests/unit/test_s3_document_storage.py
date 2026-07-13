import io
import json
from datetime import UTC, datetime
from typing import cast

import pytest
from ia_aws_clients.s3_documents import (
    Boto3S3Client,
    S3ChunkStore,
    S3JsonStore,
    S3VectorKeyManifestStore,
)
from ia_domain import (
    ChunkId,
    Classification,
    DocumentChunk,
    DocumentId,
    Role,
    TenantId,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class MissingKeyError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("missing")
        self.response: dict[str, object] = {"Error": {"Code": "NoSuchKey"}}


class RecordingS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_key_suffix: str | None = None

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("put", kwargs))
        key = str(kwargs["Key"])
        if self.fail_key_suffix is not None and key.endswith(self.fail_key_suffix):
            raise RuntimeError("write failed")
        body = kwargs["Body"]
        if not isinstance(body, bytes):
            raise TypeError("Body must be bytes")
        self.objects[key] = body
        return {}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise MissingKeyError
        return {"Body": io.BytesIO(self.objects[key])}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.objects.pop(str(kwargs["Key"]), None)
        return {}

    def delete_objects(self, **kwargs: object) -> dict[str, object]:
        delete = kwargs["Delete"]
        if not isinstance(delete, dict):
            raise TypeError("Delete must be a mapping")
        raw_objects = delete.get("Objects")
        if not isinstance(raw_objects, list):
            raise TypeError("Objects must be a list")
        for item in raw_objects:
            if isinstance(item, dict):
                self.objects.pop(str(item["Key"]), None)
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        prefix = str(kwargs["Prefix"])
        return {
            "Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)],
            "IsTruncated": False,
        }


def _chunk(chunk_id: str) -> DocumentChunk:
    return DocumentChunk(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        chunk_id=ChunkId(chunk_id),
        generation_id="generation-a",
        source_version="v1",
        source_uri="s3://source/document",
        title="Policy",
        section="Refunds",
        language="fr",
        classification=Classification.CONFIDENTIAL,
        allowed_roles=frozenset({Role.SUPPORT}),
        checksum="a" * 64,
        content=f"content-{chunk_id}",
        created_at=NOW,
        end_offset=10,
        chunking_version="paragraph-v1",
    )


@pytest.mark.asyncio
async def test_chunk_store_round_trip_and_generation_cleanup() -> None:
    client = RecordingS3Client()
    store = S3JsonStore(
        cast(Boto3S3Client, client),
        bucket_name="bucket",
        prefix="rag",
        kms_key_id="kms-id",
    )
    chunks = S3ChunkStore(store)
    values = (_chunk("chunk-a"), _chunk("chunk-b"))

    await chunks.put_batch(values)
    loaded = await chunks.get(
        TenantId("tenant-a"),
        "generation-a",
        ChunkId("chunk-a"),
    )

    assert loaded == values[0]
    first_put = next(kwargs for action, kwargs in client.calls if action == "put")
    assert first_put["ServerSideEncryption"] == "aws:kms"
    assert first_put["SSEKMSKeyId"] == "kms-id"
    await chunks.delete_generation(
        TenantId("tenant-a"),
        DocumentId("document-a"),
        "generation-a",
    )
    assert client.objects == {}


@pytest.mark.asyncio
async def test_manifest_is_written_before_chunk_objects_for_rollback() -> None:
    client = RecordingS3Client()
    store = S3JsonStore(cast(Boto3S3Client, client), bucket_name="bucket")
    chunks = S3ChunkStore(store, max_concurrency=1)
    values = (_chunk("chunk-a"), _chunk("chunk-b"))
    client.fail_key_suffix = "Y2h1bmstYg"

    with pytest.raises(RuntimeError, match="write failed"):
        await chunks.put_batch(values)

    put_keys = [str(kwargs["Key"]) for action, kwargs in client.calls if action == "put"]
    first_payload = json.loads(client.objects[put_keys[0]])
    assert isinstance(first_payload, dict)
    assert "keys" in first_payload
    await chunks.delete_generation(
        TenantId("tenant-a"),
        DocumentId("document-a"),
        "generation-a",
    )
    assert client.objects == {}


@pytest.mark.asyncio
async def test_vector_manifest_merges_keys_and_lists_tenant_scoped_generations() -> None:
    client = RecordingS3Client()
    manifests = S3VectorKeyManifestStore(
        S3JsonStore(cast(Boto3S3Client, client), bucket_name="bucket")
    )
    await manifests.record_keys(
        TenantId("tenant-a"),
        DocumentId("document-a"),
        "generation-a",
        ("v1", "v2"),
    )
    await manifests.record_keys(
        TenantId("tenant-a"),
        DocumentId("document-a"),
        "generation-a",
        ("v2", "v3"),
    )

    assert await manifests.load_keys(
        TenantId("tenant-a"),
        DocumentId("document-a"),
        "generation-a",
    ) == ("v1", "v2", "v3")
    assert await manifests.list_document_generations(
        TenantId("tenant-a"),
        DocumentId("document-a"),
    ) == ("generation-a",)
