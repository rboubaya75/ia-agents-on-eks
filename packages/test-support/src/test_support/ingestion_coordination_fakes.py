from datetime import datetime

from ia_application import (
    IngestionJobClaim,
    IngestionLease,
    IngestionLeaseClaim,
    RepositoryConflictError,
)
from ia_domain import (
    Document,
    DocumentId,
    DocumentStatus,
    IndexGeneration,
    IndexGenerationStatus,
    IngestionJob,
    IngestionStatus,
    JobId,
    TenantId,
)

from test_support.index_storage_fakes import (
    InMemoryDocumentRepository,
    InMemoryIndexGenerationRepository,
)


class InMemoryDocumentIngestionLeaseRepository:
    def __init__(self) -> None:
        self._leases: dict[tuple[TenantId, DocumentId, str], IngestionLease] = {}
        self._fencing_tokens: dict[tuple[TenantId, DocumentId, str], int] = {}

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
        key = (tenant_id, document_id, source_version)
        current = self._leases.get(key)
        if current is not None and current.expires_at > now:
            return IngestionLeaseClaim(
                lease=current,
                acquired=current.owner_token == owner_token,
            )
        fencing_token = self._fencing_tokens.get(key, 0) + 1
        self._fencing_tokens[key] = fencing_token
        lease = IngestionLease(
            tenant_id=tenant_id,
            document_id=document_id,
            source_version=source_version,
            owner_token=owner_token,
            fencing_token=fencing_token,
            expires_at=expires_at,
        )
        self._leases[key] = lease
        return IngestionLeaseClaim(lease=lease, acquired=True)

    async def release(self, lease: IngestionLease) -> None:
        key = (lease.tenant_id, lease.document_id, lease.source_version)
        current = self._leases.get(key)
        if current == lease:
            del self._leases[key]


class InMemoryIngestionJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[tuple[TenantId, JobId], IngestionJob] = {}
        self._fingerprints: dict[tuple[TenantId, str], JobId] = {}

    async def save(self, job: IngestionJob) -> None:
        if job.fingerprint is not None and job.fencing_token is not None:
            existing = await self.find_by_fingerprint(job.tenant_id, job.fingerprint)
            if existing is not None:
                if existing.job_id != job.job_id or existing.fencing_token != job.fencing_token:
                    raise RepositoryConflictError("stale ingestion job state update was rejected")
                if (
                    existing.status is IngestionStatus.SUCCEEDED
                    and job.status is not IngestionStatus.SUCCEEDED
                ):
                    raise RepositoryConflictError("succeeded ingestion job cannot be overwritten")
        self._store(job)

    async def claim(self, job: IngestionJob) -> IngestionJobClaim:
        if job.fingerprint is None or job.fencing_token is None:
            msg = "claimed ingestion jobs require a fingerprint and fencing token"
            raise ValueError(msg)
        existing = await self.find_by_fingerprint(job.tenant_id, job.fingerprint)
        if existing is not None and existing.status is IngestionStatus.SUCCEEDED:
            return IngestionJobClaim(job=existing, acquired=False)
        if (
            existing is not None
            and existing.status is IngestionStatus.RUNNING
            and existing.fencing_token is not None
            and existing.fencing_token >= job.fencing_token
        ):
            return IngestionJobClaim(job=existing, acquired=False)
        self._store(job)
        return IngestionJobClaim(job=job, acquired=True)

    async def get(self, tenant_id: TenantId, job_id: JobId) -> IngestionJob | None:
        return self._jobs.get((tenant_id, job_id))

    async def find_by_fingerprint(
        self, tenant_id: TenantId, fingerprint: str
    ) -> IngestionJob | None:
        job_id = self._fingerprints.get((tenant_id, fingerprint))
        return None if job_id is None else self._jobs.get((tenant_id, job_id))

    def _store(self, job: IngestionJob) -> None:
        self._jobs[(job.tenant_id, job.job_id)] = job
        if job.fingerprint is not None:
            self._fingerprints[(job.tenant_id, job.fingerprint)] = job.job_id


class InMemoryIndexActivationRepository:
    def __init__(
        self,
        *,
        documents: InMemoryDocumentRepository,
        generations: InMemoryIndexGenerationRepository,
        jobs: InMemoryIngestionJobRepository,
    ) -> None:
        self._documents = documents
        self._generations = generations
        self._jobs = jobs

    async def activate(
        self,
        *,
        generation: IndexGeneration,
        succeeded_job: IngestionJob,
        expected_document_revision: int,
        activated_at: datetime,
    ) -> Document:
        document = await self._documents.get(generation.tenant_id, generation.document_id)
        if document is None:
            raise RepositoryConflictError("document does not exist")
        if document.revision != expected_document_revision:
            raise RepositoryConflictError("document revision changed")
        if document.source_version != generation.source_version:
            raise RepositoryConflictError("document source version changed")
        if generation.status is not IndexGenerationStatus.READY:
            raise RepositoryConflictError("index generation is not ready")
        if generation.fencing_token <= document.last_fencing_token:
            raise RepositoryConflictError("stale ingestion fencing token")
        if (
            succeeded_job.generation_id != generation.generation_id
            or succeeded_job.fingerprint != generation.fingerprint
            or succeeded_job.status is not IngestionStatus.SUCCEEDED
        ):
            raise RepositoryConflictError("activation metadata is inconsistent")

        activated_document = await self._documents.save(
            document.model_copy(
                update={
                    "active_generation_id": generation.generation_id,
                    "active_index_fingerprint": generation.fingerprint,
                    "last_fencing_token": generation.fencing_token,
                    "status": DocumentStatus.INDEXED,
                    "updated_at": activated_at,
                }
            ),
            expected_revision=expected_document_revision,
        )
        await self._generations.save(
            generation.model_copy(
                update={
                    "status": IndexGenerationStatus.ACTIVE,
                    "activated_at": activated_at,
                }
            )
        )
        await self._jobs.save(succeeded_job)
        return activated_document
