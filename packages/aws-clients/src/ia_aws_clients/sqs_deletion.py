import asyncio
import json
from collections.abc import Mapping
from typing import Protocol, cast

from ia_application.deletion import (
    DocumentDeletionTask,
    DocumentDeletionTaskQueue,
    ReceivedDocumentDeletionTask,
)
from pydantic import ValidationError


class Boto3DeletionSqsClient(Protocol):
    def send_message(self, **kwargs: object) -> dict[str, object]: ...

    def receive_message(self, **kwargs: object) -> dict[str, object]: ...

    def delete_message(self, **kwargs: object) -> dict[str, object]: ...

    def get_queue_attributes(self, **kwargs: object) -> dict[str, object]: ...


class SqsDocumentDeletionTaskQueue(DocumentDeletionTaskQueue):
    def __init__(
        self,
        client: Boto3DeletionSqsClient,
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
    ) -> "SqsDocumentDeletionTaskQueue":
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("sqs", region_name=region_name)
        return cls(
            cast(Boto3DeletionSqsClient, client),
            queue_url=queue_url,
            visibility_timeout_seconds=visibility_timeout_seconds,
        )

    async def enqueue(self, task: DocumentDeletionTask) -> None:
        kwargs: dict[str, object] = {
            "QueueUrl": self._queue_url,
            "MessageBody": task.model_dump_json(),
        }
        if self._queue_url.endswith(".fifo"):
            kwargs.update(
                {
                    "MessageGroupId": f"{task.tenant_id}:{task.document_id}",
                    "MessageDeduplicationId": task.operation_id,
                }
            )
        await asyncio.to_thread(self._client.send_message, **kwargs)

    async def receive(
        self,
        *,
        wait_seconds: int,
    ) -> ReceivedDocumentDeletionTask | None:
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
            raise RuntimeError("SQS returned an invalid deletion message")
        receipt_handle = message.get("ReceiptHandle")
        if not isinstance(receipt_handle, str) or not receipt_handle:
            raise RuntimeError("SQS deletion message is missing a receipt handle")
        self._validate_receive_count(message)
        body = message.get("Body")
        if not isinstance(body, str):
            return None
        try:
            task = DocumentDeletionTask.model_validate_json(body)
        except ValidationError:
            return None
        return ReceivedDocumentDeletionTask(
            receipt_handle=receipt_handle,
            task=task,
        )

    async def acknowledge(self, received: ReceivedDocumentDeletionTask) -> None:
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
                AttributeNames=[
                    "QueueArn",
                    "RedrivePolicy",
                    "KmsMasterKeyId",
                    "SqsManagedSseEnabled",
                    "FifoQueue",
                ],
            )
        except Exception:
            return False
        attributes = response.get("Attributes")
        return isinstance(attributes, Mapping) and self._operational_attributes_ready(attributes)

    def _operational_attributes_ready(self, attributes: Mapping[object, object]) -> bool:
        queue_arn = attributes.get("QueueArn")
        if not isinstance(queue_arn, str) or not queue_arn:
            return False
        redrive_policy = attributes.get("RedrivePolicy")
        if not isinstance(redrive_policy, str) or not redrive_policy:
            return False
        try:
            policy = json.loads(redrive_policy)
        except (TypeError, ValueError):
            return False
        if not isinstance(policy, Mapping):
            return False
        dead_letter_arn = policy.get("deadLetterTargetArn")
        raw_receive_count = policy.get("maxReceiveCount")
        if not isinstance(dead_letter_arn, str) or not dead_letter_arn:
            return False
        if isinstance(raw_receive_count, bool) or not isinstance(raw_receive_count, (str, int)):
            return False
        try:
            max_receive_count = int(raw_receive_count)
        except ValueError:
            return False
        if max_receive_count < 1 or max_receive_count > 1_000:
            return False
        kms_key_id = attributes.get("KmsMasterKeyId")
        managed_sse = attributes.get("SqsManagedSseEnabled")
        encrypted = (isinstance(kms_key_id, str) and bool(kms_key_id)) or managed_sse == "true"
        if not encrypted:
            return False
        expected_fifo = self._queue_url.endswith(".fifo")
        actual_fifo = attributes.get("FifoQueue") == "true"
        return actual_fifo is expected_fifo

    @staticmethod
    def _validate_receive_count(message: Mapping[object, object]) -> None:
        attributes = message.get("Attributes")
        if not isinstance(attributes, Mapping):
            return
        raw_count = attributes.get("ApproximateReceiveCount")
        if raw_count is None:
            return
        if not isinstance(raw_count, str) or not raw_count.isdigit():
            raise RuntimeError("SQS deletion message has an invalid receive count")
