from fastapi import FastAPI
from ia_aws_clients import Boto3DynamoTable, DynamoChatSessionRepository
from ia_security import (
    CachedJwkProvider,
    CognitoTokenVerifier,
    CognitoTokenVerifierSettings,
    HttpJwkSetFetcher,
)

from ia_backend_api.app import create_app
from ia_backend_api.container import AppContainer, ReadinessProbe
from ia_backend_api.settings import BackendSettings


class DynamoReadinessProbe(ReadinessProbe):
    def __init__(self, table: Boto3DynamoTable) -> None:
        self._table = table

    async def is_ready(self) -> bool:
        return await self._table.ping()


def create_application() -> FastAPI:
    settings = BackendSettings()
    token_settings = CognitoTokenVerifierSettings(
        issuer=settings.cognito_issuer,
        client_id=settings.cognito_client_id,
        required_scopes=settings.cognito_required_scopes,
    )
    token_verifier = CognitoTokenVerifier(
        settings=token_settings,
        jwk_provider=CachedJwkProvider(
            jwks_uri=token_settings.jwks_uri,
            fetcher=HttpJwkSetFetcher(),
        ),
    )
    session_table = Boto3DynamoTable.from_table_name(
        settings.chat_session_table,
        region_name=settings.aws_region,
    )
    return create_app(
        AppContainer(
            token_verifier=token_verifier,
            chat_sessions=DynamoChatSessionRepository(
                session_table,
                user_index_name=settings.chat_session_user_index,
            ),
            readiness=DynamoReadinessProbe(session_table),
        )
    )
