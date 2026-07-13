from collections.abc import Mapping
from typing import cast

import pytest
from fastapi.testclient import TestClient
from ia_application import DocumentManagement
from ia_aws_clients import Boto3DynamoTable
from ia_backend_api import main as backend_main
from ia_backend_api.settings import BackendSettings


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
    monkeypatch.setenv("IA_CHAT_SESSION_USER_INDEX", "tenant-user-index")
    monkeypatch.setenv("IA_CHAT_MESSAGE_TABLE", "messages")
    monkeypatch.setenv("IA_USAGE_RECORD_TABLE", "usage")
    monkeypatch.setenv("IA_DOCUMENT_CONTROL_TABLE", "document-control")
    monkeypatch.setenv("IA_DOCUMENT_BUCKET", "document-bucket")
    monkeypatch.setenv("IA_VECTOR_BUCKET_NAME", "vector-bucket")
    monkeypatch.setenv("IA_VECTOR_INDEX_NAME", "documents")
    monkeypatch.setenv("IA_EMBEDDING_PROFILE_REVISION", "profile-v1")
    monkeypatch.setenv("IA_EMBEDDING_MODEL_ID", "model-v1")
    monkeypatch.setenv("IA_EMBEDDING_DIMENSIONS", "256")
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

    document_management = cast(DocumentManagement, object())

    def create_document_management(settings: BackendSettings) -> DocumentManagement:
        assert settings.document_control_table == "document-control"
        assert settings.document_bucket == "document-bucket"
        return document_management

    monkeypatch.setattr(Boto3DynamoTable, "from_table_name", classmethod(from_table_name))
    monkeypatch.setattr(
        backend_main,
        "create_document_management",
        create_document_management,
    )

    client = TestClient(backend_main.create_application())

    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
