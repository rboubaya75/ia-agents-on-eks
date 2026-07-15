from collections.abc import Mapping
from typing import cast

import pytest
from fastapi.testclient import TestClient
from ia_aws_clients import Boto3DynamoTable
from ia_backend_api import main as backend_main
from ia_backend_api.settings import BackendSettings
from pydantic import ValidationError


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


def _phase_two_environment(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_phase_two_environment_starts_with_documents_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _phase_two_environment(monkeypatch)
    table = ReadyTable()
    observed: list[bool] = []

    def from_table_name(
        _cls: type[Boto3DynamoTable],
        table_name: str,
        *,
        region_name: str | None = None,
    ) -> Boto3DynamoTable:
        del region_name
        assert table_name == "sessions"
        return cast(Boto3DynamoTable, table)

    def create_document_runtime(
        settings: BackendSettings,
    ) -> backend_main.DocumentRuntime | None:
        observed.append(settings.document_api_enabled)
        return None

    monkeypatch.setattr(Boto3DynamoTable, "from_table_name", classmethod(from_table_name))
    monkeypatch.setattr(
        backend_main,
        "create_document_runtime",
        create_document_runtime,
    )

    client = TestClient(backend_main.create_application())

    assert client.get("/api/v1/health/ready").status_code == 200
    assert observed == [False]
    assert client.get("/api/openapi.json").status_code == 200


def test_enabling_documents_requires_complete_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _phase_two_environment(monkeypatch)
    monkeypatch.setenv("IA_DOCUMENT_API_ENABLED", "true")

    with pytest.raises(ValidationError, match="document_control_table"):
        BackendSettings()


def test_disabled_document_settings_remain_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _phase_two_environment(monkeypatch)

    settings = BackendSettings()

    assert settings.document_api_enabled is False
    assert settings.document_control_table is None
    assert settings.document_bucket is None
    assert settings.document_ingestion_queue_url is None
