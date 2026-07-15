from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from ia_aws_clients.dynamodb_control import DynamoControlTable, PythonItem
from ia_aws_clients.dynamodb_ingestion_lease_repository import (
    DynamoDocumentIngestionLeaseRepository,
)
from ia_domain import DocumentId, TenantId
from test_support import InMemoryDocumentIngestionLeaseRepository

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
LEASE_OWNER = ":".join(("worker", "a"))


class ActiveLeaseTable:
    def __init__(self) -> None:
        self.update_called = False

    @property
    def table_name(self) -> str:
        return "control"

    async def get_item(self, key: Mapping[str, object]) -> PythonItem | None:
        return {
            **dict(key),
            "entityType": "IngestionLease",
            "tenantId": "tenant-a",
            "documentId": "document-a",
            "sourceVersion": "source-a",
            "ownerToken": LEASE_OWNER,
            "executionToken": "execution-a",
            "fencingToken": 7,
            "expiresAt": (NOW + timedelta(minutes=5))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        }

    async def update_item(self, *args: object, **kwargs: object) -> PythonItem:
        del args, kwargs
        self.update_called = True
        raise AssertionError("an active lease must not be updated")


@pytest.mark.asyncio
async def test_dynamo_active_lease_is_not_reentrant_for_same_owner() -> None:
    table = ActiveLeaseTable()
    repository = DynamoDocumentIngestionLeaseRepository(cast(DynamoControlTable, table))

    claim = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        execution_token="execution-b",
        expires_at=NOW + timedelta(minutes=10),
        now=NOW,
    )

    assert claim.acquired is False
    assert claim.lease.owner_token == LEASE_OWNER
    assert claim.lease.fencing_token == 7
    assert table.update_called is False


@pytest.mark.asyncio
async def test_in_memory_active_lease_is_not_reentrant_for_same_owner() -> None:
    repository = InMemoryDocumentIngestionLeaseRepository()
    first = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        execution_token="execution-a",
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    duplicate = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        execution_token="execution-b",
        expires_at=NOW + timedelta(minutes=10),
        now=NOW + timedelta(minutes=1),
    )

    assert first.acquired is True
    assert duplicate.acquired is False
    assert duplicate.lease == first.lease
    assert (
        await repository.renew(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            source_version="source-a",
            owner_token=LEASE_OWNER,
            fencing_token=first.lease.fencing_token,
            execution_token="execution-a",
            expires_at=NOW + timedelta(minutes=10),
            now=NOW + timedelta(minutes=1),
        )
        is True
    )


@pytest.mark.asyncio
async def test_stale_same_owner_claim_cannot_renew_replacement() -> None:
    repository = InMemoryDocumentIngestionLeaseRepository()
    first = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        execution_token="execution-a",
        expires_at=NOW + timedelta(minutes=1),
        now=NOW,
    )
    replacement = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        execution_token="execution-b",
        expires_at=NOW + timedelta(minutes=10),
        now=NOW + timedelta(minutes=2),
    )

    stale_renewal = await repository.renew(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        fencing_token=first.lease.fencing_token,
        execution_token="execution-a",
        expires_at=NOW + timedelta(minutes=20),
        now=NOW + timedelta(minutes=3),
    )
    replacement_renewal = await repository.renew(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="source-a",
        owner_token=LEASE_OWNER,
        fencing_token=replacement.lease.fencing_token,
        execution_token="execution-b",
        expires_at=NOW + timedelta(minutes=20),
        now=NOW + timedelta(minutes=3),
    )

    assert replacement.acquired is True
    assert replacement.lease.fencing_token > first.lease.fencing_token
    assert stale_renewal is False
    assert replacement_renewal is True
