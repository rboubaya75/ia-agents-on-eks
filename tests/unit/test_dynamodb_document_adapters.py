from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from ia_application import RepositoryConflictError
from ia_aws_clients.dynamodb_control import (
    DynamoConditionFailedError,
    DynamoControlTable,
    PythonItem,
    TransactionAction,
)
from ia_aws_clients.dynamodb_documents import (
    DynamoDocumentIngestionLeaseRepository,
    DynamoDocumentRepository,
    DynamoIndexActivationRepository,
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


class RecordingControlTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], PythonItem] = {}
        self.transactions: list[tuple[tuple[TransactionAction, ...], str | None]] = []
        self.fail_transaction = False

    @property
    def table_name(self) -> str:
        return "control"

    async def get_item(self, key: Mapping[str, object]) -> PythonItem | None:
        return self.items.get((str(key["pk"]), str(key["sk"])))

    async def put_item(
        self,
        item: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        del expression_attribute_names
        key = (str(item["pk"]), str(item["sk"]))
        current = self.items.get(key)
        if condition_expression is not None:
            if "attribute_not_exists" in condition_expression and current is not None:
                raise DynamoConditionFailedError("condition")
            if "#revision = :expected" in condition_expression and (
                current is None
                or current.get("revision") != (expression_attribute_values or {}).get(":expected")
            ):
                raise DynamoConditionFailedError("condition")
        self.items[key] = dict(item)

    async def update_item(
        self,
        key: Mapping[str, object],
        *,
        update_expression: str,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> PythonItem:
        del update_expression, condition_expression, expression_attribute_names
        values = dict(expression_attribute_values or {})
        return {
            **dict(key),
            "entityType": "IngestionLease",
            "tenantId": values[":tenant"],
            "documentId": values[":document"],
            "sourceVersion": values[":source"],
            "ownerToken": values[":owner"],
            "expiresAt": values[":expires"],
            "fencingToken": 1,
        }

    async def delete_item(
        self,
        key: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        del key, condition_expression, expression_attribute_names
        del expression_attribute_values

    async def transact_write(
        self,
        actions: Sequence[TransactionAction],
        *,
        client_request_token: str | None = None,
    ) -> None:
        if self.fail_transaction:
            raise DynamoConditionFailedError("condition")
        self.transactions.append((tuple(actions), client_request_token))


def _document() -> Document:
    return Document(
        tenant_id=TenantId("tenant-a"),
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


def _job(status: IngestionStatus = IngestionStatus.RUNNING) -> IngestionJob:
    return IngestionJob(
        tenant_id=TenantId("tenant-a"),
        job_id=JobId("job-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        status=status,
        fingerprint="b" * 64,
        generation_id="generation-a",
        source_checksum="a" * 64,
        authorization_checksum="c" * 64,
        embedding_model_alias="default",
        embedding_profile_revision="profile-v1",
        resolved_embedding_model_id="model-v1",
        embedding_dimensions=2,
        chunking_version="paragraph-v1",
        pipeline_version="pipeline-v1",
        fencing_token=1,
        started_at=NOW,
        completed_at=NOW if status is IngestionStatus.SUCCEEDED else None,
        chunks_created=2 if status is IngestionStatus.SUCCEEDED else 0,
        vectors_created=2 if status is IngestionStatus.SUCCEEDED else 0,
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
        embedding_dimensions=2,
        status=IndexGenerationStatus.READY,
        fencing_token=1,
        chunk_count=2,
        vector_count=2,
        created_at=NOW,
        ready_at=NOW,
    )


def _operation(action: TransactionAction, name: str) -> Mapping[str, object]:
    value = action[name]
    if not isinstance(value, Mapping):
        raise TypeError("transaction operation must be a mapping")
    return cast(Mapping[str, object], value)


@pytest.mark.asyncio
async def test_document_repository_uses_optimistic_revision() -> None:
    table = RecordingControlTable()
    repository = DynamoDocumentRepository(cast(DynamoControlTable, table))
    created = await repository.save(_document())
    updated = await repository.save(
        created.model_copy(update={"title": "Updated"}),
        expected_revision=0,
    )

    assert updated.revision == 1
    assert await repository.get(TenantId("tenant-a"), DocumentId("document-a")) == updated
    assert await repository.get(TenantId("tenant-b"), DocumentId("document-a")) is None


@pytest.mark.asyncio
async def test_job_claim_is_atomic_idempotent_and_protects_job_identity() -> None:
    table = RecordingControlTable()
    repository = DynamoIngestionJobRepository(cast(DynamoControlTable, table))

    claimed = await repository.claim(_job())

    assert claimed.acquired is True
    actions, token = table.transactions[0]
    assert len(actions) == 2
    assert token is not None and len(token) == 36
    job_put = _operation(actions[0], "Put")
    marker_put = _operation(actions[1], "Put")
    assert "fingerprint = :fingerprint" in str(job_put["ConditionExpression"])
    assert str(marker_put["ConditionExpression"]).startswith("attribute_not_exists")


@pytest.mark.asyncio
async def test_lease_adapter_returns_fenced_lease() -> None:
    table = RecordingControlTable()
    repository = DynamoDocumentIngestionLeaseRepository(cast(DynamoControlTable, table))

    claim = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token="job-a",  # noqa: S106
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )

    assert claim.acquired is True
    assert claim.lease.fencing_token == 1
    assert claim.lease.owner_token == "job-a"  # noqa: S105


@pytest.mark.asyncio
async def test_activation_uses_one_four_item_conditional_transaction() -> None:
    table = RecordingControlTable()
    documents = DynamoDocumentRepository(cast(DynamoControlTable, table))
    stored = await documents.save(_document())
    activation = DynamoIndexActivationRepository(cast(DynamoControlTable, table))

    result = await activation.activate(
        generation=_generation(),
        succeeded_job=_job(IngestionStatus.SUCCEEDED),
        expected_document_revision=stored.revision,
        activated_at=NOW,
    )

    assert result.active_generation_id == "generation-a"
    actions, token = table.transactions[-1]
    assert len(actions) == 4
    assert token is not None and len(token) == 36
    document_update = _operation(actions[0], "Update")
    generation_update = _operation(actions[1], "Update")
    assert "lastFencingToken < :fencing" in str(document_update["ConditionExpression"])
    values = generation_update["ExpressionAttributeValues"]
    assert isinstance(values, Mapping)
    assert values[":ready"] == "ready"

    table.fail_transaction = True
    with pytest.raises(RepositoryConflictError):
        await activation.activate(
            generation=_generation(),
            succeeded_job=_job(IngestionStatus.SUCCEEDED),
            expected_document_revision=stored.revision,
            activated_at=NOW,
        )
