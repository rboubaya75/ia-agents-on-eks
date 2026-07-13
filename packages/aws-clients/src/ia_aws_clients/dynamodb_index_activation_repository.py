from datetime import datetime

from ia_application import IndexActivationRepository, RepositoryConflictError
from ia_domain import (
    Document,
    DocumentStatus,
    IndexGeneration,
    IndexGenerationStatus,
    IngestionJob,
    IngestionStatus,
)

from ia_aws_clients.dynamodb_control import (
    DynamoConditionFailedError,
    DynamoControlTable,
    TransactionAction,
)
from ia_aws_clients.dynamodb_document_codec import (
    _ENTITY_DOCUMENT,
    _ENTITY_FINGERPRINT,
    _ENTITY_GENERATION,
    _ENTITY_JOB,
    _ENTITY_LEASE,
    _decode_document,
    _decode_generation,
    _decode_job,
    _document_key,
    _fingerprint_key,
    _generation_key,
    _integer,
    _iso,
    _job_key,
    _lease_key,
    _string,
    _transaction_token,
)


class DynamoIndexActivationRepository(IndexActivationRepository):
    def __init__(self, table: DynamoControlTable) -> None:
        self._table = table

    async def activate(
        self,
        *,
        generation: IndexGeneration,
        succeeded_job: IngestionJob,
        expected_document_revision: int,
        activated_at: datetime,
    ) -> Document:
        self._validate_activation(generation, succeeded_job)
        current_item = await self._table.get_item(
            _document_key(generation.tenant_id, generation.document_id)
        )
        if current_item is None:
            raise RepositoryConflictError("document does not exist")
        current = _decode_document(current_item)
        if (
            current.tenant_id != generation.tenant_id
            or current.document_id != generation.document_id
            or current.source_version != generation.source_version
        ):
            raise RepositoryConflictError("document identity changed before activation")
        activated = current.model_copy(
            update={
                "active_generation_id": generation.generation_id,
                "active_index_fingerprint": generation.fingerprint,
                "last_fencing_token": generation.fencing_token,
                "status": DocumentStatus.INDEXED,
                "updated_at": activated_at,
                "revision": expected_document_revision + 1,
            }
        )
        actions = self._activation_actions(
            generation=generation,
            succeeded_job=succeeded_job,
            expected_document_revision=expected_document_revision,
            activated_at=activated_at,
        )
        try:
            await self._table.transact_write(
                actions,
                client_request_token=_transaction_token(
                    "activate",
                    str(generation.tenant_id),
                    str(generation.document_id),
                    generation.generation_id,
                    str(generation.fencing_token),
                ),
            )
        except DynamoConditionFailedError as error:
            raise RepositoryConflictError("index activation conditions were rejected") from error
        except Exception:
            reconciled = await self._reconcile_committed_activation(
                generation=generation,
                succeeded_job=succeeded_job,
                expected_document_revision=expected_document_revision,
                activated_at=activated_at,
            )
            if reconciled is not None:
                return reconciled
            raise
        return activated

    @staticmethod
    def _validate_activation(
        generation: IndexGeneration,
        succeeded_job: IngestionJob,
    ) -> None:
        if generation.status is not IndexGenerationStatus.READY:
            raise RepositoryConflictError("index generation is not ready")
        if succeeded_job.status is not IngestionStatus.SUCCEEDED:
            raise RepositoryConflictError("ingestion job is not succeeded")
        if (
            succeeded_job.tenant_id != generation.tenant_id
            or succeeded_job.document_id != generation.document_id
            or succeeded_job.source_version != generation.source_version
            or succeeded_job.generation_id != generation.generation_id
            or succeeded_job.fingerprint != generation.fingerprint
            or succeeded_job.fencing_token != generation.fencing_token
        ):
            raise RepositoryConflictError("activation metadata is inconsistent")

    async def _reconcile_committed_activation(
        self,
        *,
        generation: IndexGeneration,
        succeeded_job: IngestionJob,
        expected_document_revision: int,
        activated_at: datetime,
    ) -> Document | None:
        completed_at = succeeded_job.completed_at
        if completed_at is None:
            return None
        try:
            document_item = await self._table.get_item(
                _document_key(generation.tenant_id, generation.document_id)
            )
            generation_item = await self._table.get_item(
                _generation_key(
                    generation.tenant_id,
                    generation.document_id,
                    generation.generation_id,
                )
            )
            job_item = await self._table.get_item(
                _job_key(succeeded_job.tenant_id, succeeded_job.job_id)
            )
            marker_item = await self._table.get_item(
                _fingerprint_key(generation.tenant_id, generation.fingerprint)
            )
            if (
                document_item is None
                or generation_item is None
                or job_item is None
                or marker_item is None
            ):
                return None
            document = _decode_document(document_item)
            stored_generation = _decode_generation(generation_item)
            stored_job = _decode_job(job_item)
            if (
                document.tenant_id != generation.tenant_id
                or document.document_id != generation.document_id
                or document.source_version != generation.source_version
                or document.status is not DocumentStatus.INDEXED
                or document.active_generation_id != generation.generation_id
                or document.active_index_fingerprint != generation.fingerprint
                or document.last_fencing_token != generation.fencing_token
                or document.revision < expected_document_revision + 1
            ):
                return None
            if (
                stored_generation.tenant_id != generation.tenant_id
                or stored_generation.document_id != generation.document_id
                or stored_generation.generation_id != generation.generation_id
                or stored_generation.source_version != generation.source_version
                or stored_generation.status is not IndexGenerationStatus.ACTIVE
                or stored_generation.fingerprint != generation.fingerprint
                or stored_generation.fencing_token != generation.fencing_token
                or stored_generation.activated_at != activated_at
            ):
                return None
            if (
                stored_job.tenant_id != succeeded_job.tenant_id
                or stored_job.job_id != succeeded_job.job_id
                or stored_job.document_id != succeeded_job.document_id
                or stored_job.source_version != succeeded_job.source_version
                or stored_job.status is not IngestionStatus.SUCCEEDED
                or stored_job.generation_id != succeeded_job.generation_id
                or stored_job.fingerprint != succeeded_job.fingerprint
                or stored_job.fencing_token != succeeded_job.fencing_token
                or stored_job.completed_at != completed_at
                or stored_job.chunks_created != succeeded_job.chunks_created
                or stored_job.vectors_created != succeeded_job.vectors_created
            ):
                return None
            if (
                _string(marker_item.get("entityType"), "entityType")
                != _ENTITY_FINGERPRINT
                or _string(marker_item.get("tenantId"), "tenantId")
                != str(generation.tenant_id)
                or _string(marker_item.get("fingerprint"), "fingerprint")
                != generation.fingerprint
                or _string(marker_item.get("jobId"), "jobId")
                != str(succeeded_job.job_id)
                or _string(marker_item.get("status"), "status")
                != IngestionStatus.SUCCEEDED.value
                or _integer(marker_item.get("fencingToken"), "fencingToken")
                != generation.fencing_token
            ):
                return None
            return document
        except Exception:
            return None

    @staticmethod
    def _activation_actions(
        *,
        generation: IndexGeneration,
        succeeded_job: IngestionJob,
        expected_document_revision: int,
        activated_at: datetime,
    ) -> tuple[TransactionAction, ...]:
        completed_at = succeeded_job.completed_at
        if completed_at is None:
            raise RepositoryConflictError("succeeded job must have completed_at")
        return (
            {
                "ConditionCheck": {
                    "Key": _lease_key(
                        generation.tenant_id,
                        generation.document_id,
                        generation.source_version,
                    ),
                    "ConditionExpression": (
                        "entityType = :lease_type AND tenantId = :tenant AND "
                        "documentId = :document AND sourceVersion = :source_version AND "
                        "ownerToken = :owner AND fencingToken = :fencing AND "
                        "expiresAt > :activated"
                    ),
                    "ExpressionAttributeValues": {
                        ":lease_type": _ENTITY_LEASE,
                        ":tenant": str(generation.tenant_id),
                        ":document": str(generation.document_id),
                        ":source_version": generation.source_version,
                        ":owner": str(succeeded_job.job_id),
                        ":fencing": generation.fencing_token,
                        ":activated": _iso(activated_at),
                    },
                }
            },
            {
                "Update": {
                    "Key": _document_key(generation.tenant_id, generation.document_id),
                    "UpdateExpression": (
                        "SET activeGenerationId = :generation, "
                        "activeIndexFingerprint = :fingerprint, "
                        "lastFencingToken = :fencing, #status = :indexed, "
                        "updatedAt = :activated, revision = :next_revision"
                    ),
                    "ConditionExpression": (
                        "entityType = :document_type AND revision = :expected_revision AND "
                        "sourceVersion = :source_version AND lastFencingToken < :fencing"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":generation": generation.generation_id,
                        ":fingerprint": generation.fingerprint,
                        ":fencing": generation.fencing_token,
                        ":indexed": DocumentStatus.INDEXED.value,
                        ":activated": _iso(activated_at),
                        ":next_revision": expected_document_revision + 1,
                        ":expected_revision": expected_document_revision,
                        ":source_version": generation.source_version,
                        ":document_type": _ENTITY_DOCUMENT,
                    },
                }
            },
            {
                "Update": {
                    "Key": _generation_key(
                        generation.tenant_id,
                        generation.document_id,
                        generation.generation_id,
                    ),
                    "UpdateExpression": "SET #status = :active, activatedAt = :activated",
                    "ConditionExpression": (
                        "entityType = :generation_type AND #status = :ready AND "
                        "fingerprint = :fingerprint AND fencingToken = :fencing"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":active": IndexGenerationStatus.ACTIVE.value,
                        ":activated": _iso(activated_at),
                        ":generation_type": _ENTITY_GENERATION,
                        ":ready": IndexGenerationStatus.READY.value,
                        ":fingerprint": generation.fingerprint,
                        ":fencing": generation.fencing_token,
                    },
                }
            },
            {
                "Update": {
                    "Key": _job_key(succeeded_job.tenant_id, succeeded_job.job_id),
                    "UpdateExpression": (
                        "SET #status = :succeeded, chunksCreated = :chunks, "
                        "vectorsCreated = :vectors, completedAt = :completed REMOVE errorCode"
                    ),
                    "ConditionExpression": (
                        "entityType = :job_type AND #status = :running AND "
                        "generationId = :generation AND fingerprint = :fingerprint AND "
                        "fencingToken = :fencing"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":succeeded": IngestionStatus.SUCCEEDED.value,
                        ":chunks": succeeded_job.chunks_created,
                        ":vectors": succeeded_job.vectors_created,
                        ":completed": _iso(completed_at),
                        ":job_type": _ENTITY_JOB,
                        ":running": IngestionStatus.RUNNING.value,
                        ":generation": generation.generation_id,
                        ":fingerprint": generation.fingerprint,
                        ":fencing": generation.fencing_token,
                    },
                }
            },
            {
                "Update": {
                    "Key": _fingerprint_key(generation.tenant_id, generation.fingerprint),
                    "UpdateExpression": "SET #status = :succeeded",
                    "ConditionExpression": "jobId = :job AND fencingToken = :fencing",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":succeeded": IngestionStatus.SUCCEEDED.value,
                        ":job": str(succeeded_job.job_id),
                        ":fencing": generation.fencing_token,
                    },
                }
            },
        )
