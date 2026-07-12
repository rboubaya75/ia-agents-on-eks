from collections.abc import Sequence

from ia_application import RepositoryConflictError, VectorMatch, VectorQuery, VectorRecord
from ia_domain import (
    ChunkId,
    Document,
    DocumentChunk,
    DocumentId,
    IndexGeneration,
    TenantId,
)


class InMemoryVectorRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[TenantId, str, ChunkId], VectorRecord] = {}

    @property
    def records(self) -> tuple[VectorRecord, ...]:
        return tuple(self._records.values())

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        for record in records:
            key = (record.tenant_id, record.generation_id, record.chunk_id)
            self._records[key] = record

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None:
        keys = [
            key
            for key, record in self._records.items()
            if record.tenant_id == tenant_id and record.document_id == document_id
        ]
        for key in keys:
            del self._records[key]

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        keys = [
            key
            for key, record in self._records.items()
            if record.tenant_id == tenant_id
            and record.document_id == document_id
            and record.generation_id == generation_id
        ]
        for key in keys:
            del self._records[key]

    async def delete_version(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> None:
        keys = [
            key
            for key, record in self._records.items()
            if record.tenant_id == tenant_id
            and record.document_id == document_id
            and record.source_version == source_version
        ]
        for key in keys:
            del self._records[key]

    async def query(self, query: VectorQuery) -> tuple[VectorMatch, ...]:
        matches: list[VectorMatch] = []
        for record in self._records.values():
            if record.tenant_id != query.tenant_id:
                continue
            if record.classification not in query.allowed_classifications:
                continue
            if record.allowed_roles.isdisjoint(query.allowed_roles):
                continue
            score = self._cosine_similarity(query.query_vector, record.vector)
            matches.append(
                VectorMatch(
                    tenant_id=record.tenant_id,
                    document_id=record.document_id,
                    chunk_id=record.chunk_id,
                    score=score,
                )
            )
        matches.sort(key=lambda item: item.score, reverse=True)
        return tuple(matches[: query.top_k])

    @staticmethod
    def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        if len(left) != len(right):
            msg = "vector dimensions must match"
            raise ValueError(msg)
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = sum(value * value for value in left) ** 0.5
        right_norm = sum(value * value for value in right) ** 0.5
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        raw_score = dot / (left_norm * right_norm)
        return float(max(0.0, min(1.0, raw_score)))


class InMemoryChunkStore:
    def __init__(self) -> None:
        self._chunks: dict[tuple[TenantId, str, ChunkId], DocumentChunk] = {}

    @property
    def chunks(self) -> tuple[DocumentChunk, ...]:
        return tuple(self._chunks.values())

    async def put(self, chunk: DocumentChunk) -> None:
        key = (chunk.tenant_id, chunk.generation_id, chunk.chunk_id)
        self._chunks[key] = chunk

    async def put_batch(self, chunks: Sequence[DocumentChunk]) -> None:
        for chunk in chunks:
            await self.put(chunk)

    async def get(
        self,
        tenant_id: TenantId,
        generation_id: str,
        chunk_id: ChunkId,
    ) -> DocumentChunk | None:
        return self._chunks.get((tenant_id, generation_id, chunk_id))

    async def delete_document(self, tenant_id: TenantId, document_id: DocumentId) -> None:
        keys = [
            key
            for key, chunk in self._chunks.items()
            if chunk.tenant_id == tenant_id and chunk.document_id == document_id
        ]
        for key in keys:
            del self._chunks[key]

    async def delete_generation(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> None:
        keys = [
            key
            for key, chunk in self._chunks.items()
            if chunk.tenant_id == tenant_id
            and chunk.document_id == document_id
            and chunk.generation_id == generation_id
        ]
        for key in keys:
            del self._chunks[key]

    async def delete_version(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
    ) -> None:
        keys = [
            key
            for key, chunk in self._chunks.items()
            if chunk.tenant_id == tenant_id
            and chunk.document_id == document_id
            and chunk.source_version == source_version
        ]
        for key in keys:
            del self._chunks[key]


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._documents: dict[tuple[TenantId, DocumentId], Document] = {}

    async def save(
        self,
        document: Document,
        *,
        expected_revision: int | None = None,
    ) -> Document:
        key = (document.tenant_id, document.document_id)
        current = self._documents.get(key)
        if current is None:
            if expected_revision is not None:
                raise RepositoryConflictError("document does not exist")
            self._documents[key] = document
            return document
        if expected_revision is not None and current.revision != expected_revision:
            raise RepositoryConflictError("document revision changed")
        stored = document.model_copy(update={"revision": current.revision + 1})
        self._documents[key] = stored
        return stored

    async def get(self, tenant_id: TenantId, document_id: DocumentId) -> Document | None:
        return self._documents.get((tenant_id, document_id))


class InMemoryIndexGenerationRepository:
    def __init__(self) -> None:
        self._generations: dict[tuple[TenantId, DocumentId, str], IndexGeneration] = {}

    @property
    def generations(self) -> tuple[IndexGeneration, ...]:
        return tuple(self._generations.values())

    async def save(self, generation: IndexGeneration) -> None:
        key = (
            generation.tenant_id,
            generation.document_id,
            generation.generation_id,
        )
        self._generations[key] = generation

    async def get(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> IndexGeneration | None:
        return self._generations.get((tenant_id, document_id, generation_id))
