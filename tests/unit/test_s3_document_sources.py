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

    def copy_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("copy", kwargs))
        self.keys.add(str(kwargs["Key"]))
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
            "ETag": '"etag-a"',
        }

    def head_bucket(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("head-bucket", kwargs))
        return {}

    def get_bucket_lifecycle_configuration(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("lifecycle", kwargs))
        return {
            "Rules": [
                {
                    "ID": "expire-temp-uploads",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "sources/dXBsb2Fkcw/"},
                    "Expiration": {"Days": 1},
                }
            ]
        }

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("delete", kwargs))
        self.keys.discard(str(kwargs["Key"]))
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


def _store(client: RecordingS3Client) -> S3DocumentSourceStore:
    return S3DocumentSourceStore(
        cast(Boto3S3Client, client),
        bucket_name="private-documents",
        prefix="sources",
        kms_key_id="kms-key",
        max_source_bytes=1_000,
        temporary_upload_lifecycle_rule_id="expire-temp-uploads",
    )


@pytest.mark.asyncio
async def test_presigned_upload_targets_temporary_session_key() -> None:
    client = RecordingS3Client()
    store = _store(client)
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    upload = await store.create_upload(
        _document(),
        upload_session_id="upload-a",
        size_bytes=8,
        expires_at=expires_at,
    )

    assert upload.upload_session_id == "upload-a"
    assert upload.headers["content-length"] == "8"
    assert upload.headers["x-amz-server-side-encryption"] == "aws:kms"
    assert "ia-temporary-upload=true" in upload.headers["x-amz-tagging"]
    _, kwargs = client.calls[-1]
    params = kwargs["Params"]
    assert isinstance(params, dict)
    key = str(params["Key"])
    assert "tenant/a" not in key
    assert "document/a" not in key
    assert key != store.source_uri(
        TenantId("tenant/a"),
        DocumentId("document/a"),
        "version-a",
    ).split("/", 3)[-1]


@pytest.mark.asyncio
async def test_promotion_copies_validated_upload_to_immutable_source() -> None:
    client = RecordingS3Client()
    store = _store(client)
    document = _document()

    metadata = await store.promote_upload(document, "upload-a")

    assert metadata.checksum_sha256 == CHECKSUM
    copy_calls = [kwargs for action, kwargs in client.calls if action == "copy"]
    assert len(copy_calls) == 1
    assert copy_calls[0]["CopySourceIfMatch"] == '"etag-a"'
    assert copy_calls[0]["Key"] == store.source_uri(
        document.tenant_id,
        document.document_id,
        document.source_version,
    ).split("/", 3)[-1]
    assert any(action == "delete" for action, _ in client.calls)


@pytest.mark.asyncio
async def test_source_read_cleanup_and_lifecycle_readiness() -> None:
    client = RecordingS3Client()
    store = _store(client)
    document = _document()
    client.keys.update(
        {
            store.source_uri(
                document.tenant_id,
                document.document_id,
                document.source_version,
            ).split("/", 3)[-1],
            "sources/dXBsb2Fkcw/dGVuYW50L2E/ZG9jdW1lbnQvYQ/dXBsb2FkLWE/b3JpZ2luYWw",
        }
    )

    payload = await store.read(document, max_bytes=100)
    ready = await store.is_ready()
    await store.delete_document(document.tenant_id, document.document_id)

    assert payload == b"document"
    assert ready is True
    assert [action for action, _ in client.calls].count("list") == 2
    assert [action for action, _ in client.calls].count("delete-many") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size_bytes", "minutes", "message"),
    ((11, 5, "limits"), (8, 16, "15 minutes")),
)
async def test_upload_rejects_invalid_limits(
    size_bytes: int,
    minutes: int,
    message: str,
) -> None:
    store = S3DocumentSourceStore(
        cast(Boto3S3Client, RecordingS3Client()),
        bucket_name="private-documents",
        max_source_bytes=10,
    )
    with pytest.raises(ValueError, match=message):
        await store.create_upload(
            _document(),
            upload_session_id="upload-a",
            size_bytes=size_bytes,
            expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
        )
