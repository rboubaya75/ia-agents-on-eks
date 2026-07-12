from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from ia_domain import DocumentId, TenantId

from ia_aws_clients.s3_json_store import S3JsonStore, _decode_component


class VectorKeyManifestStore(Protocol):
    async def record_keys(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
        keys: Sequence[str],
    ) -> None: ...

    async def load_keys(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> tuple[str, ...]: ...

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None: ...

    async def list_document_generations(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> tuple[str, ...]: ...


class S3VectorKeyManifestStore(VectorKeyManifestStore):
    def __init__(self, store: S3JsonStore) -> None:
        self._store = store

    async def record_keys(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
        keys: Sequence[str],
    ) -> None:
        existing = await self.load_keys(tenant_id, document_id, generation_id)
        merged = tuple(dict.fromkeys((*existing, *keys)))
        await self._store.put_json(
            self._manifest_key(tenant_id, document_id, generation_id),
            {
                "tenantId": str(tenant_id),
                "documentId": str(document_id),
                "generationId": generation_id,
                "keys": list(merged),
            },
        )

    async def load_keys(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> tuple[str, ...]:
        manifest = await self._store.get_json(
            self._manifest_key(tenant_id, document_id, generation_id)
        )
        if manifest is None:
            return ()
        if not isinstance(manifest, Mapping):
            msg = "vector manifest must be a JSON object"
            raise ValueError(msg)
        if (
            manifest.get("tenantId") != str(tenant_id)
            or manifest.get("documentId") != str(document_id)
            or manifest.get("generationId") != generation_id
        ):
            msg = "vector manifest identity does not match the requested generation"
            raise ValueError(msg)
        raw_keys = manifest.get("keys")
        if not isinstance(raw_keys, list) or not all(
            isinstance(key, str) for key in raw_keys
        ):
            msg = "vector manifest keys are invalid"
            raise ValueError(msg)
        return tuple(cast(str, key) for key in raw_keys)

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        await self._store.delete_key(
            self._manifest_key(tenant_id, document_id, generation_id)
        )

    async def list_document_generations(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> tuple[str, ...]:
        prefix = (
            self._store.key("vector-manifests", str(tenant_id), str(document_id)) + "/"
        )
        manifest_keys = await self._store.list_keys(prefix)
        return tuple(_decode_component(key.rsplit("/", 1)[-1]) for key in manifest_keys)

    def _manifest_key(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> str:
        return self._store.key(
            "vector-manifests",
            str(tenant_id),
            str(document_id),
            generation_id,
        )
