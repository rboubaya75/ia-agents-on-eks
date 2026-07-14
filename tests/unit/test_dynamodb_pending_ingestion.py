from typing import cast

import pytest
from ia_application import RepositoryConflictError
from ia_aws_clients.dynamodb_control import DynamoControlTable
from ia_aws_clients.dynamodb_documents import DynamoIngestionJobRepository
from ia_domain import DocumentId, IngestionJob, IngestionStatus, JobId, TenantId

from tests.unit.test_dynamodb_document_adapters import (
    NOW,
    RecordingControlTable,
    _job,
    _operation,
)


def _pending() -> IngestionJob:
    return IngestionJob(
        tenant_id=TenantId("tenant-a"),
        job_id=JobId("job-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        status=IngestionStatus.PENDING,
        source_checksum="a" * 64,
        embedding_model_alias="default",
        pipeline_version="pipeline-v1",
        started_at=NOW,
    )


@pytest.mark.asyncio
async def test_dynamo_pending_submission_is_conditional_and_idempotent() -> None:
    table = RecordingControlTable()
    repository = DynamoIngestionJobRepository(cast(DynamoControlTable, table))

    first = await repository.submit(_pending())
    second = await repository.submit(_pending())

    assert first.acquired is True
    assert second.acquired is False
    assert second.job == _pending()


@pytest.mark.asyncio
async def test_dynamo_pending_job_transitions_to_fenced_running_claim() -> None:
    table = RecordingControlTable()
    repository = DynamoIngestionJobRepository(cast(DynamoControlTable, table))
    await repository.submit(_pending())

    claim = await repository.claim(_job())

    assert claim.acquired is True
    actions, token = table.transactions[-1]
    assert token is not None
    job_put = _operation(actions[0], "Put")
    condition = str(job_put["ConditionExpression"])
    assert "#status = :pending" in condition
    assert "documentId = :document" in condition
    assert "sourceVersion = :source" in condition


@pytest.mark.asyncio
async def test_dynamo_submission_rejects_job_id_reuse_for_another_document() -> None:
    table = RecordingControlTable()
    repository = DynamoIngestionJobRepository(cast(DynamoControlTable, table))
    await repository.submit(_pending())
    conflicting = _pending().model_copy(
        update={"document_id": DocumentId("document-b")}
    )

    with pytest.raises(RepositoryConflictError, match="conflicts"):
        await repository.submit(conflicting)
