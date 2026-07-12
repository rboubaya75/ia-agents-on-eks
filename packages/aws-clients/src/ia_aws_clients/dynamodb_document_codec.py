import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from ia_application import IngestionLease
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

from ia_aws_clients.dynamodb_control import PythonItem

_ENTITY_DOCUMENT = "Document"
_ENTITY_JOB = "IngestionJob"
_ENTITY_FINGERPRINT = "IngestionFingerprint"
_ENTITY_LEASE = "IngestionLease"
_ENTITY_GENERATION = "IndexGeneration"


def _composite_key(*parts: str) -> str:
    if not parts or any(not part for part in parts):
        msg = "composite key parts must not be empty"
        raise ValueError(msg)
    return "".join(f"{len(part)}:{part}" for part in parts)


def _transaction_token(*parts: str) -> str:
    if not parts or any(not part for part in parts):
        msg = "transaction token parts must not be empty"
        raise ValueError(msg)
    material = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:36]


def _tenant_pk(tenant_id: TenantId) -> str:
    return _composite_key("TENANT", str(tenant_id))


def _document_key(tenant_id: TenantId, document_id: DocumentId) -> PythonItem:
    return {
        "pk": _tenant_pk(tenant_id),
        "sk": _composite_key("DOCUMENT", str(document_id)),
    }


def _job_key(tenant_id: TenantId, job_id: JobId) -> PythonItem:
    return {
        "pk": _tenant_pk(tenant_id),
        "sk": _composite_key("JOB", str(job_id)),
    }


def _fingerprint_key(tenant_id: TenantId, fingerprint: str) -> PythonItem:
    return {
        "pk": _tenant_pk(tenant_id),
        "sk": _composite_key("FINGERPRINT", fingerprint),
    }


def _lease_key(
    tenant_id: TenantId,
    document_id: DocumentId,
    source_version: str,
) -> PythonItem:
    return {
        "pk": _tenant_pk(tenant_id),
        "sk": _composite_key("LEASE", str(document_id), source_version),
    }


def _generation_key(
    tenant_id: TenantId,
    document_id: DocumentId,
    generation_id: str,
) -> PythonItem:
    return {
        "pk": _tenant_pk(tenant_id),
        "sk": _composite_key("GENERATION", str(document_id), generation_id),
    }


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "timestamp must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        msg = f"stored field must be an ISO-8601 timestamp: {field_name}"
        raise ValueError(msg)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = f"stored timestamp must include a timezone: {field_name}"
        raise ValueError(msg)
    return parsed


