import hashlib
import re
from datetime import datetime
from typing import Annotated

from ia_domain import ChunkId, Document, DocumentChunk
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ia_application.ports import ExtractedDocument

_WHITESPACE = re.compile(r"[ \t\f\v]+")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n+")


class ChunkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_characters: Annotated[int, Field(ge=100, le=50_000)] = 1_200
    overlap_characters: Annotated[int, Field(ge=0, le=10_000)] = 150
    minimum_characters: Annotated[int, Field(ge=1, le=10_000)] = 80
    version: Annotated[str, Field(min_length=1, max_length=128)] = "paragraph-v1"

    @model_validator(mode="after")
    def validate_ranges(self) -> "ChunkingConfig":
        if self.overlap_characters >= self.max_characters:
            msg = "overlap_characters must be smaller than max_characters"
            raise ValueError(msg)
        if self.minimum_characters > self.max_characters:
            msg = "minimum_characters must not exceed max_characters"
            raise ValueError(msg)
        return self


class ParagraphChunker:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or ChunkingConfig()

    @property
    def version(self) -> str:
        return self._config.version

    def chunk(
        self,
        document: Document,
        extracted: ExtractedDocument,
        *,
        generation_id: str,
        created_at: datetime,
    ) -> tuple[DocumentChunk, ...]:
        self._validate_identity(document, extracted)
        chunks: list[DocumentChunk] = []
        sequence = 0
        for section in extracted.sections:
            normalized = self._normalize(section.content)
            if not normalized:
                continue
            for start, end, content in self._windows(normalized):
                checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
                chunk_id = self._chunk_id(
                    document=document,
                    sequence=sequence,
                    section=section.title,
                    content=content,
                )
                chunks.append(
                    DocumentChunk(
                        tenant_id=document.tenant_id,
                        document_id=document.document_id,
                        chunk_id=ChunkId(chunk_id),
                        generation_id=generation_id,
                        source_version=document.source_version,
                        source_uri=document.source_uri,
                        title=document.title,
                        section=section.title,
                        language=document.language,
                        classification=document.classification,
                        allowed_roles=document.allowed_roles,
                        checksum=checksum,
                        content=content,
                        created_at=created_at,
                        sequence=sequence,
                        start_offset=start,
                        end_offset=end,
                        chunking_version=self._config.version,
                        page_number=section.page_number,
                    )
                )
                sequence += 1
        return tuple(chunks)

    @staticmethod
    def _validate_identity(document: Document, extracted: ExtractedDocument) -> None:
        if (
            document.tenant_id != extracted.tenant_id
            or document.document_id != extracted.document_id
            or document.source_version != extracted.source_version
        ):
            msg = "extracted document identity does not match document metadata"
            raise ValueError(msg)

    @staticmethod
    def _normalize(value: str) -> str:
        paragraphs = []
        for raw_paragraph in _PARAGRAPH_BREAK.split(value.strip()):
            collapsed = _WHITESPACE.sub(" ", raw_paragraph.replace("\n", " ")).strip()
            if collapsed:
                paragraphs.append(collapsed)
        return "\n\n".join(paragraphs)

    def _windows(self, text: str) -> tuple[tuple[int, int, str], ...]:
        windows: list[tuple[int, int, str]] = []
        cursor = 0
        text_length = len(text)
        while cursor < text_length:
            proposed_end = min(cursor + self._config.max_characters, text_length)
            end = self._preferred_boundary(text, cursor, proposed_end)
            if end <= cursor:
                end = proposed_end
            content = text[cursor:end].strip()
            if content:
                content_start = text.find(content, cursor, end)
                content_end = content_start + len(content)
                windows.append((content_start, content_end, content))
            if end >= text_length:
                break
            next_cursor = max(0, end - self._config.overlap_characters)
            cursor = end if next_cursor <= cursor else next_cursor
        return tuple(windows)

    def _preferred_boundary(self, text: str, start: int, proposed_end: int) -> int:
        if proposed_end >= len(text):
            return proposed_end
        minimum_end = min(start + self._config.minimum_characters, proposed_end)
        paragraph_end = text.rfind("\n\n", minimum_end, proposed_end)
        if paragraph_end >= minimum_end:
            return paragraph_end
        word_end = text.rfind(" ", minimum_end, proposed_end)
        return word_end if word_end >= minimum_end else proposed_end

    def _chunk_id(
        self,
        *,
        document: Document,
        sequence: int,
        section: str,
        content: str,
    ) -> str:
        material = "\x00".join(
            (
                str(document.tenant_id),
                str(document.document_id),
                document.source_version,
                self._config.version,
                str(sequence),
                section,
                content,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
