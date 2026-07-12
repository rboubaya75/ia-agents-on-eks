from ia_application import DocumentRepository, RepositoryConflictError
from ia_domain import Document, DocumentId, TenantId

from ia_aws_clients.dynamodb_control import (
    DynamoConditionFailedError,
    DynamoControlTable,
)
from ia_aws_clients.dynamodb_document_codec import (
    _decode_document,
    _document_item,
    _document_key,
)


class DynamoDocumentRepository(DocumentRepository):
    def __init__(self, table: DynamoControlTable) -> None:
        self._table = table

    async def save(
        self,
        document: Document,
        *,
        expected_revision: int | None = None,
    ) -> Document:
        if expected_revision is None:
            current = await self.get(document.tenant_id, document.document_id)
            if current is None:
                try:
                    await self._table.put_item(
                        _document_item(document),
                        condition_expression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
                    )
                except DynamoConditionFailedError:
                    current = await self.get(document.tenant_id, document.document_id)
                    if current is None:
                        raise RepositoryConflictError(
                            "document create conflicted"
                        ) from None
                else:
                    return document
            if current is None:
                raise RepositoryConflictError("document create conflicted")
            expected_revision = current.revision

        stored = document.model_copy(update={"revision": expected_revision + 1})
        try:
            await self._table.put_item(
                _document_item(stored),
                condition_expression="#revision = :expected",
                expression_attribute_names={"#revision": "revision"},
                expression_attribute_values={":expected": expected_revision},
            )
        except DynamoConditionFailedError as error:
            raise RepositoryConflictError("document revision changed") from error
        return stored

    async def get(
        self, tenant_id: TenantId, document_id: DocumentId
    ) -> Document | None:
        item = await self._table.get_item(_document_key(tenant_id, document_id))
        if item is None:
            return None
        document = _decode_document(item)
        if document.tenant_id != tenant_id or document.document_id != document_id:
            return None
        return document
