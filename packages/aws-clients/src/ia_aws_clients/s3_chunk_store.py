import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from ia_application import ChunkStore
from ia_domain import (
    ChunkId,
    Classification,
    DocumentChunk,
    DocumentId,
    Role,
    TenantId,
)

from ia_aws_clients.s3_json_store import S3JsonStore, _decode_component


def _chunk_payload(chunk: DocumentChunk) -> dict[str, object]:
    return {
        "tenantId": str(chunk.tenant_id),
        "documentId": str(chunk.document_id),
        "chunkId": str(chunk.chunk_id),
        "generationId": chunk.generation_id,
        "sourceVersion": chunk.source_version,
        "sourceUri": chunk.source_uri,
        "title": chunk.title,
        "section": chunk.section,
        "language": chunk.language,
        "classification": chunk.classification.value,
        "allowedRoles": sorted(role.value for role in chunk.allowed_roles),
        "checksum": chunk.checksum,
        "content": chunk.content,
        "createdAt": chunk.created_at.isoformat(),
        "sequence": chunk.sequence,
        "startOffset": chunk.start_offset,
        "endOffset": chunk.end_offset,
        "chunkingVersion": chunk.chunking_version,
        "pageNumber": chunk.page_number,
    }


def _chunk_from_payload(value: object) -> DocumentChunk:
    if not isinstance(value, Mapping):
        msg = "stored chunk must be a JSON object"
        raise ValueError(msg)
    allowed_roles = value.get("allowedRoles")
    if not isinstance(allowed_roles, list):
        msg = "stored chunk allowedRoles must be a list"
        raise ValueError(msg)
    created_at = value.get("createdAt")
    if not isinstance(created_at, str):
        msg = "stored chunk createdAt must be a string"
        raise ValueError(msg)
    return DocumentChunk(
        tenant_id=TenantId(_required_string(value, "tenantId")),
        document_id=DocumentId(_required_string(value, "documentId")),
        chunk_id=ChunkId(_required_string(value, "chunkId")),
        generation_id=_required_string(value, "generationId"),
        source_version=_required_string(value, "sourceVersion"),
        source_uri=_required_string(value, "sourceUri"),
        title=_required_string(value, "title"),
        section=_required_string(value, "section"),
        language=_required_string(value, "language"),
        classification=Classification(_required_string(value, "classification")),
        allowed_roles=frozenset(Role(str(role)) for role in allowed_roles),
        checksum=_required_string(value, "checksum"),
        content=_required_string(value, "content"),
        created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
        sequence=_required_int(value, "sequence"),
        start_offset=_required_int(value, "startOffset"),
        end_offset=_optional_int(value.get("endOffset"), "endOffset"),
        chunking_version=_required_string(value, "chunkingVersion"),
        page_number=_optional_int(value.get("pageNumber"), "pageNumber"),
    )


def _required_string(value: Mapping[str, object], field_name: str) -> str:
    item = value.get(field_name)
    if not isinstance(item, str) or not item:
        msg = f"stored chunk field must be a non-empty string: {field_name}"
        raise ValueError(msg)
    return item


def _required_int(value: Mapping[str, object], field_name: str) -> int:
    item = value.get(field_name)
    if isinstance(item, bool) or not isinstance(item, int):
        msg = f"stored chunk field must be an integer: {field_name}"
        raise ValueError(msg)
    return item


def _optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"stored chunk field must be an integer: {field_name}"
        raise ValueError(msg)
    return value


class S3ChunkStore(ChunkStore):
    def __init__(
        self,
        store: S3JsonStore,
        *,
        max_concurrency: int = 8,
    ) -> None:
        if max_concurrency <= 0:
            msg = "max_concurrency must be positive"
            raise ValueError(msg)
        self._store = store
        self._max_concurrency = max_concurrency

    async def put_batch(self, chunks: Sequence[DocumentChunk]) -> None:
        if not chunks:
            return
        first = chunks[0]
        if any(
            chunk.tenant_id != first.tenant_id
            or chunk.document_id != first.document_id
            or chunk.generation_id != first.generation_id
            for chunk in chunks
        ):
            msg = "one chunk batch must belong to one tenant, document, and generation"
            raise ValueError(msg)
        keys = tuple(self._chunk_key(chunk) for chunk in chunks)
        await self._store.put_json(
            self._manifest_key(
                first.tenant_id,
                first.document_id,
                first.generation_id,
            ),
            {
                "tenantId": str(first.tenant_id),
                "documentId": str(first.document_id),
                "generationId": first.generation_id,
                "keys": list(keys),
            },
        )
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def put(chunk: DocumentChunk, key: str) -> None:
            async with semaphore:
                await self._store.put_json(key, _chunk_payload(chunk))

        await asyncio.gather(*(put(chunk, key) for chunk, key in zip(chunks, keys, strict=True)))

    async def get(
        self,
        tenant_id: TenantId,
        generation_id: str,
        chunk_id: ChunkId,
    ) -> DocumentChunk | None:
        payload = await self._store.get_json(
            self._store.key("chunks", str(tenant_id), generation_id, str(chunk_id))
        )
        if payload is None:
            return None
        chunk = _chunk_from_payload(payload)
        if (
            chunk.tenant_id != tenant_id
            or chunk.generation_id != generation_id
            or chunk.chunk_id != chunk_id
        ):
            return None
        return chunk

    async def delete_document(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
    ) -> None:
        prefix = self._store.key("chunk-manifests", str(tenant_id), str(document_id)) + "/"
        manifest_keys = await self._store.list_keys(prefix)
        for manifest_key in manifest_keys:
            generation_component = manifest_key.rsplit("/", 1)[-1]
            generation_id = _decode_component(generation_component)
            await self.delete_generation(tenant_id, document_id, generation_id)

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        manifest_key = self._manifest_key(tenant_id, document_id, generation_id)
        manifest = await self._store.get_json(manifest_key)
        keys: tuple[str, ...]
        if isinstance(manifest, Mapping):
            if (
                manifest.get("tenantId") != str(tenant_id)
                or manifest.get("documentId") != str(document_id)
                or manifest.get("generationId") != generation_id
            ):
                msg = "chunk manifest identity does not match the requested generation"
                raise ValueError(msg)
            raw_keys = manifest.get("keys")
            if not isinstance(raw_keys, list) or not all(isinstance(key, str) for key in raw_keys):
                msg = "chunk manifest keys are invalid"
                raise ValueError(msg)
            keys = tuple(cast(str, key) for key in raw_keys)
        else:
            prefix = self._store.key("chunks", str(tenant_id), generation_id) + "/"
            keys = await self._store.list_keys(prefix)
        await self._store.delete_keys(keys)
        await self._store.delete_key(manifest_key)

    def _chunk_key(self, chunk: DocumentChunk) -> str:
        return self._store.key(
            "chunks",
            str(chunk.tenant_id),
            chunk.generation_id,
            str(chunk.chunk_id),
        )

    def _manifest_key(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> str:
        return self._store.key(
            "chunk-manifests",
            str(tenant_id),
            str(document_id),
            generation_id,
        )
