import pytest
from ia_domain import DocumentId, DocumentStatus, TenantId

from tests.unit.phase3_ingestion_support import environment


@pytest.mark.asyncio
async def test_failed_worker_does_not_restore_document_over_deleting_state() -> None:
    value = await environment()
    original = await value.documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert original is not None
    deleting = await value.documents.save(
        original.model_copy(update={"status": DocumentStatus.DELETING}),
        expected_revision=original.revision,
    )

    await value.service._best_effort_restore_document_state(original)

    current = await value.documents.get(TenantId("tenant-a"), DocumentId("document-a"))
    assert current == deleting
    assert current.status is DocumentStatus.DELETING
