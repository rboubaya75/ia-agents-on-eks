from typing import cast

import pytest
from ia_aws_clients import Boto3DynamoTable


class SyncTable:
    def __init__(self) -> None:
        self.item: dict[str, object] | None = {"tenantId": "tenant-a"}
        self.loaded = False
        self.fail_load = False
        self.last_call: tuple[str, dict[str, object]] | None = None

    def get_item(self, **kwargs: object) -> dict[str, object]:
        self.last_call = ("get", kwargs)
        return {} if self.item is None else {"Item": self.item}

    def put_item(self, **kwargs: object) -> dict[str, object]:
        self.last_call = ("put", kwargs)
        return {}

    def delete_item(self, **kwargs: object) -> dict[str, object]:
        self.last_call = ("delete", kwargs)
        return {} if self.item is None else {"Attributes": self.item}

    def query(self, **kwargs: object) -> dict[str, object]:
        self.last_call = ("query", kwargs)
        return {"Items": [self.item] if self.item is not None else []}

    def load(self) -> None:
        if self.fail_load:
            raise RuntimeError("unavailable")
        self.loaded = True


@pytest.mark.asyncio
async def test_boto3_table_facade_wraps_blocking_operations() -> None:
    sync_table = SyncTable()
    table = Boto3DynamoTable(sync_table)

    assert await table.get_item({"tenantId": "tenant-a"}) == {"tenantId": "tenant-a"}
    await table.put_item({"tenantId": "tenant-a"})
    assert sync_table.last_call is not None
    assert sync_table.last_call[0] == "put"
    assert await table.delete_item({"tenantId": "tenant-a"}) == {"tenantId": "tenant-a"}
    assert await table.query_items(key_name="tenantId", key_value="tenant-a") == (
        {"tenantId": "tenant-a"},
    )
    assert await table.query_items(
        key_name="tenantUserKey",
        key_value="tenant-a#user-a",
        index_name="tenant-user-index",
        scan_forward=False,
    ) == ({"tenantId": "tenant-a"},)
    assert await table.ping() is True
    assert sync_table.loaded is True


@pytest.mark.asyncio
async def test_boto3_table_facade_handles_missing_items_and_failed_readiness() -> None:
    sync_table = SyncTable()
    sync_table.item = None
    table = Boto3DynamoTable(sync_table)

    assert await table.get_item({"tenantId": "tenant-a"}) is None
    assert await table.delete_item({"tenantId": "tenant-a"}) is None
    assert await table.query_items(key_name="tenantId", key_value="tenant-a") == ()
    sync_table.fail_load = True
    assert await table.ping() is False


def test_table_name_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="table_name"):
        Boto3DynamoTable.from_table_name("")


def test_sync_table_satisfies_runtime_shape() -> None:
    table = SyncTable()
    assert cast(object, table) is table
