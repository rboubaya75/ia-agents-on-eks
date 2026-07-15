import hashlib

from ia_domain import Document

from ia_application.documents import (
    DocumentSourceStore,
    InvalidDocumentSourceError,
    UnsupportedDocumentContentTypeError,
)
from ia_application.ingestion import InvalidExtractionError
from ia_application.ports import ExtractedDocument, ExtractedSection, TextExtractor

_SUPPORTED_CONTENT_TYPES = frozenset({"text/plain", "text/markdown"})


class InvalidDocumentContentError(InvalidExtractionError, InvalidDocumentSourceError):
    """Terminal content validation error visible to ingestion and document APIs."""


class Utf8DocumentExtractor(TextExtractor):
    """Extract UTF-8 text and classify malformed content as terminal ingestion failure."""

    def __init__(self, sources: DocumentSourceStore, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            msg = "max_bytes must be positive"
            raise ValueError(msg)
        self._sources = sources
        self._max_bytes = max_bytes

    async def extract(self, document: Document) -> ExtractedDocument:
        if document.content_type not in _SUPPORTED_CONTENT_TYPES:
            raise UnsupportedDocumentContentTypeError(
                f"unsupported document content type: {document.content_type}"
            )
        payload = await self._sources.read(document, max_bytes=self._max_bytes)
        if not payload or len(payload) > self._max_bytes:
            raise InvalidDocumentContentError("document source size is invalid")
        checksum = hashlib.sha256(payload).hexdigest()
        if checksum != document.source_checksum:
            raise InvalidDocumentContentError("document source checksum does not match metadata")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise InvalidDocumentContentError("document source is not valid UTF-8") from error
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if any(
            (ord(character) < 32 and character not in {"\n", "\t", "\f"}) or ord(character) == 127
            for character in normalized
        ):
            raise InvalidDocumentContentError(
                "document source contains unsupported control characters"
            )
        normalized = normalized.strip()
        if not normalized:
            raise InvalidDocumentContentError("document source contains no indexable text")
        return ExtractedDocument(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            source_version=document.source_version,
            sections=(ExtractedSection(title=document.title, content=normalized),),
        )
