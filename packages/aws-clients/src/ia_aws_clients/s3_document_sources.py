import asyncio
import base64
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from ia_application import (
    DocumentSourceMetadata,
    DocumentSourceStore,
    PresignedSourceUpload,
)
from ia_domain import Document, DocumentId, TenantId

from ia_aws_clients.s3_json_store import (
    Boto3S3Client,
    S3Body,
    S3JsonStore,
    _aws_error_code,
)


class S3DocumentSourceStore(DocumentSourceStore):
    def __init__(
        self,
        client: Boto3S3Client,
        *,
        bucket_name: str,
        prefix: str = "documents",
        kms_key_id: str | None = None,
        max_source_bytes: int = 10_000_000,
    ) -> None:
        if not bucket_name:
            msg = "bucket_name must not be empty"
            raise ValueError(msg)
        if max_source_bytes <= 0:
            msg = "max_source_bytes must be positive"
            raise ValueError(msg)
        self._client = client
        self._bucket_name = bucket_name
        self._kms_key_id = kms_key_id
        self._max_source_bytes = max_source_bytes
        self._keys = S3JsonStore(
            client,
            bucket_name=bucket_name,
            prefix=prefix,
            kms_key_id=kms_key_id,
        )

    @classmethod
    def from_bucket_name(
        cls,
        bucket_name: str,
        *,
        prefix: str = "documents",
        kms_key_id: str | None = None,
        max_source_bytes: int = 10_000_000,
        region_name: str | None = None,
    ) -> "S3DocumentSourceStore":
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("s3", region_name=region_name)
        return cls(
            cast(Boto3S3Client, client),
            bucket_name=bucket_name,
            prefix=prefix,
            kms_key_id=kms_key_id,
            max_source_bytes=max_source_bytes,
        )

    def source_uri(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> str:
        return f"s3://{self._bucket_name}/{self._source_key(tenant_id, document_id, source_version)}"

    async def create_upload(
        self,
        document: Document,
        *,
        size_bytes: int,
        expires_at: datetime,
    ) -> PresignedSourceUpload:
        if size_bytes <= 0 or size_bytes > self._max_source_bytes:
            msg = "source upload size exceeds configured limits"
            raise ValueError(msg)
        now = datetime.now(expires_at.tzinfo)
        ttl_seconds = int((expires_at - now).total_seconds())
        if ttl_seconds <= 0:
            msg = "source upload expiration must be in the future"
            raise ValueError(msg)
        checksum_base64 = base64.b64encode(
            bytes.fromhex(document.source_checksum)
        ).decode("ascii")
        params: dict[str, object] = {
            "Bucket": self._bucket_name,
            "Key": self._source_key(
                document.tenant_id,
                document.document_id,
                document.source_version,
            ),
            "ContentType": document.content_type,
            "ContentLength": size_bytes,
            "ChecksumSHA256": checksum_base64,
        }
        headers = {
            "content-type": document.content_type,
            "content-length": str(size_bytes),
            "x-amz-checksum-sha256": checksum_base64,
        }
        if self._kms_key_id is None:
            params["ServerSideEncryption"] = "AES256"
            headers["x-amz-server-side-encryption"] = "AES256"
        else:
            params["ServerSideEncryption"] = "aws:kms"
            params["SSEKMSKeyId"] = self._kms_key_id
            headers["x-amz-server-side-encryption"] = "aws:kms"
            headers["x-amz-server-side-encryption-aws-kms-key-id"] = self._kms_key_id
        url = await asyncio.to_thread(
            self._client.generate_presigned_url,
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=ttl_seconds,
            HttpMethod="PUT",
        )
        if not isinstance(url, str) or not url:
            msg = "S3 did not return a presigned upload URL"
            raise RuntimeError(msg)
        return PresignedSourceUpload(
            url=url,
            headers=headers,
            expires_at=expires_at,
        )

    async def inspect(self, document: Document) -> DocumentSourceMetadata:
        try:
            response = await asyncio.to_thread(
                self._client.head_object,
                Bucket=self._bucket_name,
                Key=self._source_key(
                    document.tenant_id,
                    document.document_id,
                    document.source_version,
                ),
                ChecksumMode="ENABLED",
            )
        except Exception as error:
            if _aws_error_code(error) in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError("document source was not uploaded") from error
            raise
        content_type = response.get("ContentType")
        content_length = response.get("ContentLength")
        checksum = response.get("ChecksumSHA256")
        if not isinstance(content_type, str) or not content_type:
            msg = "S3 source metadata is missing ContentType"
            raise RuntimeError(msg)
        if isinstance(content_length, bool) or not isinstance(content_length, int):
            msg = "S3 source metadata is missing ContentLength"
            raise RuntimeError(msg)
        if not isinstance(checksum, str) or not checksum:
            msg = "S3 source metadata is missing ChecksumSHA256"
            raise RuntimeError(msg)
        try:
            checksum_hex = base64.b64decode(checksum, validate=True).hex()
        except ValueError as error:
            raise RuntimeError("S3 source checksum is malformed") from error
        return DocumentSourceMetadata(
            content_type=content_type,
            size_bytes=content_length,
            checksum_sha256=checksum_hex,
        )

    async def read(self, document: Document, *, max_bytes: int) -> bytes:
        effective_limit = min(max_bytes, self._max_source_bytes)
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._bucket_name,
                Key=self._source_key(
                    document.tenant_id,
                    document.document_id,
                    document.source_version,
                ),
                Range=f"bytes=0-{effective_limit}",
            )
        except Exception as error:
            if _aws_error_code(error) in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError("document source was not uploaded") from error
            raise
        raw_body = response.get("Body")
        if isinstance(raw_body, bytes):
            payload = raw_body
        elif raw_body is not None and hasattr(raw_body, "read"):
            payload = cast(S3Body, raw_body).read()
        else:
            msg = "S3 source response does not contain a readable body"
            raise RuntimeError(msg)
        if len(payload) > effective_limit:
            msg = "document source exceeds configured limits"
            raise ValueError(msg)
        return payload

    async def delete_document(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> None:
        prefix = self._document_prefix(tenant_id, document_id)
        keys = await self._keys.list_keys(prefix)
        await self._keys.delete_keys(keys)

    def _source_key(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> str:
        return self._keys.key(
            "sources",
            str(tenant_id),
            str(document_id),
            source_version,
            "original",
        )

    def _document_prefix(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> str:
        return self._keys.key("sources", str(tenant_id), str(document_id)) + "/"
