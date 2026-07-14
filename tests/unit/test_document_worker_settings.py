import pytest
from ia_backend_api.settings import BackendSettings
from pydantic import ValidationError


def _settings(**overrides: object) -> BackendSettings:
    values: dict[str, object] = {
        "cognito_issuer": "https://issuer.example",
        "cognito_client_id": "client-a",
        "user_profile_table": "profiles",
        "chat_session_table": "sessions",
        "chat_session_user_index": "tenant-user-index",
        "chat_message_table": "messages",
        "usage_record_table": "usage",
        "document_api_enabled": True,
        "document_control_table": "documents",
        "document_bucket": "documents-bucket",
        "document_upload_lifecycle_rule_id": "temporary-uploads",
        "document_ingestion_queue_url": "https://sqs.example/queue",
        "vector_bucket_name": "vectors",
        "vector_index_name": "documents",
        "embedding_profile_alias": "default",
        "embedding_profile_revision": "profile-v1",
        "embedding_model_id": "model-v1",
        "document_pipeline_version": "pipeline-v1",
    }
    values.update(overrides)
    return BackendSettings(**values)


def test_document_worker_timing_defaults_are_coherent() -> None:
    settings = _settings()

    assert settings.document_ingestion_lease_ttl_seconds == 900
    assert settings.document_queue_visibility_timeout_seconds == 900
    assert settings.document_ingestion_heartbeat_interval_seconds == 60


@pytest.mark.parametrize(
    "overrides",
    (
        {
            "document_ingestion_lease_ttl_seconds": 901,
            "document_queue_visibility_timeout_seconds": 900,
        },
        {
            "document_ingestion_lease_ttl_seconds": 60,
            "document_ingestion_heartbeat_interval_seconds": 60,
        },
        {
            "document_queue_visibility_timeout_seconds": 60,
            "document_ingestion_heartbeat_interval_seconds": 60,
        },
    ),
)
def test_document_worker_rejects_incoherent_timing(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="document ingestion|visibility timeout"):
        _settings(**overrides)
