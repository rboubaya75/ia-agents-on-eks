from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from ia_aws_clients.dynamodb_control import (
    DynamoConditionFailedError,
    DynamoControlTable,
    PythonItem,
    TransactionAction,
)
from ia_aws_clients.dynamodb_ingestion_lease_repository import (
    DynamoDocumentIngestionLeaseRepository,
)
from ia_domain import DocumentId, TenantId

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
LEASE_OWNER = ":".join(("worker", "a"))
ATTEMPT_ID = ":".join(("execution", "a"))
FENCING_TOKEN = 7


class LeaseControlTable:
    def __init__(self) -> None:
        self.fail_condition = False
        self.last_update: dict[str, object] | None = None

    @property
    def table_name(self) -> str:
        return "control"

    async def get_item(self, key: Mapping[str, object]) -> PythonItem | None:
        del key
        return None

    async def put_item(
        self,
        item: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        del item, condition_expression, expression_attribute_names
        del expression_attribute_values

    async def update_item(
        self,
        key: Mapping[str, object],
        *,
        update_expression: str,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> PythonItem:
        del expression_attribute_names
        if self.fail_condition:
            raise DynamoConditionFailedError("condition")
        values = dict(expression_attribute_values or {})
        self.last_update = {
            "key": dict(key),
            "update": update_expression,
            "condition": condition_expression or "",
            "values": values,
        }
        return dict(key)

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
        del actions, client_request_token


@pytest.mark.asyncio
async def test_renew_extends_only_exact_unexpired_claim() -> None:
    table = LeaseControlTable()
    repository = DynamoDocumentIngestionLeaseRepository(cast(DynamoControlTable, table))

    renewed = await repository.renew(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        fencing_token=FENCING_TOKEN,
        execution_token=ATTEMPT_ID,
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )

    assert renewed is True
    assert table.last_update is not None
    assert table.last_update["update"] == "SET expiresAt = :expires"
    assert table.last_update["condition"] == (
        "ownerToken = :owner AND fencingToken = :fencing AND "
        "executionToken = :execution AND expiresAt > :now"
    )
    values = table.last_update["values"]
    assert isinstance(values, dict)
    assert values[":owner"] == LEASE_OWNER
    assert values[":fencing"] == FENCING_TOKEN
    assert values[":execution"] == ATTEMPT_ID


@pytest.mark.asyncio
async def test_renew_returns_false_after_lease_loss() -> None:
    table = LeaseControlTable()
    table.fail_condition = True
    repository = DynamoDocumentIngestionLeaseRepository(cast(DynamoControlTable, table))

    renewed = await repository.renew(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        fencing_token=FENCING_TOKEN,
        execution_token=ATTEMPT_ID,
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )

    assert renewed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fencing_token", "execution_token", "expected_message"),
    (
        (0, ATTEMPT_ID, "fencing_token"),
        (FENCING_TOKEN, "", "execution_token"),
    ),
)
async def test_renew_rejects_invalid_claim_identity(
    fencing_token: int,
    execution_token: str,
    expected_message: str,
) -> None:
    repository = DynamoDocumentIngestionLeaseRepository(
        cast(DynamoControlTable, LeaseControlTable())
    )

    with pytest.raises(ValueError, match=expected_message):
        await repository.renew(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            source_version="source-a",
            owner_token=LEASE_OWNER,
            fencing_token=fencing_token,
            execution_token=execution_token,
            expires_at=NOW + timedelta(minutes=15),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_renew_rejects_non_future_expiration() -> None:
    repository = DynamoDocumentIngestionLeaseRepository(
        cast(DynamoControlTable, LeaseControlTable())
    )

    with pytest.raises(ValueError, match="after now"):
        await repository.renew(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            source_version="source-a",
            owner_token=LEASE_OWNER,
            fencing_token=FENCING_TOKEN,
            execution_token=ATTEMPT_ID,
            expires_at=NOW,
            now=NOW,
        )
