from dataclasses import dataclass

from fastapi import FastAPI
from ia_application import (
    DocumentIngestionService,
    DocumentIngestionWorker,
    DocumentManagement,
    DocumentManagementService,
    DocumentPipelineSettings,
    ParagraphChunker,
    Utf8DocumentExtractor,
)
from ia_aws_clients import (
    BedrockTitanEmbeddingProvider,
    Boto3DynamoControlTable,
    Boto3DynamoTable,
    DynamoChatSessionRepository,
    DynamoControlReadinessProbe,
    DynamoDocumentIngestionLeaseRepository,
    DynamoDocumentRepository,
    DynamoIndexActivationRepository,
    DynamoIndexGenerationRepository,
    DynamoIngestionJobRepository,
    EmbeddingProfileReadinessProbe,
    S3ChunkStore,
    S3DocumentSourceStore,
    S3JsonStore,
    S3VectorIndexReadinessProbe,
    S3VectorIndexSettings,
    S3VectorKeyManifestStore,
    S3VectorRepository,
    SqsIngestionTaskQueue,
    TitanEmbeddingProfileSettings,
)
from ia_security import (
    CachedJwkProvider,
    CognitoTokenVerifier,
    CognitoTokenVerifierSettings,
    HttpJwkSetFetcher,
)

from ia_backend_api.app import create_app
from ia_backend_api.container import (
    AppContainer,
    CompositeReadinessProbe,
    ReadinessProbe,
)
from ia_backend_api.settings import BackendSettings


class DynamoReadinessProbe(ReadinessProbe):
    def __init__(self, table: Boto3DynamoTable) -> None:
        self._table = table

    async def is_ready(self) -> bool:
        return await self._table.ping()


@dataclass(frozen=True, slots=True)
class DocumentRuntime:
    management: DocumentManagement
    worker: DocumentIngestionWorker
    readiness: ReadinessProbe


def _required(value: str | None, name: str) -> str:
    if value is None:
        raise ValueError(f"required document setting is missing: {name}")
    return value


def create_document_runtime(settings: BackendSettings) -> DocumentRuntime | None:
    if not settings.document_api_enabled:
        return None

    control_table = Boto3DynamoControlTable.from_table_name(
        _required(settings.document_control_table, "document_control_table"),
        region_name=settings.aws_region,
    )
    documents = DynamoDocumentRepository(control_table)
    jobs = DynamoIngestionJobRepository(control_table)
    leases = DynamoDocumentIngestionLeaseRepository(control_table)
    generations = DynamoIndexGenerationRepository(control_table)
    activations = DynamoIndexActivationRepository(control_table)

    sources = S3DocumentSourceStore.from_bucket_name(
        _required(settings.document_bucket, "document_bucket"),
        prefix=settings.document_source_prefix,
        kms_key_id=settings.document_kms_key_id,
        max_source_bytes=settings.document_max_source_bytes,
        temporary_upload_lifecycle_rule_id=_required(
            settings.document_upload_lifecycle_rule_id,
            "document_upload_lifecycle_rule_id",
        ),
        region_name=settings.aws_region,
    )
    index_store = S3JsonStore.from_bucket_name(
        _required(settings.document_bucket, "document_bucket"),
        prefix=settings.document_index_prefix,
        kms_key_id=settings.document_kms_key_id,
        region_name=settings.aws_region,
    )
    chunks = S3ChunkStore(index_store)
    vector_manifests = S3VectorKeyManifestStore(index_store)
    vector_settings = S3VectorIndexSettings(
        vector_bucket_name=_required(settings.vector_bucket_name, "vector_bucket_name"),
        index_name=_required(settings.vector_index_name, "vector_index_name"),
    )
    vectors = S3VectorRepository.from_settings(
        vector_settings,
        manifests=vector_manifests,
        region_name=settings.aws_region,
    )
    embedding_alias = _required(
        settings.embedding_profile_alias,
        "embedding_profile_alias",
    )
    embeddings = BedrockTitanEmbeddingProvider.from_profiles(
        (
            TitanEmbeddingProfileSettings(
                alias=embedding_alias,
                revision=_required(
                    settings.embedding_profile_revision,
                    "embedding_profile_revision",
                ),
                model_id=_required(settings.embedding_model_id, "embedding_model_id"),
                dimensions=settings.embedding_dimensions,
            ),
        ),
        region_name=settings.aws_region,
    )
    queue = SqsIngestionTaskQueue.from_queue_url(
        _required(
            settings.document_ingestion_queue_url,
            "document_ingestion_queue_url",
        ),
        visibility_timeout_seconds=settings.document_queue_visibility_timeout_seconds,
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
    management = DocumentManagementService(
        documents=documents,
        jobs=jobs,
        leases=leases,
        sources=sources,
        queue=queue,
        chunks=chunks,
        vectors=vectors,
        pipeline=DocumentPipelineSettings(
            embedding_model_alias=embedding_alias,
            pipeline_version=_required(
                settings.document_pipeline_version,
                "document_pipeline_version",
            ),
        ),
        max_source_bytes=settings.document_max_source_bytes,
    )
    worker = DocumentIngestionWorker(
        jobs=jobs,
        queue=queue,
        ingestion=ingestion,
    )
    readiness = CompositeReadinessProbe(
        (
            DynamoControlReadinessProbe(control_table),
            sources,
            queue,
            S3VectorIndexReadinessProbe.from_settings(
                vector_settings,
                region_name=settings.aws_region,
            ),
            EmbeddingProfileReadinessProbe(
                embeddings,
                model_alias=embedding_alias,
            ),
        )
    )
    return DocumentRuntime(
        management=management,
        worker=worker,
        readiness=readiness,
    )


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
    document_runtime = create_document_runtime(settings)
    probes: list[ReadinessProbe] = [DynamoReadinessProbe(session_table)]
    if document_runtime is not None:
        probes.append(document_runtime.readiness)

    return create_app(
        AppContainer(
            token_verifier=token_verifier,
            chat_sessions=DynamoChatSessionRepository(
                session_table,
                user_index_name=settings.chat_session_user_index,
            ),
            readiness=CompositeReadinessProbe(probes),
            documents=(None if document_runtime is None else document_runtime.management),
        )
    )
