from decimal import Decimal
from typing import Any, cast

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from ia_aws_clients.dynamodb_control import (
    Boto3DynamoControlTable,
    Boto3DynamoClient,
    DynamoConditionFailedError,
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
        client_request_token="stable-token",
    )

    assert item is not None
    assert item["pk"] == "tenant"
    assert cast(Decimal, item["count"]) == Decimal("2")
    name, kwargs = client.calls[-1]
    assert name == "transact"
    assert kwargs["ClientRequestToken"] == "stable-token"
    assert kwargs["TransactItems"] == [
        {
            "Put": {
                "TableName": "control",
                "Item": {"pk": {"S": "tenant"}, "count": {"N": "2"}},
            }
        }
    ]


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
