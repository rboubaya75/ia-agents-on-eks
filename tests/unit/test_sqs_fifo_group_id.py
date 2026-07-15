from typing import cast

import pytest
from ia_application import IngestionTask
from ia_aws_clients.sqs_ingestion import Boto3SqsClient, SqsIngestionTaskQueue
from ia_domain import DocumentId, JobId, TenantId


class RecordingSqsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_message(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"MessageId": "message-a"}

    def receive_message(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {}

    def delete_message(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {}

    def change_message_visibility(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {}

    def get_queue_attributes(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {}


def _task(*, tenant_id: str, document_id: str) -> IngestionTask:
    return IngestionTask(
        tenant_id=TenantId(tenant_id),
        document_id=DocumentId(document_id),
        job_id=JobId("job-a"),
    )


def _queue(client: RecordingSqsClient) -> SqsIngestionTaskQueue:
    return SqsIngestionTaskQueue(
        cast(Boto3SqsClient, client),
        queue_url="https://sqs.example/queue.fifo",
        visibility_timeout_seconds=120,
    )


def _last_group_id(client: RecordingSqsClient) -> str:
    value = client.calls[-1]["MessageGroupId"]
    assert isinstance(value, str)
    return value


@pytest.mark.asyncio
async def test_long_unicode_document_group_is_stable_ascii_and_bounded() -> None:
    client = RecordingSqsClient()
    queue = _queue(client)
    tenant_id = chr(0x00E9) * 128
    document_id = chr(0x6587) * 128
    task = _task(tenant_id=tenant_id, document_id=document_id)

    await queue.enqueue(task)
    first = _last_group_id(client)
    await queue.enqueue(task)
    second = _last_group_id(client)

    assert first == second
    assert first.startswith("doc-")
    assert len(first) == 68
    assert first.isascii()


@pytest.mark.asyncio
async def test_document_groups_are_distinct_for_distinct_documents() -> None:
    client = RecordingSqsClient()
    queue = _queue(client)

    await queue.enqueue(_task(tenant_id="t" * 128, document_id="a" * 128))
    first = _last_group_id(client)
    await queue.enqueue(_task(tenant_id="t" * 128, document_id="b" * 128))
    second = _last_group_id(client)

    assert first != second
    assert len(first) <= 128
    assert len(second) <= 128
