import hashlib
from datetime import UTC, datetime
from typing import cast

import pytest
from ia_application import (
    DocumentSourceStore,
    InvalidDocumentSourceError,
    InvalidExtractionError,
    Utf8DocumentExtractor,
)
from ia_domain import (
    Classification,
    Document,
    DocumentId,
    DocumentStatus,
    Role,
    TenantId,
    UserId,
)

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


class SourcePayload:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def read(self, document: Document, *, max_bytes: int) -> bytes:
        del document
        return self.payload[: max_bytes + 1]


def _document(payload: bytes, *, checksum: str | None = None) -> Document:
    return Document(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        owner_user_id=UserId("admin-a"),
        title="Policy",
        source_uri="s3://bucket/source",
        source_version="source-a",
        source_checksum=checksum or hashlib.sha256(payload).hexdigest(),
        content_type="text/plain",
        language="fr",
        classification=Classification.INTERNAL,
        allowed_roles=frozenset({Role.USER}),
        status=DocumentStatus.UPLOADED,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "checksum", "message"),
    (
        (b"", None, "size"),
        (b"\xff", None, "UTF-8"),
        (b"valid\x00invalid", None, "control"),
        (b"valid text", "0" * 64, "checksum"),
    ),
)
async def test_invalid_document_content_is_a_terminal_ingestion_error(
    payload: bytes,
    checksum: str | None,
    message: str,
) -> None:
    extractor = Utf8DocumentExtractor(
        cast(DocumentSourceStore, SourcePayload(payload)),
        max_bytes=1_000,
    )

    with pytest.raises(InvalidExtractionError, match=message) as raised:
        await extractor.extract(_document(payload, checksum=checksum))

    assert isinstance(raised.value, InvalidDocumentSourceError)
