from ia_domain import Document, DocumentStatus

from ia_application.ingestion import (
    DocumentIngestionService,
    DocumentNotReadyError,
)


class RecoverableDocumentIngestionService(DocumentIngestionService):
    """Ingestion service variant that permits fenced crash recovery."""

    @staticmethod
    def _ensure_ingestable(document: Document) -> None:
        if document.status not in {
            DocumentStatus.UPLOADED,
            DocumentStatus.PROCESSING,
            DocumentStatus.FAILED,
            DocumentStatus.INDEXED,
        }:
            raise DocumentNotReadyError("document state does not permit ingestion")
