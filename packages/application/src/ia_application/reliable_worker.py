import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ia_domain import DocumentId, IngestionJob, IngestionStatus, TenantId

from ia_application.documents import (
    DocumentIngestion,
    IngestionTaskQueue,
    ReceivedIngestionTask,
)
from ia_application.ingestion import (
    DocumentNotFoundError,
    DocumentNotReadyError,
    IngestDocumentCommand,
    IngestionError,
    IngestionFailedError,
    IngestionInProgressError,
)
from ia_application.ports import IngestionJobRepository

_RETRYABLE_FAILURE_CODES = frozenset({"INGESTION_FAILED"})


class IngestionHeartbeatError(RuntimeError):
    """Raised when the worker can no longer extend its execution ownership."""


class RenewableDocumentLeaseRepository:
    async def renew(
        self,
        *,
        tenant_id: TenantId,
        document_id: DocumentId,
        source_version: str,
        owner_token: str,
        fencing_token: int,
        execution_token: str,
        expires_at: datetime,
        now: datetime,
    ) -> bool: ...


class VisibilityExtendingIngestionTaskQueue:
    async def extend_visibility(
        self,
        received: ReceivedIngestionTask,
        *,
        timeout_seconds: int,
    ) -> None: ...


class DocumentIngestionWorker:
    def __init__(
        self,
        *,
        jobs: IngestionJobRepository,
        queue: IngestionTaskQueue,
        ingestion: DocumentIngestion,
        leases: RenewableDocumentLeaseRepository | None = None,
        lease_ttl_seconds: int = 900,
        heartbeat_interval_seconds: int = 60,
        visibility_timeout_seconds: int = 900,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        execution_token_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._jobs = jobs
        self._queue = queue
        self._ingestion = ingestion
        self._leases = leases
        self._clock = clock
        self._execution_token_factory = execution_token_factory
        self.configure_timing(
            lease_ttl_seconds=lease_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            visibility_timeout_seconds=visibility_timeout_seconds,
        )

    def configure_timing(
        self,
        *,
        lease_ttl_seconds: int,
        heartbeat_interval_seconds: int,
        visibility_timeout_seconds: int,
    ) -> None:
        if lease_ttl_seconds < 30 or lease_ttl_seconds > 3600:
            msg = "lease_ttl_seconds must be between 30 and 3600"
            raise ValueError(msg)
        if heartbeat_interval_seconds < 1:
            msg = "heartbeat_interval_seconds must be positive"
            raise ValueError(msg)
        if heartbeat_interval_seconds >= lease_ttl_seconds:
            msg = "heartbeat interval must be shorter than the execution lease"
            raise ValueError(msg)
        if visibility_timeout_seconds < lease_ttl_seconds:
            msg = "visibility timeout must be at least the execution lease TTL"
            raise ValueError(msg)
        if heartbeat_interval_seconds >= visibility_timeout_seconds:
            msg = "heartbeat interval must be shorter than the visibility timeout"
            raise ValueError(msg)
        self._lease_ttl_seconds = lease_ttl_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._visibility_timeout_seconds = visibility_timeout_seconds

    async def run_once(self, *, wait_seconds: int = 20) -> bool:
        received = await self._queue.receive(wait_seconds=wait_seconds)
        if received is None:
            return False
        task = received.task
        job = await self._jobs.get(task.tenant_id, task.job_id)
        if job is None or job.document_id != task.document_id:
            await self._queue.acknowledge(received)
            return True
        if job.status is IngestionStatus.SUCCEEDED:
            await self._queue.acknowledge(received)
            return True
        if job.status is IngestionStatus.FAILED and job.error_code not in _RETRYABLE_FAILURE_CODES:
            await self._queue.acknowledge(received)
            return True
        if job.embedding_model_alias is None or job.pipeline_version is None:
            await self._fail_job(job, "INVALID_PENDING_JOB")
            await self._queue.acknowledge(received)
            return True

        command = IngestDocumentCommand(
            tenant_id=job.tenant_id,
            document_id=job.document_id,
            job_id=job.job_id,
            embedding_model_alias=job.embedding_model_alias,
            pipeline_version=job.pipeline_version,
            lease_ttl_seconds=self._lease_ttl_seconds,
            execution_token=self._execution_token_factory(),
        )
        try:
            result = await self._execute_with_heartbeat(received, job, command)
        except IngestionInProgressError:
            return False
        except (DocumentNotFoundError, DocumentNotReadyError):
            await self._fail_job(job, "DOCUMENT_NOT_INGESTABLE")
            await self._queue.acknowledge(received)
            return True
        except IngestionFailedError:
            current = await self._jobs.get(job.tenant_id, job.job_id)
            if (
                current is not None
                and current.status is IngestionStatus.FAILED
                and current.error_code in _RETRYABLE_FAILURE_CODES
            ):
                raise
            if current is None or current.status in {
                IngestionStatus.PENDING,
                IngestionStatus.RUNNING,
            }:
                await self._fail_job(job, "INGESTION_FAILED")
                raise
            await self._queue.acknowledge(received)
            return True
        except IngestionError:
            current = await self._jobs.get(job.tenant_id, job.job_id)
            if current is None or current.status in {
                IngestionStatus.PENDING,
                IngestionStatus.RUNNING,
            }:
                await self._fail_job(job, "INGESTION_FAILED")
            await self._queue.acknowledge(received)
            return True

        if result.job_id != job.job_id:
            await self._save_reused_result(job, result)
        await self._queue.acknowledge(received)
        return True

    async def _execute_with_heartbeat(
        self,
        received: ReceivedIngestionTask,
        job: IngestionJob,
        command: IngestDocumentCommand,
    ) -> IngestionJob:
        ingestion_task = asyncio.create_task(self._ingestion.ingest(command))
        heartbeat_task = asyncio.create_task(self._heartbeat(received, job, command))
        done, _ = await asyncio.wait(
            {ingestion_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if ingestion_task in done:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            return await ingestion_task

        heartbeat_error = heartbeat_task.exception()
        ingestion_task.cancel()
        await asyncio.gather(ingestion_task, return_exceptions=True)
        if heartbeat_error is not None:
            raise heartbeat_error
        raise IngestionHeartbeatError("ingestion heartbeat stopped unexpectedly")

    async def _heartbeat(
        self,
        received: ReceivedIngestionTask,
        job: IngestionJob,
        command: IngestDocumentCommand,
    ) -> None:
        await asyncio.sleep(self._heartbeat_interval_seconds)
        leases = self._leases
        if leases is None:
            candidate = getattr(self._ingestion, "_leases", None)
            if not isinstance(candidate, RenewableDocumentLeaseRepository):
                raise IngestionHeartbeatError("ingestion lease repository cannot be renewed")
            leases = candidate
        if not isinstance(self._queue, VisibilityExtendingIngestionTaskQueue):
            raise IngestionHeartbeatError("ingestion queue cannot extend visibility")

        ownership_confirmed = False
        startup_wait_seconds = 0
        while True:
            current = await self._jobs.get(job.tenant_id, job.job_id)
            if current is None:
                raise IngestionHeartbeatError("ingestion job disappeared during execution")

            renewed = False
            if current.status is IngestionStatus.RUNNING and current.fencing_token is not None:
                now = self._aware_now()
                renewed = await leases.renew(
                    tenant_id=job.tenant_id,
                    document_id=job.document_id,
                    source_version=job.source_version,
                    owner_token=str(job.job_id),
                    fencing_token=current.fencing_token,
                    execution_token=command.execution_token,
                    expires_at=now + timedelta(seconds=self._lease_ttl_seconds),
                    now=now,
                )
            elif current.status is not IngestionStatus.PENDING:
                raise IngestionHeartbeatError("ingestion execution is no longer running")

            if renewed:
                ownership_confirmed = True
            elif ownership_confirmed:
                raise IngestionHeartbeatError("document ingestion lease was lost")
            else:
                startup_wait_seconds += self._heartbeat_interval_seconds
                if startup_wait_seconds >= self._lease_ttl_seconds:
                    raise IngestionHeartbeatError("document ingestion lease was not established")

            await self._queue.extend_visibility(
                received,
                timeout_seconds=self._visibility_timeout_seconds,
            )
            await asyncio.sleep(self._heartbeat_interval_seconds)

    async def _save_reused_result(
        self,
        job: IngestionJob,
        result: IngestionJob,
    ) -> None:
        await self._jobs.save(
            job.model_copy(
                update={
                    "status": result.status,
                    "chunks_created": result.chunks_created,
                    "vectors_created": result.vectors_created,
                    "error_code": result.error_code,
                    "fingerprint": result.fingerprint,
                    "generation_id": result.generation_id,
                    "source_checksum": result.source_checksum,
                    "authorization_checksum": result.authorization_checksum,
                    "embedding_profile_revision": result.embedding_profile_revision,
                    "resolved_embedding_model_id": result.resolved_embedding_model_id,
                    "embedding_dimensions": result.embedding_dimensions,
                    "chunking_version": result.chunking_version,
                    "completed_at": result.completed_at or self._aware_now(),
                }
            )
        )

    async def _fail_job(self, job: IngestionJob, error_code: str) -> None:
        await self._jobs.save(
            job.model_copy(
                update={
                    "status": IngestionStatus.FAILED,
                    "error_code": error_code,
                    "completed_at": self._aware_now(),
                }
            )
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value
