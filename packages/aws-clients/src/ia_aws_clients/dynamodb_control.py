import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]


type PythonItem = dict[str, object]
type TransactionAction = dict[str, object]


class DynamoConditionFailedError(RuntimeError):
    """Raised when a DynamoDB conditional or transactional write is rejected."""


class Boto3DynamoClient(Protocol):
    def get_item(self, **kwargs: object) -> dict[str, object]: ...

    def put_item(self, **kwargs: object) -> dict[str, object]: ...

    def update_item(self, **kwargs: object) -> dict[str, object]: ...

    def delete_item(self, **kwargs: object) -> dict[str, object]: ...

    def transact_write_items(self, **kwargs: object) -> dict[str, object]: ...


class DynamoControlTable(Protocol):
    @property
    def table_name(self) -> str: ...

    async def get_item(self, key: Mapping[str, object]) -> PythonItem | None: ...

    async def put_item(
        self,
        item: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None: ...

    async def update_item(
        self,
        key: Mapping[str, object],
        *,
        update_expression: str,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> PythonItem: ...

    async def delete_item(
        self,
        key: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None: ...

    async def transact_write(
        self,
        actions: Sequence[TransactionAction],
        *,
        client_request_token: str | None = None,
    ) -> None: ...


class Boto3DynamoControlTable:
    """Typed async facade over one low-level DynamoDB control-plane table."""

    def __init__(self, client: Boto3DynamoClient, *, table_name: str) -> None:
        if not table_name:
            msg = "table_name must not be empty"
            raise ValueError(msg)
        self._client = client
        self._table_name = table_name
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    @classmethod
    def from_table_name(
        cls,
        table_name: str,
        *,
        region_name: str | None = None,
    ) -> "Boto3DynamoControlTable":
        import boto3  # type: ignore[import-untyped]

        client = boto3.client("dynamodb", region_name=region_name)
        return cls(cast(Boto3DynamoClient, client), table_name=table_name)

    @property
    def table_name(self) -> str:
        return self._table_name

    async def get_item(self, key: Mapping[str, object]) -> PythonItem | None:
        response = await asyncio.to_thread(
            self._client.get_item,
            TableName=self._table_name,
            Key=self._serialize_map(key),
            ConsistentRead=True,
        )
        raw_item = response.get("Item")
        if not isinstance(raw_item, dict):
            return None
        return self._deserialize_map(cast(dict[str, object], raw_item))

    async def put_item(
        self,
        item: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        kwargs: dict[str, object] = {
            "TableName": self._table_name,
            "Item": self._serialize_map(item),
        }
        self._add_expressions(
            kwargs,
            condition_expression=condition_expression,
            expression_attribute_names=expression_attribute_names,
            expression_attribute_values=expression_attribute_values,
        )
        await self._call(self._client.put_item, **kwargs)

    async def update_item(
        self,
        key: Mapping[str, object],
        *,
        update_expression: str,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> PythonItem:
        kwargs: dict[str, object] = {
            "TableName": self._table_name,
            "Key": self._serialize_map(key),
            "UpdateExpression": update_expression,
            "ReturnValues": "ALL_NEW",
        }
        self._add_expressions(
            kwargs,
            condition_expression=condition_expression,
            expression_attribute_names=expression_attribute_names,
            expression_attribute_values=expression_attribute_values,
        )
        response = await self._call(self._client.update_item, **kwargs)
        raw_attributes = response.get("Attributes")
        if not isinstance(raw_attributes, dict):
            msg = "DynamoDB update did not return attributes"
            raise RuntimeError(msg)
        return self._deserialize_map(cast(dict[str, object], raw_attributes))

    async def delete_item(
        self,
        key: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        kwargs: dict[str, object] = {
            "TableName": self._table_name,
            "Key": self._serialize_map(key),
        }
        self._add_expressions(
            kwargs,
            condition_expression=condition_expression,
            expression_attribute_names=expression_attribute_names,
            expression_attribute_values=expression_attribute_values,
        )
        await self._call(self._client.delete_item, **kwargs)

    async def transact_write(
        self,
        actions: Sequence[TransactionAction],
        *,
        client_request_token: str | None = None,
    ) -> None:
        if not actions:
            msg = "at least one transaction action is required"
            raise ValueError(msg)
        serialized = tuple(
            self._serialize_transaction_action(action) for action in actions
        )
        kwargs: dict[str, object] = {"TransactItems": list(serialized)}
        if client_request_token is not None:
            if not 1 <= len(client_request_token) <= 36:
                msg = "client_request_token must contain between 1 and 36 characters"
                raise ValueError(msg)
            kwargs["ClientRequestToken"] = client_request_token
        await self._call(self._client.transact_write_items, **kwargs)

    async def _call(self, operation: object, **kwargs: object) -> dict[str, object]:
        try:
            callable_operation = cast("Boto3Operation", operation)
            return await asyncio.to_thread(callable_operation, **kwargs)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code == "ConditionalCheckFailedException":
                raise DynamoConditionFailedError(
                    "DynamoDB condition rejected the write"
                ) from error
            if (
                code == "TransactionCanceledException"
                and self._is_condition_cancellation(error)
            ):
                raise DynamoConditionFailedError(
                    "DynamoDB transaction condition rejected the write"
                ) from error
            raise

    @staticmethod
    def _is_condition_cancellation(error: ClientError) -> bool:
        reasons = error.response.get("CancellationReasons")
        if not isinstance(reasons, list):
            return False
        codes = {
            str(reason.get("Code"))
            for reason in reasons
            if isinstance(reason, Mapping) and reason.get("Code") not in {None, "None"}
        }
        return bool(codes) and codes.issubset({"ConditionalCheckFailed"})

    def _serialize_transaction_action(
        self, action: TransactionAction
    ) -> TransactionAction:
        if len(action) != 1:
            msg = "a transaction action must contain exactly one operation"
            raise ValueError(msg)
        operation_name, raw_payload = next(iter(action.items()))
        if operation_name not in {"Put", "Update", "Delete", "ConditionCheck"}:
            msg = f"unsupported transaction action: {operation_name}"
            raise ValueError(msg)
        if not isinstance(raw_payload, Mapping):
            msg = "transaction action payload must be a mapping"
            raise TypeError(msg)
        payload = dict(raw_payload)
        payload["TableName"] = self._table_name
        if "Item" in payload:
            payload["Item"] = self._serialize_map(
                cast(Mapping[str, object], payload["Item"])
            )
        if "Key" in payload:
            payload["Key"] = self._serialize_map(
                cast(Mapping[str, object], payload["Key"])
            )
        values = payload.get("ExpressionAttributeValues")
        if isinstance(values, Mapping):
            payload["ExpressionAttributeValues"] = self._serialize_map(
                cast(Mapping[str, object], values)
            )
        return {operation_name: payload}

    def _serialize_map(self, value: Mapping[str, object]) -> dict[str, object]:
        return {key: self._serializer.serialize(item) for key, item in value.items()}

    def _deserialize_map(self, value: Mapping[str, object]) -> PythonItem:
        return {
            key: self._deserializer.deserialize(cast(dict[str, object], item))
            for key, item in value.items()
        }

    def _add_expressions(
        self,
        kwargs: dict[str, object],
        *,
        condition_expression: str | None,
        expression_attribute_names: Mapping[str, str] | None,
        expression_attribute_values: Mapping[str, object] | None,
    ) -> None:
        if condition_expression is not None:
            kwargs["ConditionExpression"] = condition_expression
        if expression_attribute_names:
            kwargs["ExpressionAttributeNames"] = dict(expression_attribute_names)
        if expression_attribute_values:
            kwargs["ExpressionAttributeValues"] = self._serialize_map(
                expression_attribute_values
            )


class Boto3Operation(Protocol):
    def __call__(self, **kwargs: object) -> dict[str, object]: ...
