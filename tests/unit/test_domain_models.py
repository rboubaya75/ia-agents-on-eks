from datetime import UTC, datetime
from decimal import Decimal

import pytest
from ia_domain import (
    ChatMessage,
    ChunkId,
    Citation,
    DocumentChunk,
    DocumentId,
    MessageId,
    MessageRole,
    Role,
    SessionId,
    TenantId,
    UserId,
)
from ia_domain.models import Classification
from pydantic import ValidationError


def test_citation_rejects_score_above_one() -> None:
    with pytest.raises(ValidationError):
        Citation(
            document_id=DocumentId("doc-1"),
            title="Title",
            section="Section",
            source_uri="s3://bucket/doc-1",
            chunk_id=ChunkId("chunk-1"),
            score=1.1,
        )


def test_chat_message_is_strict_and_frozen() -> None:
    message = ChatMessage(
        tenant_id=TenantId("tenant-a"),
        session_id=SessionId("session-1"),
        message_id=MessageId("message-1"),
        user_id=UserId("user-1"),
        role=MessageRole.USER,
        content="hello",
        estimated_cost_usd=Decimal("0"),
        created_at=datetime.now(UTC),
    )

    with pytest.raises(ValidationError):
        ChatMessage.model_validate({**message.model_dump(), "unexpected": "field"})

    with pytest.raises(ValidationError):
        message.__setattr__("input_tokens", 4)


def test_chat_message_rejects_negative_token_count() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(
            tenant_id=TenantId("tenant-a"),
            session_id=SessionId("session-1"),
            message_id=MessageId("message-1"),
            user_id=UserId("user-1"),
            role=MessageRole.USER,
            content="hello",
            input_tokens=-1,
            estimated_cost_usd=Decimal("0"),
            created_at=datetime.now(UTC),
        )


def test_domain_models_reject_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(
            tenant_id=TenantId("tenant-a"),
            session_id=SessionId("session-1"),
            message_id=MessageId("message-1"),
            user_id=UserId("user-1"),
            role=MessageRole.USER,
            content="hello",
            estimated_cost_usd=Decimal("0"),
            created_at=datetime(2026, 1, 1),
        )


def test_document_chunk_requires_allowed_role() -> None:
    with pytest.raises(ValidationError):
        DocumentChunk(
            tenant_id=TenantId("tenant-a"),
            document_id=DocumentId("doc-1"),
            chunk_id=ChunkId("chunk-1"),
            source_version="1",
            source_uri="s3://bucket/doc-1",
            title="Title",
            section="Section",
            language="fr",
            classification=Classification.INTERNAL,
            allowed_roles=frozenset(),
            checksum="a" * 64,
            content="content",
            created_at=datetime.now(UTC),
        )


def test_document_chunk_accepts_non_empty_roles() -> None:
    chunk = DocumentChunk(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("doc-1"),
        chunk_id=ChunkId("chunk-1"),
        source_version="1",
        source_uri="s3://bucket/doc-1",
        title="Title",
        section="Section",
        language="fr",
        classification=Classification.INTERNAL,
        allowed_roles=frozenset({Role.USER}),
        checksum="a" * 64,
        content="content",
        created_at=datetime.now(UTC),
    )
    assert chunk.allowed_roles == frozenset({Role.USER})
