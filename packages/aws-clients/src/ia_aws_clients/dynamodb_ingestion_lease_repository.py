from datetime import datetime

from ia_application import (
    DocumentIngestionLeaseRepository,
    IngestionLease,
    IngestionLeaseClaim,
    RepositoryConflictError,
)
from ia_domain import DocumentId, TenantId

from ia_aws_clients.dynamodb_control import (
    DynamoConditionFailedError,
    DynamoControlTable,
)
from ia_aws_clients.dynamodb_document_codec import (
    _ENTITY_LEASE,
    _decode_lease,
    _iso,
    _lease_key,
)


class DynamoDocumentIngestionLeaseRepository(DocumentIngestionLeaseRepository):
    def __init__(self, table: DynamoControlTable) -> None:
        self._table = table

    async def acquire(
        self,
        *,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
        owner_token: str,
        expires_at: datetime,
        now: datetime,
    ) -> IngestionLeaseClaim:
        if expires_at <= now:
            msg = "lease expiration must be after now"
            raise ValueError(msg)
        key = _lease_key(tenant_id, document_id, source_version)
        current_item = await self._table.get_item(key)
        if current_item is not None:
            current = _decode_lease(current_item)
            if current.expires_at > now:
                return IngestionLeaseClaim(
                    lease=current,
                    acquired=current.owner_token == owner_token,
                )
        try:
            updated = await self._table.update_item(
                key,
                update_expression=(
                    "SET entityType = :entity, tenantId = :tenant, documentId = :document, "
                    "sourceVersion = :source, ownerToken = :owner, expiresAt = :expires, "
                    "fencingToken = if_not_exists(fencingToken, :zero) + :one"
                ),
                condition_expression="attribute_not_exists(expiresAt) OR expiresAt <= :now",
                expression_attribute_values={
                    ":entity": _ENTITY_LEASE,
                    ":tenant": str(tenant_id),
                    ":document": str(document_id),
                    ":source": source_version,
                    ":owner": owner_token,
                    ":expires": _iso(expires_at),
                    ":now": _iso(now),
                    ":zero": 0,
                    ":one": 1,
                },
            )
        except DynamoConditionFailedError:
            blocked_item = await self._table.get_item(key)
            if blocked_item is None:
                raise RepositoryConflictError("lease acquisition conflicted") from None
            return IngestionLeaseClaim(
                lease=_decode_lease(blocked_item), acquired=False
            )
        return IngestionLeaseClaim(lease=_decode_lease(updated), acquired=True)

    async def release(self, lease: IngestionLease) -> None:
        try:
            await self._table.delete_item(
                _lease_key(lease.tenant_id, lease.document_id, lease.source_version),
                condition_expression="ownerToken = :owner AND fencingToken = :fencing",
                expression_attribute_values={
                    ":owner": lease.owner_token,
                    ":fencing": lease.fencing_token,
                },
            )
        except DynamoConditionFailedError:
            return
