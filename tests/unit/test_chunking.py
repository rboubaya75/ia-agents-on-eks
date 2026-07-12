from datetime import UTC, datetime

from ia_application import ChunkingConfig, ExtractedDocument, ExtractedSection, ParagraphChunker
from ia_domain import (
    Classification,
    Document,
    DocumentId,
    DocumentStatus,
    Role,
    TenantId,
    UserId,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _document() -> Document:
    return Document(
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
        allowed_roles=frozenset({Role.USER}),
        status=DocumentStatus.UPLOADED,
        created_at=NOW,
        updated_at=NOW,
    )


def test_paragraph_chunker_is_deterministic_and_preserves_metadata() -> None:
    chunker = ParagraphChunker(
        ChunkingConfig(
            max_characters=120,
            overlap_characters=20,
            minimum_characters=30,
            version="paragraph-test-v1",
        )
    )
    extracted = ExtractedDocument(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        sections=(
            ExtractedSection(
                title="Refunds",
                page_number=2,
                content=(
                    "Refunds are processed within five business days. "
                    "Additional verification can extend the processing time.\n\n"
                    "Digital products follow a separate validation process. "
                    "A manager must approve exceptional refunds."
                ),
            ),
        ),
    )

    first = chunker.chunk(_document(), extracted, created_at=NOW)
    second = chunker.chunk(_document(), extracted, created_at=NOW)

    assert len(first) >= 2
    assert first == second
    assert tuple(chunk.sequence for chunk in first) == tuple(range(len(first)))
    assert all(chunk.section == "Refunds" for chunk in first)
    assert all(chunk.page_number == 2 for chunk in first)
    assert all(chunk.chunking_version == "paragraph-test-v1" for chunk in first)
    assert all(len(chunk.checksum) == 64 for chunk in first)
    assert len({chunk.chunk_id for chunk in first}) == len(first)
