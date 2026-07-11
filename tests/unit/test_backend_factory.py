from collections.abc import Mapping
from typing import cast

import pytest
from fastapi.testclient import TestClient
from ia_aws_clients import Boto3DynamoTable
from ia_backend_api.main import create_application


class ReadyTable:
    async def get_item(self, key: Mapping[str, object]) -> dict[str, object] | None:
        del key
        return None

    async def put_item(self, item: Mapping[str, object]) -> None:
        del item

    async def delete_item(self, key: Mapping[str, object]) -> dict[str, object] | None:
        del key
        return None

    async def query_items(
        self,
        *,
        key_name: str,
        key_value: str,
        index_name: str | None = None,
        scan_forward: bool = True,
    ) -> tuple[dict[str, object], ...]:
        del key_name, key_value, index_name, scan_forward
        return ()

    async def ping(self) -> bool:
        return True


def test_environment_factory_wires_cognito_and_dynamodb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "IA_COGNITO_ISSUER",
        "https://cognito-idp.eu-west-3.amazonaws.com/eu-west-3_example",
    )
    monkeypatch.setenv("IA_COGNITO_CLIENT_ID", "client-123")
    monkeypatch.setenv("IA_USER_PROFILE_TABLE", "profiles")
    monkeypatch.setenv("IA_CHAT_SESSION_TABLE", "sessions")
    monkeypatch.setenv("IA_CHAT_MESSAGE_TABLE", "messages")
    monkeypatch.setenv("IA_USAGE_RECORD_TABLE", "usage")
    table = ReadyTable()

    def from_table_name(
        _cls: type[Boto3DynamoTable],
        table_name: str,
        *,
        region_name: str | None = None,
    ) -> Boto3DynamoTable:
        del region_name
        assert table_name == "sessions"
        return cast(Boto3DynamoTable, table)

    monkeypatch.setattr(Boto3DynamoTable, "from_table_name", classmethod(from_table_name))

    client = TestClient(create_application())

    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
