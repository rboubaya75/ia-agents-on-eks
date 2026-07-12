from datetime import UTC, datetime

import pytest
from ia_domain import (
    ChunkId,
    Classification,
    Document,
    DocumentChunk,
    DocumentId,
    DocumentStatus,
    Role,
    TenantId,
    UserId,
)
from pydantic import ValidationError

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def test_document_requires_at_least_one_allowed_role() -> None:
    with pytest.raises(ValidationError):
        Document(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            owner_user_id=UserId("user-a"),
            title="Policy",
            source_uri="s3://documents/tenant-a/document-a/v1/source.pdf",
            source_version="v1",
            source_checksum="a" * 64,
            content_type="application/pdf",
            language="fr",
            classification=Classification.INTERNAL,
            allowed_roles=frozenset(),
            status=DocumentStatus.UPLOADED,
            created_at=NOW,
            updated_at=NOW,
        )


def test_document_chunk_rejects_invalid_normalized_offsets() -> None:
    with pytest.raises(ValidationError):
        DocumentChunk(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("document-a"),
            chunk_id=ChunkId("chunk-a"),
            generation_id="generation-a",
            source_version="v1",
            source_uri="s3://documents/tenant-a/document-a/v1/source.pdf",
            title="Policy",
            section="Refunds",
            language="fr",
            classification=Classification.INTERNAL,
            allowed_roles=frozenset({Role.USER}),
            checksum="b" * 64,
            content="content",
            created_at=NOW,
            start_offset=10,
            end_offset=10,
        )
