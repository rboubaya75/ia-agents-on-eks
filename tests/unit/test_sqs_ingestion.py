import json
from collections.abc import Mapping
from typing import cast

import pytest
from ia_application import IngestionTask, ReceivedIngestionTask
from ia_aws_clients.sqs_ingestion import Boto3SqsClient, SqsIngestionTaskQueue
from ia_domain import DocumentId, JobId, TenantId


class RecordingSqsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.messages: list[dict[str, object]] = []
        self.ready = True
        self.attributes: dict[str, str] = {
            "QueueArn": "arn:aws:sqs:region:account:queue",
            "RedrivePolicy": json.dumps(
                {
                    "deadLetterTargetArn": "arn:aws:sqs:region:account:queue-dlq",
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

    def change_message_visibility(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("visibility", kwargs))
        return {}

    def get_queue_attributes(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("attributes", kwargs))
        if not self.ready:
            raise RuntimeError("queue unavailable")
        return {"Attributes": self.attributes}


def _task() -> IngestionTask:
    return IngestionTask(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        job_id=JobId("job-a"),
    )


def _queue(
    client: RecordingSqsClient,
    *,
    queue_url: str = "https://sqs.example/queue",
) -> SqsIngestionTaskQueue:
    return SqsIngestionTaskQueue(
        cast(Boto3SqsClient, client),
        queue_url=queue_url,
        visibility_timeout_seconds=120,
    )


@pytest.mark.asyncio
async def test_standard_queue_enqueues_typed_json() -> None:
    client = RecordingSqsClient()
    queue = _queue(client)

    await queue.enqueue(_task())

    _, kwargs = client.calls[-1]
    assert kwargs["QueueUrl"] == "https://sqs.example/queue"
    assert IngestionTask.model_validate_json(str(kwargs["MessageBody"])) == _task()
    assert "MessageGroupId" not in kwargs


@pytest.mark.asyncio
async def test_fifo_queue_uses_document_group_and_job_deduplication() -> None:
    client = RecordingSqsClient()
    queue = _queue(client, queue_url="https://sqs.example/queue.fifo")

    await queue.enqueue(_task())

    _, kwargs = client.calls[-1]
    assert kwargs["MessageGroupId"] == "tenant-a:document-a"
    assert kwargs["MessageDeduplicationId"] == "job-a"


@pytest.mark.asyncio
async def test_receive_acknowledge_and_extend_visibility() -> None:
    client = RecordingSqsClient()
    client.messages = [
        {
            "Body": _task().model_dump_json(),
            "ReceiptHandle": "receipt-a",
            "Attributes": {"ApproximateReceiveCount": "1"},
        }
    ]
    queue = _queue(client)

    received = await queue.receive(wait_seconds=10)

    assert received == ReceivedIngestionTask(
        receipt_handle="receipt-a",
        task=_task(),
    )
    assert received is not None
    await queue.extend_visibility(received, timeout_seconds=300)
    await queue.acknowledge(received)
    assert [action for action, _ in client.calls][-2:] == ["visibility", "delete"]
    assert client.calls[-2][1]["VisibilityTimeout"] == 300


@pytest.mark.asyncio
async def test_receive_returns_none_without_messages() -> None:
    queue = _queue(RecordingSqsClient())

    assert await queue.receive(wait_seconds=0) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_seconds", (-1, 21))
async def test_receive_rejects_invalid_long_poll(wait_seconds: int) -> None:
    queue = _queue(RecordingSqsClient())

    with pytest.raises(ValueError, match="between 0 and 20"):
        await queue.receive(wait_seconds=wait_seconds)


@pytest.mark.asyncio
async def test_receive_rejects_message_without_receipt_handle() -> None:
    client = RecordingSqsClient()
    client.messages = [{"Body": _task().model_dump_json()}]
    queue = _queue(client)

    with pytest.raises(RuntimeError, match="receipt handle"):
        await queue.receive(wait_seconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    (
        "not-json",
        '{"tenant_id":"tenant-a"}',
        '{"tenant_id":"tenant-a","document_id":"document-a","job_id":"job-a","extra":1}',
    ),
)
async def test_poison_message_is_left_for_sqs_redrive(body: str) -> None:
    client = RecordingSqsClient()
    client.messages = [
        {
            "Body": body,
            "ReceiptHandle": "receipt-poison",
            "Attributes": {"ApproximateReceiveCount": "3"},
        }
    ]
    queue = _queue(client)

    assert await queue.receive(wait_seconds=0) is None
    assert "delete" not in [action for action, _ in client.calls]
    assert "visibility" not in [action for action, _ in client.calls]


@pytest.mark.asyncio
async def test_receive_rejects_invalid_receive_count() -> None:
    client = RecordingSqsClient()
    client.messages = [
        {
            "Body": _task().model_dump_json(),
            "ReceiptHandle": "receipt-a",
            "Attributes": {"ApproximateReceiveCount": "invalid"},
        }
    ]

    with pytest.raises(RuntimeError, match="receive count"):
        await _queue(client).receive(wait_seconds=0)


@pytest.mark.asyncio
async def test_extend_visibility_rejects_invalid_timeout() -> None:
    queue = _queue(RecordingSqsClient())
    received = ReceivedIngestionTask(receipt_handle="receipt-a", task=_task())

    with pytest.raises(ValueError, match="between 30 and 43200"):
        await queue.extend_visibility(received, timeout_seconds=29)


@pytest.mark.asyncio
async def test_readiness_requires_operational_queue_attributes() -> None:
    client = RecordingSqsClient()
    queue = _queue(client)

    assert await queue.is_ready() is True
    _, kwargs = client.calls[-1]
    assert "RedrivePolicy" in kwargs["AttributeNames"]
    assert "SqsManagedSseEnabled" in kwargs["AttributeNames"]

    client.ready = False
    assert await queue.is_ready() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_attribute",
    ("RedrivePolicy", "SqsManagedSseEnabled"),
)
async def test_readiness_rejects_missing_dlq_or_encryption(missing_attribute: str) -> None:
    client = RecordingSqsClient()
    client.attributes.pop(missing_attribute)

    assert await _queue(client).is_ready() is False


@pytest.mark.asyncio
async def test_fifo_readiness_requires_fifo_queue_attribute() -> None:
    client = RecordingSqsClient()
    fifo_queue = _queue(client, queue_url="https://sqs.example/queue.fifo")

    assert await fifo_queue.is_ready() is False

    client.attributes["FifoQueue"] = "true"
    assert await fifo_queue.is_ready() is True


@pytest.mark.parametrize(
    ("queue_url", "visibility_timeout"),
    (("", 120), ("https://sqs.example/queue", 29), ("https://sqs.example/queue", 43_201)),
)
def test_queue_configuration_validation(
    queue_url: str,
    visibility_timeout: int,
) -> None:
    with pytest.raises(ValueError):
        SqsIngestionTaskQueue(
            cast(Boto3SqsClient, RecordingSqsClient()),
            queue_url=queue_url,
            visibility_timeout_seconds=visibility_timeout,
        )


def test_recording_client_attribute_shape_is_mapping() -> None:
    response = RecordingSqsClient().get_queue_attributes()
    assert isinstance(response.get("Attributes"), Mapping)
