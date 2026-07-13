from fastapi import FastAPI
from ia_application import (
    DocumentIngestionService,
    DocumentManagementService,
    ParagraphChunker,
    Utf8DocumentExtractor,
)
from ia_aws_clients import (
    BedrockTitanEmbeddingProvider,
    Boto3DynamoControlTable,
    Boto3DynamoTable,
    DynamoChatSessionRepository,
    DynamoDocumentIngestionLeaseRepository,
    DynamoDocumentRepository,
    DynamoIndexActivationRepository,
    DynamoIndexGenerationRepository,
    DynamoIngestionJobRepository,
    S3ChunkStore,
    S3DocumentSourceStore,
    S3JsonStore,
    S3VectorIndexSettings,
    S3VectorKeyManifestStore,
    S3VectorRepository,
    TitanEmbeddingProfileSettings,
)
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

    control_table = Boto3DynamoControlTable.from_table_name(
        settings.document_control_table,
        region_name=settings.aws_region,
    )
    documents = DynamoDocumentRepository(control_table)
    jobs = DynamoIngestionJobRepository(control_table)
    leases = DynamoDocumentIngestionLeaseRepository(control_table)
    generations = DynamoIndexGenerationRepository(control_table)
    activations = DynamoIndexActivationRepository(control_table)

    sources = S3DocumentSourceStore.from_bucket_name(
        settings.document_bucket,
        prefix=settings.document_source_prefix,
        kms_key_id=settings.document_kms_key_id,
        max_source_bytes=settings.document_max_source_bytes,
        region_name=settings.aws_region,
    )
    index_store = S3JsonStore.from_bucket_name(
        settings.document_bucket,
        prefix=settings.document_index_prefix,
        kms_key_id=settings.document_kms_key_id,
        region_name=settings.aws_region,
    )
    chunks = S3ChunkStore(index_store)
    vector_manifests = S3VectorKeyManifestStore(index_store)
    vectors = S3VectorRepository.from_settings(
        S3VectorIndexSettings(
            vector_bucket_name=settings.vector_bucket_name,
            index_name=settings.vector_index_name,
        ),
        manifests=vector_manifests,
        region_name=settings.aws_region,
    )
    embeddings = BedrockTitanEmbeddingProvider.from_profiles(
        (
            TitanEmbeddingProfileSettings(
                alias=settings.embedding_profile_alias,
                revision=settings.embedding_profile_revision,
                model_id=settings.embedding_model_id,
                dimensions=settings.embedding_dimensions,
            ),
        ),
        region_name=settings.aws_region,
    )
    ingestion = DocumentIngestionService(
        documents=documents,
        jobs=jobs,
        leases=leases,
        generations=generations,
        activations=activations,
        extractor=Utf8DocumentExtractor(
            sources,
            max_bytes=settings.document_max_source_bytes,
        ),
        chunker=ParagraphChunker(),
        embeddings=embeddings,
        chunks=chunks,
        vectors=vectors,
    )
    document_management = DocumentManagementService(
        documents=documents,
        jobs=jobs,
        sources=sources,
        ingestion=ingestion,
        chunks=chunks,
        vectors=vectors,
        max_source_bytes=settings.document_max_source_bytes,
    )

    return create_app(
        AppContainer(
            token_verifier=token_verifier,
            chat_sessions=DynamoChatSessionRepository(
                session_table,
                user_index_name=settings.chat_session_user_index,
            ),
            readiness=DynamoReadinessProbe(session_table),
            documents=document_management,
        )
    )
