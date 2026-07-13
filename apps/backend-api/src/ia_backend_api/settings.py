from typing import Annotated

from pydantic import Field
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

    document_control_table: str = Field(min_length=1, max_length=255)
    document_bucket: str = Field(min_length=1, max_length=63)
    document_source_prefix: str = Field(default="documents", min_length=1, max_length=256)
    document_index_prefix: str = Field(default="rag", min_length=1, max_length=256)
    document_kms_key_id: str | None = Field(default=None, min_length=1, max_length=2048)
    document_max_source_bytes: Annotated[int, Field(ge=1, le=50_000_000)] = 10_000_000

    vector_bucket_name: str = Field(min_length=1, max_length=63)
    vector_index_name: str = Field(min_length=1, max_length=63)

    embedding_profile_alias: str = Field(default="default", min_length=1, max_length=128)
    embedding_profile_revision: str = Field(min_length=1, max_length=128)
    embedding_model_id: str = Field(min_length=1, max_length=300)
    embedding_dimensions: Annotated[int, Field(ge=1, le=4096)] = 1024
