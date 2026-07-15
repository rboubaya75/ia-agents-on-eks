import json
from typing import cast

import pytest
from ia_application import DocumentDeletionTask, ReceivedDocumentDeletionTask
from ia_aws_clients.sqs_deletion import (
    Boto3DeletionSqsClient,
    SqsDocumentDeletionTaskQueue,
)
from ia_domain import DocumentId, TenantId


class RecordingDeletionSqsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.messages: list[dict[str, object]] = []
        self.attributes: dict[str, str] = {
            "QueueArn": "arn:aws:sqs:region:account:deletion",
            "RedrivePolicy": json.dumps(
                {
                    "deadLetterTargetArn": "arn:aws:sqs:region:account:deletion-dlq",
                    "maxReceiveCount": "5",
                }
            ),
            "SqsManagedSseEnabled": "true",
            "FifoQueue": "false",
        }

    def send_message(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("send", kwargs))
        return {"MessageId": "message-a"}

    def receive_message(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("receive", kwargs))
        return {"Messages": self.messages}

    def delete_message(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("delete", kwargs))
        return {}

    def get_queue_attributes(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("attributes", kwargs))
        return {"Attributes": self.attributes}


def _task() -> DocumentDeletionTask:
    return DocumentDeletionTask(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        operation_id="delete-a",
    )


def _queue(
    client: RecordingDeletionSqsClient,
    *,
    queue_url: str = "https://sqs.example/deletion",
) -> SqsDocumentDeletionTaskQueue:
    return SqsDocumentDeletionTaskQueue(
        cast(Boto3DeletionSqsClient, client),
        queue_url=queue_url,
        visibility_timeout_seconds=300,
    )


@pytest.mark.asyncio
async def test_deletion_queue_enqueues_and_acknowledges_typed_task() -> None:
    client = RecordingDeletionSqsClient()
    queue = _queue(client)

    await queue.enqueue(_task())
    _, sent = client.calls[-1]
    assert DocumentDeletionTask.model_validate_json(str(sent["MessageBody"])) == _task()

    client.messages = [
        {
            "Body": _task().model_dump_json(),
            "ReceiptHandle": "receipt-a",
            "Attributes": {"ApproximateReceiveCount": "1"},
        }
    ]
    received = await queue.receive(wait_seconds=0)
    assert received == ReceivedDocumentDeletionTask(
        receipt_handle="receipt-a",
        task=_task(),
    )
    assert received is not None
    await queue.acknowledge(received)
    assert client.calls[-1][0] == "delete"


@pytest.mark.asyncio
async def test_fifo_deletion_queue_uses_document_group_and_operation_deduplication() -> None:
    client = RecordingDeletionSqsClient()
    queue = _queue(client, queue_url="https://sqs.example/deletion.fifo")

    await queue.enqueue(_task())

    _, kwargs = client.calls[-1]
    assert kwargs["MessageGroupId"] == "tenant-a:document-a"
    assert kwargs["MessageDeduplicationId"] == "delete-a"


@pytest.mark.asyncio
async def test_poison_deletion_message_is_left_for_redrive() -> None:
    client = RecordingDeletionSqsClient()
    client.messages = [
        {
            "Body": "not-json",
            "ReceiptHandle": "receipt-poison",
            "Attributes": {"ApproximateReceiveCount": "3"},
        }
    ]

    assert await _queue(client).receive(wait_seconds=0) is None
    assert "delete" not in [action for action, _ in client.calls]


@pytest.mark.asyncio
async def test_deletion_queue_readiness_requires_dlq_and_encryption() -> None:
    client = RecordingDeletionSqsClient()
    queue = _queue(client)

    assert await queue.is_ready() is True

    client.attributes.pop("RedrivePolicy")
    assert await queue.is_ready() is False
