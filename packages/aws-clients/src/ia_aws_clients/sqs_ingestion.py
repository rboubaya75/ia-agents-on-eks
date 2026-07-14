import asyncio
from collections.abc import Mapping
from typing import Protocol, cast

from ia_application import (
    IngestionTask,
    IngestionTaskQueue,
    ReceivedIngestionTask,
)


class Boto3SqsClient(Protocol):
    def send_message(self, **kwargs: object) -> dict[str, object]: ...

    def receive_message(self, **kwargs: object) -> dict[str, object]: ...

    def delete_message(self, **kwargs: object) -> dict[str, object]: ...

    def get_queue_attributes(self, **kwargs: object) -> dict[str, object]: ...


class SqsIngestionTaskQueue(IngestionTaskQueue):
    def __init__(
        self,
        client: Boto3SqsClient,
        *,
        queue_url: str,
        visibility_timeout_seconds: int = 900,
    ) -> None:
        if not queue_url:
            msg = "queue_url must not be empty"
            raise ValueError(msg)
        if visibility_timeout_seconds < 30 or visibility_timeout_seconds > 43_200:
            msg = "visibility timeout must be between 30 and 43200 seconds"
            raise ValueError(msg)
        self._client = client
        self._queue_url = queue_url
        self._visibility_timeout_seconds = visibility_timeout_seconds

    @classmethod
    def from_queue_url(
        cls,
        queue_url: str,
        *,
        visibility_timeout_seconds: int = 900,
        region_name: str | None = None,
    ) -> "SqsIngestionTaskQueue":
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("sqs", region_name=region_name)
        return cls(
            cast(Boto3SqsClient, client),
            queue_url=queue_url,
            visibility_timeout_seconds=visibility_timeout_seconds,
        )

    async def enqueue(self, task: IngestionTask) -> None:
        kwargs: dict[str, object] = {
            "QueueUrl": self._queue_url,
            "MessageBody": task.model_dump_json(),
        }
        if self._queue_url.endswith(".fifo"):
            kwargs.update(
                {
                    "MessageGroupId": f"{task.tenant_id}:{task.document_id}",
                    "MessageDeduplicationId": str(task.job_id),
                }
            )
        await asyncio.to_thread(self._client.send_message, **kwargs)

    async def receive(self, *, wait_seconds: int) -> ReceivedIngestionTask | None:
        if wait_seconds < 0 or wait_seconds > 20:
            msg = "SQS wait_seconds must be between 0 and 20"
            raise ValueError(msg)
        response = await asyncio.to_thread(
            self._client.receive_message,
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=self._visibility_timeout_seconds,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = response.get("Messages")
        if not isinstance(messages, list) or not messages:
            return None
        message = messages[0]
        if not isinstance(message, Mapping):
            msg = "SQS returned an invalid message"
            raise RuntimeError(msg)
        body = message.get("Body")
        receipt_handle = message.get("ReceiptHandle")
        if not isinstance(body, str) or not isinstance(receipt_handle, str):
            msg = "SQS message is missing body or receipt handle"
            raise RuntimeError(msg)
        return ReceivedIngestionTask(
            receipt_handle=receipt_handle,
            task=IngestionTask.model_validate_json(body),
        )

    async def acknowledge(self, received: ReceivedIngestionTask) -> None:
        await asyncio.to_thread(
            self._client.delete_message,
            QueueUrl=self._queue_url,
            ReceiptHandle=received.receipt_handle,
        )

    async def is_ready(self) -> bool:
        try:
            response = await asyncio.to_thread(
                self._client.get_queue_attributes,
                QueueUrl=self._queue_url,
                AttributeNames=["QueueArn"],
            )
        except Exception:
            return False
        attributes = response.get("Attributes")
        return (
            isinstance(attributes, Mapping)
            and isinstance(attributes.get("QueueArn"), str)
            and bool(attributes.get("QueueArn"))
        )
