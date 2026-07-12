import pytest
from ia_application import VectorQuery, VectorRecord
from ia_domain import ChunkId, Classification, DocumentId, Role, TenantId
from test_support import InMemoryVectorRepository


@pytest.mark.asyncio
async def test_vector_query_can_restrict_results_to_active_generations() -> None:
    repository = InMemoryVectorRepository()
    await repository.upsert(
        (
            VectorRecord(
                tenant_id=TenantId("tenant-a"),
                document_id=DocumentId("document-a"),
                chunk_id=ChunkId("active-chunk"),
                generation_id="generation-active",
                classification=Classification.INTERNAL,
                allowed_roles=frozenset({Role.USER}),
                source_version="v1",
                checksum="a" * 64,
                vector=(1.0, 0.0),
            ),
            VectorRecord(
                tenant_id=TenantId("tenant-a"),
                document_id=DocumentId("document-a"),
                chunk_id=ChunkId("candidate-chunk"),
                generation_id="generation-candidate",
                classification=Classification.INTERNAL,
                allowed_roles=frozenset({Role.USER}),
                source_version="v1",
                checksum="b" * 64,
                vector=(1.0, 0.0),
            ),
        )
    )

    matches = await repository.query(
        VectorQuery(
            tenant_id=TenantId("tenant-a"),
            allowed_classifications=frozenset({Classification.INTERNAL}),
            allowed_roles=frozenset({Role.USER}),
            query_vector=(1.0, 0.0),
            top_k=10,
            allowed_generation_ids=frozenset({"generation-active"}),
        )
    )

    assert tuple(match.chunk_id for match in matches) == (ChunkId("active-chunk"),)
    assert matches[0].generation_id == "generation-active"