def _optional_datetime(value: object, field_name: str) -> datetime | None:
    return None if value is None else _datetime(value, field_name)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"stored field must be a non-empty string: {field_name}"
        raise ValueError(msg)
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    return None if value is None else _string(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        msg = f"stored field must be an integer: {field_name}"
        raise ValueError(msg)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    msg = f"stored field must be an integer: {field_name}"
    raise ValueError(msg)


def _optional_integer(value: object, field_name: str) -> int | None:
    return None if value is None else _integer(value, field_name)


def _roles(value: object) -> frozenset[Role]:
    if not isinstance(value, list):
        msg = "stored allowedRoles must be a list"
        raise ValueError(msg)
    return frozenset(Role(_string(item, "allowedRoles")) for item in value)


def _without_none(value: Mapping[str, object]) -> PythonItem:
    return {key: item for key, item in value.items() if item is not None}


def _document_item(document: Document) -> PythonItem:
    return _without_none(
        {
            **_document_key(document.tenant_id, document.document_id),
            "entityType": _ENTITY_DOCUMENT,
            "tenantId": str(document.tenant_id),
            "documentId": str(document.document_id),
            "ownerUserId": str(document.owner_user_id),
            "title": document.title,
            "sourceUri": document.source_uri,
            "sourceVersion": document.source_version,
            "sourceChecksum": document.source_checksum,
            "contentType": document.content_type,
            "language": document.language,
            "classification": document.classification.value,
            "allowedRoles": sorted(role.value for role in document.allowed_roles),
            "status": document.status.value,
            "revision": document.revision,
            "activeGenerationId": document.active_generation_id,
            "activeIndexFingerprint": document.active_index_fingerprint,
            "lastFencingToken": document.last_fencing_token,
            "createdAt": _iso(document.created_at),
            "updatedAt": _iso(document.updated_at),
        }
    )


def _decode_document(item: Mapping[str, object]) -> Document:
    return Document(
        tenant_id=TenantId(_string(item.get("tenantId"), "tenantId")),
        document_id=DocumentId(_string(item.get("documentId"), "documentId")),
        owner_user_id=UserId(_string(item.get("ownerUserId"), "ownerUserId")),
        title=_string(item.get("title"), "title"),
        source_uri=_string(item.get("sourceUri"), "sourceUri"),
        source_version=_string(item.get("sourceVersion"), "sourceVersion"),
        source_checksum=_string(item.get("sourceChecksum"), "sourceChecksum"),
        content_type=_string(item.get("contentType"), "contentType"),
        language=_string(item.get("language"), "language"),
        classification=Classification(_string(item.get("classification"), "classification")),
        allowed_roles=_roles(item.get("allowedRoles")),
        status=DocumentStatus(_string(item.get("status"), "status")),
        revision=_integer(item.get("revision"), "revision"),
        active_generation_id=_optional_string(item.get("activeGenerationId"), "activeGenerationId"),
        active_index_fingerprint=_optional_string(
            item.get("activeIndexFingerprint"), "activeIndexFingerprint"
        ),
        last_fencing_token=_integer(item.get("lastFencingToken"), "lastFencingToken"),
        created_at=_datetime(item.get("createdAt"), "createdAt"),
        updated_at=_datetime(item.get("updatedAt"), "updatedAt"),
    )


def _job_item(job: IngestionJob) -> PythonItem:
    return _without_none(
        {
            **_job_key(job.tenant_id, job.job_id),
            "entityType": _ENTITY_JOB,
            "tenantId": str(job.tenant_id),
            "jobId": str(job.job_id),
            "documentId": str(job.document_id),
            "sourceVersion": job.source_version,
            "status": job.status.value,
            "chunksCreated": job.chunks_created,
            "vectorsCreated": job.vectors_created,
            "errorCode": job.error_code,
            "fingerprint": job.fingerprint,
            "generationId": job.generation_id,
            "sourceChecksum": job.source_checksum,
            "authorizationChecksum": job.authorization_checksum,
            "embeddingModelAlias": job.embedding_model_alias,
            "embeddingProfileRevision": job.embedding_profile_revision,
            "resolvedEmbeddingModelId": job.resolved_embedding_model_id,
            "embeddingDimensions": job.embedding_dimensions,
            "chunkingVersion": job.chunking_version,
            "pipelineVersion": job.pipeline_version,
            "fencingToken": job.fencing_token,
            "startedAt": _iso(job.started_at),
            "completedAt": None if job.completed_at is None else _iso(job.completed_at),
        }
    )


def _decode_job(item: Mapping[str, object]) -> IngestionJob:
    return IngestionJob(
        tenant_id=TenantId(_string(item.get("tenantId"), "tenantId")),
        job_id=JobId(_string(item.get("jobId"), "jobId")),
        document_id=DocumentId(_string(item.get("documentId"), "documentId")),
        source_version=_string(item.get("sourceVersion"), "sourceVersion"),
        status=IngestionStatus(_string(item.get("status"), "status")),
        chunks_created=_integer(item.get("chunksCreated"), "chunksCreated"),
        vectors_created=_integer(item.get("vectorsCreated"), "vectorsCreated"),
        error_code=_optional_string(item.get("errorCode"), "errorCode"),
        fingerprint=_optional_string(item.get("fingerprint"), "fingerprint"),
        generation_id=_optional_string(item.get("generationId"), "generationId"),
        source_checksum=_optional_string(item.get("sourceChecksum"), "sourceChecksum"),
        authorization_checksum=_optional_string(
            item.get("authorizationChecksum"), "authorizationChecksum"
        ),
        embedding_model_alias=_optional_string(
            item.get("embeddingModelAlias"), "embeddingModelAlias"
        ),
        embedding_profile_revision=_optional_string(
            item.get("embeddingProfileRevision"), "embeddingProfileRevision"
        ),
        resolved_embedding_model_id=_optional_string(
            item.get("resolvedEmbeddingModelId"), "resolvedEmbeddingModelId"
        ),
        embedding_dimensions=_optional_integer(
            item.get("embeddingDimensions"), "embeddingDimensions"
        ),
        chunking_version=_optional_string(item.get("chunkingVersion"), "chunkingVersion"),
        pipeline_version=_optional_string(item.get("pipelineVersion"), "pipelineVersion"),
        fencing_token=_optional_integer(item.get("fencingToken"), "fencingToken"),
        started_at=_datetime(item.get("startedAt"), "startedAt"),
        completed_at=_optional_datetime(item.get("completedAt"), "completedAt"),
    )


def _generation_item(generation: IndexGeneration) -> PythonItem:
    return _without_none(
        {
            **_generation_key(
                generation.tenant_id,
                generation.document_id,
                generation.generation_id,
            ),
            "entityType": _ENTITY_GENERATION,
            "tenantId": str(generation.tenant_id),
            "documentId": str(generation.document_id),
            "sourceVersion": generation.source_version,
            "generationId": generation.generation_id,
            "fingerprint": generation.fingerprint,
            "authorizationChecksum": generation.authorization_checksum,
            "embeddingProfileRevision": generation.embedding_profile_revision,
            "embeddingModelId": generation.embedding_model_id,
            "embeddingDimensions": generation.embedding_dimensions,
            "status": generation.status.value,
            "fencingToken": generation.fencing_token,
            "chunkCount": generation.chunk_count,
            "vectorCount": generation.vector_count,
            "createdAt": _iso(generation.created_at),
            "readyAt": None if generation.ready_at is None else _iso(generation.ready_at),
            "activatedAt": (
                None if generation.activated_at is None else _iso(generation.activated_at)
            ),
        }
    )


def _decode_generation(item: Mapping[str, object]) -> IndexGeneration:
    return IndexGeneration(
        tenant_id=TenantId(_string(item.get("tenantId"), "tenantId")),
        document_id=DocumentId(_string(item.get("documentId"), "documentId")),
        source_version=_string(item.get("sourceVersion"), "sourceVersion"),
        generation_id=_string(item.get("generationId"), "generationId"),
        fingerprint=_string(item.get("fingerprint"), "fingerprint"),
        authorization_checksum=_string(item.get("authorizationChecksum"), "authorizationChecksum"),
        embedding_profile_revision=_string(
            item.get("embeddingProfileRevision"), "embeddingProfileRevision"
        ),
        embedding_model_id=_string(item.get("embeddingModelId"), "embeddingModelId"),
        embedding_dimensions=_integer(item.get("embeddingDimensions"), "embeddingDimensions"),
        status=IndexGenerationStatus(_string(item.get("status"), "status")),
        fencing_token=_integer(item.get("fencingToken"), "fencingToken"),
        chunk_count=_integer(item.get("chunkCount"), "chunkCount"),
        vector_count=_integer(item.get("vectorCount"), "vectorCount"),
        created_at=_datetime(item.get("createdAt"), "createdAt"),
        ready_at=_optional_datetime(item.get("readyAt"), "readyAt"),
        activated_at=_optional_datetime(item.get("activatedAt"), "activatedAt"),
    )


def _decode_lease(item: Mapping[str, object]) -> IngestionLease:
    return IngestionLease(
        tenant_id=TenantId(_string(item.get("tenantId"), "tenantId")),
        document_id=DocumentId(_string(item.get("documentId"), "documentId")),
        source_version=_string(item.get("sourceVersion"), "sourceVersion"),
        owner_token=_string(item.get("ownerToken"), "ownerToken"),
        fencing_token=_integer(item.get("fencingToken"), "fencingToken"),
        expires_at=_datetime(item.get("expiresAt"), "expiresAt"),
    )
