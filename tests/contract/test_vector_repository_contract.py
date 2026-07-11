import pytest
from ia_application import VectorQuery, VectorRecord
from ia_domain import ChunkId, Classification, DocumentId, Role, TenantId
from pydantic import ValidationError
from test_support import InMemoryVectorRepository


def _record(
    tenant: str,
    document: str,
    chunk: str,
    classification: Classification,
    roles: frozenset[Role],
    vector: tuple[float, ...],
) -> VectorRecord:
    return VectorRecord(
        tenant_id=TenantId(tenant),
        document_id=DocumentId(document),
        chunk_id=ChunkId(chunk),
        classification=classification,
        allowed_roles=roles,
        source_version="1",
        checksum="a" * 64,
        vector=vector,
    )


@pytest.fixture
async def repository() -> InMemoryVectorRepository:
    repository = InMemoryVectorRepository()
    await repository.upsert(
        [
            _record(
                "tenant-a",
                "doc-a-user",
                "chunk-a-user",
                Classification.INTERNAL,
                frozenset({Role.USER}),
                (1.0, 0.0),
            ),
            _record(
                "tenant-a",
                "doc-a-admin",
                "chunk-a-admin",
                Classification.RESTRICTED,
                frozenset({Role.TENANT_ADMIN}),
                (1.0, 0.0),
            ),
            _record(
                "tenant-b",
                "doc-b-user",
                "chunk-b-user",
                Classification.INTERNAL,
                frozenset({Role.USER}),
                (1.0, 0.0),
            ),
        ]
    )
    return repository


async def test_query_isolates_tenant(repository: InMemoryVectorRepository) -> None:
    matches = await repository.query(
        VectorQuery(
            tenant_id=TenantId("tenant-a"),
            allowed_classifications=frozenset({Classification.INTERNAL}),
            allowed_roles=frozenset({Role.USER}),
            query_vector=(1.0, 0.0),
            top_k=10,
        )
    )
    assert {match.document_id for match in matches} == {DocumentId("doc-a-user")}


async def test_query_filters_classification(repository: InMemoryVectorRepository) -> None:
    matches = await repository.query(
        VectorQuery(
            tenant_id=TenantId("tenant-a"),
            allowed_classifications=frozenset({Classification.RESTRICTED}),
            allowed_roles=frozenset({Role.TENANT_ADMIN}),
            query_vector=(1.0, 0.0),
            top_k=10,
        )
    )
    assert {match.document_id for match in matches} == {DocumentId("doc-a-admin")}


async def test_query_filters_roles(repository: InMemoryVectorRepository) -> None:
    matches = await repository.query(
        VectorQuery(
            tenant_id=TenantId("tenant-a"),
            allowed_classifications=frozenset({Classification.INTERNAL, Classification.RESTRICTED}),
            allowed_roles=frozenset({Role.USER}),
            query_vector=(1.0, 0.0),
            top_k=10,
        )
    )
    assert {match.document_id for match in matches} == {DocumentId("doc-a-user")}


def test_query_rejects_empty_role_filter() -> None:
    with pytest.raises(ValidationError):
        VectorQuery(
            tenant_id=TenantId("tenant-a"),
            allowed_classifications=frozenset({Classification.INTERNAL}),
            allowed_roles=frozenset(),
            query_vector=(1.0, 0.0),
            top_k=10,
        )


def test_query_rejects_empty_classification_filter() -> None:
    with pytest.raises(ValidationError):
        VectorQuery(
            tenant_id=TenantId("tenant-a"),
            allowed_classifications=frozenset(),
            allowed_roles=frozenset({Role.USER}),
            query_vector=(1.0, 0.0),
            top_k=10,
        )


async def test_delete_document_is_tenant_scoped(repository: InMemoryVectorRepository) -> None:
    await repository.delete_document(TenantId("tenant-a"), DocumentId("doc-a-user"))
    tenant_a = await repository.query(
        VectorQuery(
            tenant_id=TenantId("tenant-a"),
            allowed_classifications=frozenset({Classification.INTERNAL}),
            allowed_roles=frozenset({Role.USER}),
            query_vector=(1.0, 0.0),
            top_k=10,
        )
    )
    tenant_b = await repository.query(
        VectorQuery(
            tenant_id=TenantId("tenant-b"),
            allowed_classifications=frozenset({Classification.INTERNAL}),
            allowed_roles=frozenset({Role.USER}),
            query_vector=(1.0, 0.0),
            top_k=10,
        )
    )
    assert tenant_a == ()
    assert {match.document_id for match in tenant_b} == {DocumentId("doc-b-user")}
