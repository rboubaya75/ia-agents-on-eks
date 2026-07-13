import base64
import io
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from ia_aws_clients.s3_document_sources import S3DocumentSourceStore
from ia_aws_clients.s3_json_store import Boto3S3Client
from ia_domain import (
    Classification,
    Document,
    DocumentId,
    DocumentStatus,
    Role,
    TenantId,
    UserId,
)

NOW = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
CHECKSUM = "a" * 64


class RecordingS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.body = b"document"
        self.keys: set[str] = set()

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("put", kwargs))
        return {}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get", kwargs))
        return {"Body": io.BytesIO(self.body)}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("head", kwargs))
        return {
            "ContentType": "text/plain",
            "ContentLength": len(self.body),
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(CHECKSUM)).decode("ascii"),
        }

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("delete", kwargs))
        return {}

    def delete_objects(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("delete-many", kwargs))
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("list", kwargs))
        prefix = str(kwargs["Prefix"])
        return {
            "Contents": [{"Key": key} for key in sorted(self.keys) if key.startswith(prefix)],
            "IsTruncated": False,
        }

    def generate_presigned_url(self, **kwargs: object) -> str:
        self.calls.append(("presign", kwargs))
        return "https://s3.example/upload"


def _document() -> Document:
    return Document(
        tenant_id=TenantId("tenant/a"),
        document_id=DocumentId("document/a"),
        owner_user_id=UserId("admin-a"),
        title="Policy",
        source_uri="s3://bucket/key",
        source_version="version-a",
        source_checksum=CHECKSUM,
        content_type="text/plain",
        language="fr",
        classification=Classification.INTERNAL,
        allowed_roles=frozenset({Role.USER}),
        status=DocumentStatus.PENDING_UPLOAD,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_presigned_upload_signs_identity_checksum_size_and_encryption() -> None:
    client = RecordingS3Client()
    store = S3DocumentSourceStore(
        cast(Boto3S3Client, client),
        bucket_name="private-documents",
        prefix="sources",
        kms_key_id="kms-key",
        max_source_bytes=1_000,
    )
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    upload = await store.create_upload(_document(), size_bytes=8, expires_at=expires_at)

    assert upload.url == "https://s3.example/upload"
    assert upload.headers["content-length"] == "8"
    assert upload.headers["x-amz-server-side-encryption"] == "aws:kms"
    _, kwargs = client.calls[-1]
    params = kwargs["Params"]
    assert isinstance(params, dict)
    assert params["Bucket"] == "private-documents"
    assert params["ContentType"] == "text/plain"
    assert params["ContentLength"] == 8
    key = str(params["Key"])
    assert "tenant/a" not in key
    assert "document/a" not in key


@pytest.mark.asyncio
async def test_source_inspection_read_and_tenant_scoped_cleanup() -> None:
    client = RecordingS3Client()
    store = S3DocumentSourceStore(
        cast(Boto3S3Client, client),
        bucket_name="private-documents",
        prefix="sources",
        max_source_bytes=1_000,
    )
    document = _document()
    source_key = store.source_uri(
        document.tenant_id,
        document.document_id,
        document.source_version,
    ).split("/", 3)[-1]
    client.keys.add(source_key)

    metadata = await store.inspect(document)
    payload = await store.read(document, max_bytes=100)
    await store.delete_document(document.tenant_id, document.document_id)

    assert metadata.content_type == "text/plain"
    assert metadata.checksum_sha256 == CHECKSUM
    assert payload == b"document"
    actions = [action for action, _ in client.calls]
    assert "head" in actions
    assert "get" in actions
    assert "list" in actions
    assert "delete-many" in actions


@pytest.mark.asyncio
async def test_upload_rejects_oversized_source() -> None:
    store = S3DocumentSourceStore(
        cast(Boto3S3Client, RecordingS3Client()),
        bucket_name="private-documents",
        max_source_bytes=10,
    )
    with pytest.raises(ValueError, match="limits"):
        await store.create_upload(
            _document(),
            size_bytes=11,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
