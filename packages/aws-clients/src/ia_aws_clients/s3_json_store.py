import asyncio
import base64
import json
from collections.abc import Mapping, Sequence
from typing import Protocol, cast


class S3Body(Protocol):
    def read(self) -> bytes: ...


class Boto3S3Client(Protocol):
    def put_object(self, **kwargs: object) -> dict[str, object]: ...

    def get_object(self, **kwargs: object) -> dict[str, object]: ...

    def delete_object(self, **kwargs: object) -> dict[str, object]: ...

    def delete_objects(self, **kwargs: object) -> dict[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]: ...


class S3JsonStore:
    def __init__(
        self,
        client: Boto3S3Client,
        *,
        bucket_name: str,
        prefix: str = "rag",
        kms_key_id: str | None = None,
    ) -> None:
        if not bucket_name:
            msg = "bucket_name must not be empty"
            raise ValueError(msg)
        self._client = client
        self._bucket_name = bucket_name
        self._prefix = prefix.strip("/")
        self._kms_key_id = kms_key_id

    @classmethod
    def from_bucket_name(
        cls,
        bucket_name: str,
        *,
        prefix: str = "rag",
        kms_key_id: str | None = None,
        region_name: str | None = None,
    ) -> "S3JsonStore":
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("s3", region_name=region_name)
        return cls(
            cast(Boto3S3Client, client),
            bucket_name=bucket_name,
            prefix=prefix,
            kms_key_id=kms_key_id,
        )

    async def put_json(self, key: str, value: object) -> None:
        body = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encryption: dict[str, object]
        if self._kms_key_id is None:
            encryption = {"ServerSideEncryption": "AES256"}
        else:
            encryption = {
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": self._kms_key_id,
            }
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket_name,
            Key=key,
            Body=body,
            ContentType="application/json",
            **encryption,
        )

    async def get_json(self, key: str) -> object | None:
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._bucket_name,
                Key=key,
            )
        except Exception as error:
            if _aws_error_code(error) in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        raw_body = response.get("Body")
        if isinstance(raw_body, bytes):
            payload = raw_body
        elif raw_body is not None and hasattr(raw_body, "read"):
            payload = cast(S3Body, raw_body).read()
        else:
            msg = "S3 response does not contain a readable body"
            raise RuntimeError(msg)
        return cast(object, json.loads(payload))

    async def delete_key(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object,
            Bucket=self._bucket_name,
            Key=key,
        )

    async def delete_keys(self, keys: Sequence[str]) -> None:
        for offset in range(0, len(keys), 1000):
            batch = keys[offset : offset + 1000]
            if not batch:
                continue
            response = await asyncio.to_thread(
                self._client.delete_objects,
                Bucket=self._bucket_name,
                Delete={
                    "Objects": [{"Key": key} for key in batch],
                    "Quiet": True,
                },
            )
            errors = response.get("Errors")
            if isinstance(errors, list) and errors:
                msg = "S3 failed to delete one or more generation objects"
                raise RuntimeError(msg)

    async def list_keys(self, prefix: str) -> tuple[str, ...]:
        keys: list[str] = []
        continuation_token: str | None = None
        while True:
            kwargs: dict[str, object] = {
                "Bucket": self._bucket_name,
                "Prefix": prefix,
            }
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            response = await asyncio.to_thread(self._client.list_objects_v2, **kwargs)
            contents = response.get("Contents", [])
            if isinstance(contents, list):
                for item in contents:
                    if isinstance(item, Mapping) and isinstance(item.get("Key"), str):
                        keys.append(cast(str, item["Key"]))
            if not bool(response.get("IsTruncated")):
                break
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token:
                msg = "S3 pagination response is missing its continuation token"
                raise RuntimeError(msg)
            continuation_token = token
        return tuple(keys)

    def key(self, *parts: str) -> str:
        encoded = "/".join(_component(part) for part in parts)
        return f"{self._prefix}/{encoded}" if self._prefix else encoded


def _component(value: str) -> str:
    if not value:
        msg = "S3 key component must not be empty"
        raise ValueError(msg)
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_component(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")


def _aws_error_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        error_payload = response.get("Error")
        if isinstance(error_payload, Mapping):
            code = error_payload.get("Code")
            if isinstance(code, str):
                return code
    return ""
