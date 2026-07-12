from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from ia_application import RepositoryConflictError
from ia_aws_clients.dynamodb_control import (
    Boto3DynamoClient,
    Boto3DynamoControlTable,
    DynamoConditionFailedError,
    DynamoControlTable,
    PythonItem,
    TransactionAction,
)
from ia_aws_clients.dynamodb_document_codec import (
    _decode_document,
    _decode_generation,
    _decode_job,
    _document_item,
    _fingerprint_key,
    _generation_item,
    _generation_key,
    _integer,
    _iso,
    _job_item,
    _job_key,
    _roles,
    _transaction_token,
)
from ia_aws_clients.dynamodb_documents import (
    DynamoDocumentIngestionLeaseRepository,
    DynamoDocumentRepository,
    DynamoIndexGenerationRepository,
    DynamoIngestionJobRepository,
)
from ia_domain import (
    Classification,
    Document,
    DocumentId,
    DocumentStatus,
    IndexGeneration,
    IndexGenerationStatus,
    IngestionJob,
    IngestionStatus,
    JobId,
    Role,
    TenantId,
    UserId,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class LowLevelClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error: ClientError | None = None
        self.update_response: dict[str, object] = {"Attributes": {"pk": {"S": "tenant"}}}

    def get_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get", kwargs))
        return {}

    def put_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("put", kwargs))
        if self.error is not None:
            raise self.error
        return {}

    def update_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("update", kwargs))
        if self.error is not None:
            raise self.error
        return self.update_response

    def delete_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("delete", kwargs))
        if self.error is not None:
            raise self.error
        return {}

    def transact_write_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("transact", kwargs))
        if self.error is not None:
            raise self.error
        return {}


class StubControlTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], PythonItem] = {}
        self.puts: list[PythonItem] = []
        self.updates: list[tuple[PythonItem, dict[str, object]]] = []
        self.deletes: list[PythonItem] = []
        self.transactions: list[tuple[TransactionAction, ...]] = []
        self.put_error = False
        self.update_error = False
        self.delete_error = False
        self.transaction_error = False
        self.update_result: PythonItem | None = None

    @property
    def table_name(self) -> str:
        return "control"

    @staticmethod
    def _key(value: Mapping[str, object]) -> tuple[str, str]:
        return str(value["pk"]), str(value["sk"])

    async def get_item(self, key: Mapping[str, object]) -> PythonItem | None:
        return self.items.get(self._key(key))

    async def put_item(
        self,
        item: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        del (
            condition_expression,
            expression_attribute_names,
            expression_attribute_values,
        )
        if self.put_error:
            raise DynamoConditionFailedError("put conflict")
        stored = dict(item)
        self.puts.append(stored)
        self.items[self._key(stored)] = stored

    async def update_item(
        self,
        key: Mapping[str, object],
        *,
        update_expression: str,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> PythonItem:
        del condition_expression, expression_attribute_names
        if self.update_error:
            raise DynamoConditionFailedError("update conflict")
        values = dict(expression_attribute_values or {})
        self.updates.append((dict(key), {"expression": update_expression, **values}))
        if self.update_result is not None:
            return self.update_result
        return {**dict(key), **values}

    async def delete_item(
        self,
        key: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        del (
            condition_expression,
            expression_attribute_names,
            expression_attribute_values,
        )
        if self.delete_error:
            raise DynamoConditionFailedError("delete conflict")
        self.deletes.append(dict(key))
        self.items.pop(self._key(key), None)

    async def transact_write(
        self,
        actions: Sequence[TransactionAction],
        *,
        client_request_token: str | None = None,
    ) -> None:
        del client_request_token
        if self.transaction_error:
            raise DynamoConditionFailedError("transaction conflict")
        self.transactions.append(tuple(actions))


def _client_error(code: str) -> ClientError:
    return ClientError(
        cast(
            dict[str, Any],
            {"Error": {"Code": code, "Message": code}},
        ),
        "Test",
    )


def _document(*, tenant_id: str = "tenant-a") -> Document:
    return Document(
        tenant_id=TenantId(tenant_id),
        document_id=DocumentId("document-a"),
        owner_user_id=UserId("user-a"),
        title="Policy",
        source_uri="s3://bucket/key",
        source_version="v1",
        source_checksum="a" * 64,
        content_type="application/pdf",
        language="fr",
        classification=Classification.CONFIDENTIAL,
        allowed_roles=frozenset({Role.SUPPORT}),
        status=DocumentStatus.UPLOADED,
        created_at=NOW,
        updated_at=NOW,
    )


def _job(*, status: IngestionStatus = IngestionStatus.RUNNING) -> IngestionJob:
    return IngestionJob(
        tenant_id=TenantId("tenant-a"),
        job_id=JobId("job-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        status=status,
        chunks_created=2,
        vectors_created=2,
        error_code=None,
        fingerprint="b" * 64,
        generation_id="generation-a",
        source_checksum="a" * 64,
        authorization_checksum="c" * 64,
        embedding_model_alias="default",
        embedding_profile_revision="profile-v1",
        resolved_embedding_model_id="model-v1",
        embedding_dimensions=256,
        chunking_version="paragraph-v1",
        pipeline_version="pipeline-v1",
        fencing_token=2,
        started_at=NOW,
        completed_at=NOW if status is IngestionStatus.SUCCEEDED else None,
    )


def _generation() -> IndexGeneration:
    return IndexGeneration(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        generation_id="generation-a",
        fingerprint="b" * 64,
        authorization_checksum="c" * 64,
        embedding_profile_revision="profile-v1",
        embedding_model_id="model-v1",
        embedding_dimensions=256,
        status=IndexGenerationStatus.READY,
        fencing_token=2,
        chunk_count=2,
        vector_count=2,
        created_at=NOW,
        ready_at=NOW,
    )


@pytest.mark.asyncio
async def test_low_level_control_operations_and_validation_branches() -> None:
    client = LowLevelClient()
    table = Boto3DynamoControlTable(
        cast(Boto3DynamoClient, client),
        table_name="control",
    )
    await table.put_item(
        {"pk": "tenant", "count": 2},
        condition_expression="#count = :count",
        expression_attribute_names={"#count": "count"},
        expression_attribute_values={":count": 2},
    )
    updated = await table.update_item(
        {"pk": "tenant"},
        update_expression="SET #count = :count",
        expression_attribute_names={"#count": "count"},
        expression_attribute_values={":count": 3},
    )
    await table.delete_item({"pk": "tenant"})

    assert updated == {"pk": "tenant"}
    assert [name for name, _ in client.calls] == ["put", "update", "delete"]

    with pytest.raises(ValueError, match="table_name"):
        Boto3DynamoControlTable(cast(Boto3DynamoClient, client), table_name="")
    with pytest.raises(ValueError, match="at least one"):
        await table.transact_write(())
    with pytest.raises(ValueError, match="exactly one"):
        await table.transact_write(({"Put": {}, "Delete": {}},))
    with pytest.raises(ValueError, match="unsupported"):
        await table.transact_write(({"Get": {}},))
    with pytest.raises(TypeError, match="mapping"):
        await table.transact_write(({"Put": "invalid"},))


@pytest.mark.asyncio
async def test_low_level_control_reports_missing_attributes_and_conditions() -> None:
    client = LowLevelClient()
    table = Boto3DynamoControlTable(
        cast(Boto3DynamoClient, client),
        table_name="control",
    )
    client.update_response = {}
    with pytest.raises(RuntimeError, match="attributes"):
        await table.update_item({"pk": "tenant"}, update_expression="SET value = :value")

    client.error = _client_error("ConditionalCheckFailedException")
    with pytest.raises(DynamoConditionFailedError):
        await table.put_item({"pk": "tenant"})


@pytest.mark.parametrize("parts", [(), ("",), ("one", "")])
def test_transaction_token_requires_non_empty_parts(parts: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        _transaction_token(*parts)


def test_codec_round_trips_all_control_entities() -> None:
    document = _document()
    job = _job(IngestionStatus.SUCCEEDED)
    generation = _generation()

    assert _decode_document(_document_item(document)) == document
    assert _decode_job(_job_item(job)) == job
    assert _decode_generation(_generation_item(generation)) == generation


@pytest.mark.parametrize("value", [True, Decimal("1.5"), "1", None])
def test_codec_integer_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="integer"):
        _integer(value, "field")


def test_codec_helpers_validate_timezone_and_roles() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _iso(datetime(2026, 7, 12, 12, 0))
    with pytest.raises(ValueError, match="list"):
        _roles("support")


@pytest.mark.asyncio
async def test_document_repository_conflicts_and_defensive_identity_filter() -> None:
    table = StubControlTable()
    repository = DynamoDocumentRepository(cast(DynamoControlTable, table))
    table.put_error = True
    with pytest.raises(RepositoryConflictError, match="create"):
        await repository.save(_document())

    table.put_error = False
    wrong = _document(tenant_id="tenant-b")
    requested_key = _document_item(_document())
    table.items[(str(requested_key["pk"]), str(requested_key["sk"]))] = _document_item(wrong)
    assert await repository.get(TenantId("tenant-a"), DocumentId("document-a")) is None

    table.put_error = True
    with pytest.raises(RepositoryConflictError, match="revision"):
        await repository.save(_document(), expected_revision=0)


@pytest.mark.asyncio
async def test_index_generation_repository_round_trip_and_identity_filter() -> None:
    table = StubControlTable()
    repository = DynamoIndexGenerationRepository(cast(DynamoControlTable, table))
    generation = _generation()
    await repository.save(generation)

    assert (
        await repository.get(
            generation.tenant_id,
            generation.document_id,
            generation.generation_id,
        )
        == generation
    )
    assert (
        await repository.get(
            TenantId("tenant-b"),
            generation.document_id,
            generation.generation_id,
        )
        is None
    )

    key = _generation_key(generation.tenant_id, generation.document_id, "wrong-id")
    table.items[(str(key["pk"]), str(key["sk"]))] = _generation_item(generation)
    assert (
        await repository.get(
            generation.tenant_id,
            generation.document_id,
            "wrong-id",
        )
        is None
    )


@pytest.mark.asyncio
async def test_job_repository_save_lookup_and_conflict_paths() -> None:
    table = StubControlTable()
    repository = DynamoIngestionJobRepository(cast(DynamoControlTable, table))

    no_fingerprint = _job().model_copy(update={"fingerprint": None})
    await repository.save(no_fingerprint)
    assert len(table.puts) == 1
    assert table.updates == []

    await repository.save(_job())
    assert len(table.updates) == 1

    table.update_error = True
    await repository.save(_job())

    assert await repository.get(TenantId("tenant-b"), JobId("job-a")) is None
    assert await repository.find_by_fingerprint(TenantId("tenant-a"), "missing") is None

    marker_key = _fingerprint_key(TenantId("tenant-a"), "x" * 64)
    table.items[(str(marker_key["pk"]), str(marker_key["sk"]))] = {
        **marker_key,
        "tenantId": "tenant-b",
        "fingerprint": "x" * 64,
        "jobId": "job-a",
    }
    assert await repository.find_by_fingerprint(TenantId("tenant-a"), "x" * 64) is None

    marker_key = _fingerprint_key(TenantId("tenant-a"), "y" * 64)
    table.items[(str(marker_key["pk"]), str(marker_key["sk"]))] = {
        **marker_key,
        "tenantId": "tenant-a",
        "fingerprint": "y" * 64,
        "jobId": "missing-job",
    }
    with pytest.raises(RuntimeError, match="missing job"):
        await repository.find_by_fingerprint(TenantId("tenant-a"), "y" * 64)


@pytest.mark.asyncio
async def test_job_claim_returns_existing_job_after_conditional_conflict() -> None:
    table = StubControlTable()
    repository = DynamoIngestionJobRepository(cast(DynamoControlTable, table))
    job = _job()
    table.items[
        (
            str(_job_key(job.tenant_id, job.job_id)["pk"]),
            str(_job_key(job.tenant_id, job.job_id)["sk"]),
        )
    ] = _job_item(job)
    marker_key = _fingerprint_key(job.tenant_id, cast(str, job.fingerprint))
    table.items[(str(marker_key["pk"]), str(marker_key["sk"]))] = {
        **marker_key,
        "tenantId": str(job.tenant_id),
        "fingerprint": job.fingerprint,
        "jobId": str(job.job_id),
    }
    table.transaction_error = True

    result = await repository.claim(job)

    assert result.acquired is False
    assert result.job == job


@pytest.mark.asyncio
async def test_lease_repository_handles_active_conflict_and_release() -> None:
    table = StubControlTable()
    repository = DynamoDocumentIngestionLeaseRepository(cast(DynamoControlTable, table))
    with pytest.raises(ValueError, match="after now"):
        await repository.acquire(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            source_version="v1",
            owner_token="owner-a",  # noqa: S106
            expires_at=NOW,
            now=NOW,
        )

    lease_key = {
        "pk": "6:TENANT8:tenant-a",
        "sk": "5:LEASE10:document-a2:v1",
    }
    active = {
        **lease_key,
        "tenantId": "tenant-a",
        "documentId": "document-a",
        "sourceVersion": "v1",
        "ownerToken": "owner-a",
        "fencingToken": 3,
        "expiresAt": (NOW + timedelta(minutes=5)).isoformat(),
    }
    table.items[(str(lease_key["pk"]), str(lease_key["sk"]))] = active
    same_owner = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token="owner-a",  # noqa: S106
        expires_at=NOW + timedelta(minutes=10),
        now=NOW,
    )
    assert same_owner.acquired is True

    other_owner = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token="owner-b",  # noqa: S106
        expires_at=NOW + timedelta(minutes=10),
        now=NOW,
    )
    assert other_owner.acquired is False

    table.delete_error = True
    await repository.release(same_owner.lease)
