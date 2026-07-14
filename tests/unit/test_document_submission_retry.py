import pytest
from ia_application import StartDocumentIngestionCommand
from ia_domain import DocumentId, JobId, TenantId

from tests.unit.test_document_management import _register, _service


@pytest.mark.asyncio
async def test_retry_with_same_upload_session_returns_canonical_pending_job() -> None:
    service, _, _, _, sources, queue, _, _ = _service()
    await service.register(_register())
    command = StartDocumentIngestionCommand(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        job_id=JobId("job-a"),
        upload_session_id="upload-a",
    )

    first = await service.submit_ingestion(command)
    second = await service.submit_ingestion(command)

    assert second == first
    assert sources.promoted_session_id == "upload-a"
    assert len(queue.tasks) == 2
