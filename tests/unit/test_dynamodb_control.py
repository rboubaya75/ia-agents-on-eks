from decimal import Decimal
from typing import Any, cast

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from ia_aws_clients.dynamodb_control import (
    Boto3DynamoClient,
    Boto3DynamoControlTable,
    DynamoConditionFailedError,
    TransactionAction,
    transaction_payload_token,
)


class RecordingDynamoClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error: ClientError | None = None

    def get_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get", kwargs))
        return {"Item": {"pk": {"S": "tenant"}, "count": {"N": "2"}}}

    def put_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("put", kwargs))
        if self.error is not None:
            raise self.error
        return {}

    def update_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("update", kwargs))
        return {"Attributes": {"pk": {"S": "tenant"}}}

    def delete_item(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("delete", kwargs))
        return {}

    def transact_write_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("transact", kwargs))
        if self.error is not None:
            raise self.error
        return {}


def _client_error(
    code: str,
    *,
    reasons: list[dict[str, object]] | None = None,
) -> ClientError:
    response: dict[str, object] = {"Error": {"Code": code, "Message": code}}
    if reasons is not None:
        response["CancellationReasons"] = reasons
    return ClientError(cast(dict[str, Any], response), "Test")


@pytest.mark.asyncio
async def test_control_table_serializes_values_and_transaction_token() -> None:
    client = RecordingDynamoClient()
    table = Boto3DynamoControlTable(
        cast(Boto3DynamoClient, client),
        table_name="control",
    )

    item = await table.get_item({"pk": "tenant"})
    await table.transact_write(
        ({"Put": {"Item": {"pk": "tenant", "count": 2}}},),
        client_request_token="request-token",  # noqa: S106
    )

    assert item is not None
    assert item["pk"] == "tenant"
    assert cast(Decimal, item["count"]) == Decimal("2")
    name, kwargs = client.calls[-1]
    assert name == "transact"
    assert kwargs["ClientRequestToken"] == "request-token"
    assert kwargs["TransactItems"] == [
        {
            "Put": {
                "TableName": "control",
                "Item": {"pk": {"S": "tenant"}, "count": {"N": "2"}},
            }
        }
    ]


def test_transaction_payload_token_is_stable_for_identical_serialized_payloads() -> None:
    first: tuple[TransactionAction, ...] = (
        {
            "Put": {
                "Item": {
                    "pk": "tenant",
                    "startedAt": "2026-07-12T12:00:00.000000Z",
                    "roles": {"support", "admin"},
                    "payload": b"payload",
                },
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        },
    )
    reordered: tuple[TransactionAction, ...] = (
        {
            "Put": {
                "ConditionExpression": "attribute_not_exists(pk)",
                "Item": {
                    "payload": b"payload",
                    "roles": {"admin", "support"},
                    "startedAt": "2026-07-12T12:00:00.000000Z",
                    "pk": "tenant",
                },
            }
        },
    )

    first_token = transaction_payload_token(
        namespace="claim-ingestion-job",
        table_name="control",
        actions=first,
    )
    reordered_token = transaction_payload_token(
        namespace="claim-ingestion-job",
        table_name="control",
        actions=reordered,
    )

    assert len(first_token) == 36
    assert first_token == reordered_token


def test_transaction_payload_token_changes_with_any_request_parameter() -> None:
    base: tuple[TransactionAction, ...] = (
        {
            "Put": {
                "Item": {
                    "pk": "tenant",
                    "startedAt": "2026-07-12T12:00:00.000000Z",
                }
            }
        },
    )
    changed_timestamp: tuple[TransactionAction, ...] = (
        {
            "Put": {
                "Item": {
                    "pk": "tenant",
                    "startedAt": "2026-07-12T12:00:01.000000Z",
                }
            }
        },
    )

    base_token = transaction_payload_token(
        namespace="claim-ingestion-job",
        table_name="control",
        actions=base,
    )

    assert base_token != transaction_payload_token(
        namespace="claim-ingestion-job",
        table_name="control",
        actions=changed_timestamp,
    )
    assert base_token != transaction_payload_token(
        namespace="activate-index-generation",
        table_name="control",
        actions=base,
    )
    assert base_token != transaction_payload_token(
        namespace="claim-ingestion-job",
        table_name="other-control",
        actions=base,
    )


@pytest.mark.parametrize(
    ("namespace", "table_name", "actions", "message"),
    [
        ("", "control", ({"Delete": {"Key": {"pk": "tenant"}}},), "namespace"),
        ("claim", "", ({"Delete": {"Key": {"pk": "tenant"}}},), "table_name"),
        ("claim", "control", (), "actions"),
    ],
)
def test_transaction_payload_token_rejects_incomplete_inputs(
    namespace: str,
    table_name: str,
    actions: tuple[TransactionAction, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        transaction_payload_token(
            namespace=namespace,
            table_name=table_name,
            actions=actions,
        )


@pytest.mark.asyncio
async def test_only_condition_cancellations_map_to_repository_conflict() -> None:
    client = RecordingDynamoClient()
    table = Boto3DynamoControlTable(
        cast(Boto3DynamoClient, client),
        table_name="control",
    )
    client.error = _client_error(
        "TransactionCanceledException",
        reasons=[{"Code": "ConditionalCheckFailed"}],
    )
    with pytest.raises(DynamoConditionFailedError):
        await table.transact_write(({"Delete": {"Key": {"pk": "tenant"}}},))

    client.error = _client_error(
        "TransactionCanceledException",
        reasons=[{"Code": "ProvisionedThroughputExceeded"}],
    )
    with pytest.raises(ClientError):
        await table.transact_write(({"Delete": {"Key": {"pk": "tenant"}}},))


@pytest.mark.asyncio
async def test_transaction_token_length_is_bounded() -> None:
    client = RecordingDynamoClient()
    table = Boto3DynamoControlTable(
        cast(Boto3DynamoClient, client),
        table_name="control",
    )
    with pytest.raises(ValueError, match="36"):
        await table.transact_write(
            ({"Delete": {"Key": {"pk": "tenant"}}},),
            client_request_token="x" * 37,
        )
