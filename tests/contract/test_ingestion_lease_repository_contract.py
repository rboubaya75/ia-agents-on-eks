from datetime import UTC, datetime, timedelta

import pytest
from ia_domain import DocumentId, TenantId
from test_support import InMemoryDocumentIngestionLeaseRepository

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _lease_owner(value: str) -> str:
    return value


@pytest.mark.asyncio
async def test_lease_is_unique_per_document_version_and_uses_fencing_tokens() -> None:
    repository = InMemoryDocumentIngestionLeaseRepository()

    first = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token=_lease_owner("job-a"),
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    renewed = await repository.renew(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token=_lease_owner("job-a"),
        expires_at=NOW + timedelta(minutes=10),
        now=NOW + timedelta(minutes=1),
    )
    blocked = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token=_lease_owner("job-b"),
        expires_at=NOW + timedelta(minutes=5),
        now=NOW + timedelta(minutes=6),
    )
    replacement = await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token=_lease_owner("job-b"),
        expires_at=NOW + timedelta(minutes=20),
        now=NOW + timedelta(minutes=11),
    )

    assert first.acquired is True
    assert renewed is True
    assert blocked.acquired is False
    assert replacement.acquired is True
    assert replacement.lease.fencing_token > first.lease.fencing_token


@pytest.mark.asyncio
async def test_lease_renewal_rejects_wrong_owner_or_expired_lease() -> None:
    repository = InMemoryDocumentIngestionLeaseRepository()
    await repository.acquire(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token=_lease_owner("job-a"),
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )

    wrong_owner = await repository.renew(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token=_lease_owner("job-b"),
        expires_at=NOW + timedelta(minutes=10),
        now=NOW + timedelta(minutes=1),
    )
    expired = await repository.renew(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        owner_token=_lease_owner("job-a"),
        expires_at=NOW + timedelta(minutes=12),
        now=NOW + timedelta(minutes=6),
    )

    assert wrong_owner is False
    assert expired is False
