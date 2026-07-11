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
    chat_message_table: str = Field(min_length=1, max_length=255)
    usage_record_table: str = Field(min_length=1, max_length=255)
