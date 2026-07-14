import asyncio
import base64
from collections.abc import Mapping
from datetime import datetime
from typing import cast
from urllib.parse import urlencode

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

_MAX_UPLOAD_TTL_SECONDS = 900


class S3DocumentSourceStore(DocumentSourceStore):
    def __init__(
        self,
        client: Boto3S3Client,
        *,
        bucket_name: str,
        prefix: str = "documents",
        kms_key_id: str | None = None,
        max_source_bytes: int = 10_000_000,
        temporary_upload_lifecycle_rule_id: str | None = None,
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
        self._temporary_upload_lifecycle_rule_id = temporary_upload_lifecycle_rule_id
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
        temporary_upload_lifecycle_rule_id: str | None = None,
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
            temporary_upload_lifecycle_rule_id=temporary_upload_lifecycle_rule_id,
        )

    def source_uri(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> str:
        key = self._source_key(tenant_id, document_id, source_version)
        return f"s3://{self._bucket_name}/{key}"

    async def create_upload(
        self,
        document: Document,
        *,
        upload_session_id: str,
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
        if ttl_seconds > _MAX_UPLOAD_TTL_SECONDS:
            msg = "source upload expiration exceeds 15 minutes"
            raise ValueError(msg)
        checksum_base64 = base64.b64encode(bytes.fromhex(document.source_checksum)).decode("ascii")
        tagging = urlencode(
            {
                "ia-temporary-upload": "true",
                "ia-upload-session": upload_session_id,
            }
        )
        params: dict[str, object] = {
            "Bucket": self._bucket_name,
            "Key": self._temporary_key(
                document.tenant_id,
                document.document_id,
                upload_session_id,
            ),
            "ContentType": document.content_type,
            "ContentLength": size_bytes,
            "ChecksumSHA256": checksum_base64,
            "Tagging": tagging,
        }
        headers = {
            "content-type": document.content_type,
            "content-length": str(size_bytes),
            "x-amz-checksum-sha256": checksum_base64,
            "x-amz-tagging": tagging,
        }
        params.update(self._encryption())
        headers.update(self._encryption_headers())
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
            upload_session_id=upload_session_id,
            url=url,
            headers=headers,
            expires_at=expires_at,
        )

    async def promote_upload(
        self,
        document: Document,
        upload_session_id: str,
    ) -> DocumentSourceMetadata:
        temporary_key = self._temporary_key(
            document.tenant_id,
            document.document_id,
            upload_session_id,
        )
        try:
            response = await asyncio.to_thread(
                self._client.head_object,
                Bucket=self._bucket_name,
                Key=temporary_key,
                ChecksumMode="ENABLED",
            )
        except Exception as error:
            if _aws_error_code(error) in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError("temporary document upload was not found") from error
            raise
        metadata = self._metadata(response)
        if metadata.content_type != document.content_type:
            raise ValueError("temporary upload content type does not match the document")
        if metadata.size_bytes > self._max_source_bytes:
            raise ValueError("temporary upload exceeds configured limits")
        if metadata.checksum_sha256 != document.source_checksum:
            raise ValueError("temporary upload checksum does not match the document")
        etag = response.get("ETag")
        if not isinstance(etag, str) or not etag:
            msg = "S3 temporary upload is missing ETag"
            raise RuntimeError(msg)
        await asyncio.to_thread(
            self._client.copy_object,
            Bucket=self._bucket_name,
            Key=self._source_key(
                document.tenant_id,
                document.document_id,
                document.source_version,
            ),
            CopySource={"Bucket": self._bucket_name, "Key": temporary_key},
            CopySourceIfMatch=etag,
            MetadataDirective="COPY",
            TaggingDirective="REPLACE",
            Tagging="ia-temporary-upload=false",
            ChecksumAlgorithm="SHA256",
            **self._encryption(),
        )
        await asyncio.to_thread(
            self._client.delete_object,
            Bucket=self._bucket_name,
            Key=temporary_key,
        )
        return await self.inspect(document)

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
        return self._metadata(response)

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
        for prefix in (
            self._source_document_prefix(tenant_id, document_id),
            self._temporary_document_prefix(tenant_id, document_id),
        ):
            keys = await self._keys.list_keys(prefix)
            await self._keys.delete_keys(keys)

    async def is_ready(self) -> bool:
        try:
            await asyncio.to_thread(
                self._client.head_bucket,
                Bucket=self._bucket_name,
            )
            if self._temporary_upload_lifecycle_rule_id is None:
                return False
            response = await asyncio.to_thread(
                self._client.get_bucket_lifecycle_configuration,
                Bucket=self._bucket_name,
            )
        except Exception:
            return False
        rules = response.get("Rules")
        if not isinstance(rules, list):
            return False
        expected_prefix = self._keys.key("uploads") + "/"
        return any(
            self._valid_lifecycle_rule(rule, expected_prefix)
            for rule in rules
            if isinstance(rule, Mapping)
        )

    def _valid_lifecycle_rule(
        self,
        rule: Mapping[object, object],
        expected_prefix: str,
    ) -> bool:
        if (
            rule.get("ID") != self._temporary_upload_lifecycle_rule_id
            or rule.get("Status") != "Enabled"
        ):
            return False
        prefix = rule.get("Prefix")
        filter_value = rule.get("Filter")
        if isinstance(filter_value, Mapping):
            prefix = filter_value.get("Prefix", prefix)
        expiration = rule.get("Expiration")
        return (
            prefix == expected_prefix
            and isinstance(expiration, Mapping)
            and expiration.get("Days") == 1
        )

    @staticmethod
    def _metadata(response: Mapping[str, object]) -> DocumentSourceMetadata:
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

    def _encryption(self) -> dict[str, object]:
        if self._kms_key_id is None:
            return {"ServerSideEncryption": "AES256"}
        return {
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self._kms_key_id,
        }

    def _encryption_headers(self) -> dict[str, str]:
        if self._kms_key_id is None:
            return {"x-amz-server-side-encryption": "AES256"}
        return {
            "x-amz-server-side-encryption": "aws:kms",
            "x-amz-server-side-encryption-aws-kms-key-id": self._kms_key_id,
        }

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

    def _temporary_key(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        upload_session_id: str,
    ) -> str:
        return self._keys.key(
            "uploads",
            str(tenant_id),
            str(document_id),
            upload_session_id,
            "original",
        )

    def _source_document_prefix(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> str:
        return self._keys.key("sources", str(tenant_id), str(document_id)) + "/"

    def _temporary_document_prefix(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> str:
        return self._keys.key("uploads", str(tenant_id), str(document_id)) + "/"
