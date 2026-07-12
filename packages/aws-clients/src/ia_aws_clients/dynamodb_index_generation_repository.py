from ia_application import IndexGenerationRepository
from ia_domain import DocumentId, IndexGeneration, TenantId

from ia_aws_clients.dynamodb_control import DynamoControlTable
from ia_aws_clients.dynamodb_document_codec import (
    _decode_generation,
    _generation_item,
    _generation_key,
)


class DynamoIndexGenerationRepository(IndexGenerationRepository):
    def __init__(self, table: DynamoControlTable) -> None:
        self._table = table

    async def save(self, generation: IndexGeneration) -> None:
        await self._table.put_item(_generation_item(generation))

    async def get(
        self,
        tenant_id: TenantId,
        document_id: DocumentId,
        generation_id: str,
    ) -> IndexGeneration | None:
        item = await self._table.get_item(_generation_key(tenant_id, document_id, generation_id))
        if item is None:
            return None
        generation = _decode_generation(item)
        if (
            generation.tenant_id != tenant_id
            or generation.document_id != document_id
            or generation.generation_id != generation_id
        ):
            return None
        return generation
