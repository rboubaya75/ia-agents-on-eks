from typing import Annotated

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IA_",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    aws_region: str | None = Field(default=None, min_length=1, max_length=64)
    cognito_issuer: str = Field(min_length=1, max_length=512)
    cognito_client_id: str = Field(min_length=1, max_length=256)
    cognito_required_scopes: frozenset[str] = frozenset()
    user_profile_table: str = Field(min_length=1, max_length=255)
    chat_session_table: str = Field(min_length=1, max_length=255)
    chat_session_user_index: str = Field(min_length=1, max_length=255)
    chat_message_table: str = Field(min_length=1, max_length=255)
    usage_record_table: str = Field(min_length=1, max_length=255)

    document_api_enabled: bool = False
    document_control_table: str | None = Field(default=None, min_length=1, max_length=255)
    document_bucket: str | None = Field(default=None, min_length=1, max_length=63)
    document_source_prefix: str = Field(default="documents", min_length=1, max_length=256)
    document_index_prefix: str = Field(default="rag", min_length=1, max_length=256)
    document_kms_key_id: str | None = Field(default=None, min_length=1, max_length=2048)
    document_max_source_bytes: Annotated[int, Field(ge=1, le=50_000_000)] = 10_000_000
    document_upload_lifecycle_rule_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    document_ingestion_queue_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    document_queue_visibility_timeout_seconds: Annotated[
        int,
        Field(ge=30, le=43_200),
    ] = 900

    vector_bucket_name: str | None = Field(default=None, min_length=1, max_length=63)
    vector_index_name: str | None = Field(default=None, min_length=1, max_length=63)

    embedding_profile_alias: str | None = Field(default=None, min_length=1, max_length=128)
    embedding_profile_revision: str | None = Field(default=None, min_length=1, max_length=128)
    embedding_model_id: str | None = Field(default=None, min_length=1, max_length=300)
    embedding_dimensions: Annotated[int, Field(ge=1, le=4096)] = 1024
    document_pipeline_version: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_document_runtime(self) -> "BackendSettings":
        if not self.document_api_enabled:
            return self
        required = {
            "document_control_table": self.document_control_table,
            "document_bucket": self.document_bucket,
            "document_upload_lifecycle_rule_id": self.document_upload_lifecycle_rule_id,
            "document_ingestion_queue_url": self.document_ingestion_queue_url,
            "vector_bucket_name": self.vector_bucket_name,
            "vector_index_name": self.vector_index_name,
            "embedding_profile_alias": self.embedding_profile_alias,
            "embedding_profile_revision": self.embedding_profile_revision,
            "embedding_model_id": self.embedding_model_id,
            "document_pipeline_version": self.document_pipeline_version,
        }
        missing = sorted(name for name, value in required.items() if value is None)
        if missing:
            raise ValueError(
                "document API is enabled but required settings are missing: " + ", ".join(missing)
            )
        return self
