from ia_application import (
    IngestionJobClaim,
    IngestionJobRepository,
    RepositoryConflictError,
)
from ia_domain import IngestionJob, IngestionStatus, JobId, TenantId

from ia_aws_clients.dynamodb_control import (
    DynamoConditionFailedError,
    DynamoControlTable,
    TransactionAction,
)
from ia_aws_clients.dynamodb_document_codec import (
    _ENTITY_FINGERPRINT,
    _decode_job,
    _fingerprint_key,
    _job_item,
    _job_key,
    _string,
    _transaction_token,
)


class DynamoIngestionJobRepository(IngestionJobRepository):
    def __init__(self, table: DynamoControlTable) -> None:
        self._table = table

    async def save(self, job: IngestionJob) -> None:
        await self._table.put_item(_job_item(job))
        if job.fingerprint is None:
            return
        marker_values: dict[str, object] = {
            ":entity": _ENTITY_FINGERPRINT,
            ":tenant": str(job.tenant_id),
            ":fingerprint": job.fingerprint,
            ":job": str(job.job_id),
            ":status": job.status.value,
            ":fencing": job.fencing_token or 0,
        }
        try:
            await self._table.update_item(
                _fingerprint_key(job.tenant_id, job.fingerprint),
                update_expression=(
                    "SET entityType = :entity, tenantId = :tenant, fingerprint = :fingerprint, "
                    "jobId = :job, #status = :status, fencingToken = :fencing"
                ),
                condition_expression="attribute_not_exists(jobId) OR jobId = :job",
                expression_attribute_names={"#status": "status"},
                expression_attribute_values=marker_values,
            )
        except DynamoConditionFailedError:
            return

    async def claim(self, job: IngestionJob) -> IngestionJobClaim:
        if job.fingerprint is None or job.fencing_token is None:
            msg = "claimed ingestion jobs require a fingerprint and fencing token"
            raise ValueError(msg)
        marker = {
            **_fingerprint_key(job.tenant_id, job.fingerprint),
            "entityType": _ENTITY_FINGERPRINT,
            "tenantId": str(job.tenant_id),
            "fingerprint": job.fingerprint,
            "jobId": str(job.job_id),
            "status": job.status.value,
            "fencingToken": job.fencing_token,
        }
        actions: tuple[TransactionAction, ...] = (
            {
                "Put": {
                    "Item": _job_item(job),
                    "ConditionExpression": (
                        "attribute_not_exists(jobId) OR "
                        "(fingerprint = :fingerprint AND "
                        "(#status = :failed OR "
                        "(#status = :running AND fencingToken < :fencing)))"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":fingerprint": job.fingerprint,
                        ":failed": IngestionStatus.FAILED.value,
                        ":running": IngestionStatus.RUNNING.value,
                        ":fencing": job.fencing_token,
                    },
                }
            },
            {
                "Put": {
                    "Item": marker,
                    "ConditionExpression": (
                        "attribute_not_exists(jobId) OR #status = :failed OR "
                        "(#status = :running AND fencingToken < :fencing)"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":failed": IngestionStatus.FAILED.value,
                        ":running": IngestionStatus.RUNNING.value,
                        ":fencing": job.fencing_token,
                    },
                }
            },
        )
        try:
            await self._table.transact_write(
                actions,
                client_request_token=_transaction_token(
                    "claim",
                    str(job.tenant_id),
                    str(job.job_id),
                    job.fingerprint,
                    str(job.fencing_token),
                ),
            )
        except DynamoConditionFailedError:
            existing = await self.find_by_fingerprint(job.tenant_id, job.fingerprint)
            if existing is None:
                raise RepositoryConflictError(
                    "ingestion fingerprint claim conflicted"
                ) from None
            return IngestionJobClaim(job=existing, acquired=False)
        return IngestionJobClaim(job=job, acquired=True)

    async def get(self, tenant_id: TenantId, job_id: JobId) -> IngestionJob | None:
        item = await self._table.get_item(_job_key(tenant_id, job_id))
        if item is None:
            return None
        job = _decode_job(item)
        if job.tenant_id != tenant_id or job.job_id != job_id:
            return None
        return job

    async def find_by_fingerprint(
        self,
        tenant_id: TenantId,
        fingerprint: str,
    ) -> IngestionJob | None:
        marker = await self._table.get_item(_fingerprint_key(tenant_id, fingerprint))
        if marker is None:
            return None
        if (
            marker.get("tenantId") != str(tenant_id)
            or marker.get("fingerprint") != fingerprint
        ):
            return None
        job_id = JobId(_string(marker.get("jobId"), "jobId"))
        job = await self.get(tenant_id, job_id)
        if job is None:
            msg = "ingestion fingerprint marker references a missing job"
            raise RuntimeError(msg)
        return job
