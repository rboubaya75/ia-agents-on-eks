from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from ia_aws_clients.dynamodb_control import (
    DynamoControlTable,
    PythonItem,
    TransactionAction,
    transaction_payload_token,
)
from ia_aws_clients.dynamodb_documents import (
    DynamoIndexActivationRepository,
    DynamoIngestionJobRepository,
)
from ia_domain import (
    DocumentId,
    IndexGeneration,
    IndexGenerationStatus,
    IngestionJob,
    IngestionStatus,
    JobId,
    TenantId,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class RecordingTransactionTable:
    def __init__(self) -> None:
        self.tokens: list[str | None] = []

    @property
    def table_name(self) -> str:
        return "control"

    async def get_item(self, key: Mapping[str, object]) -> PythonItem | None:
        del key
        return None

    async def put_item(
        self,
        item: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        del (
            item,
            condition_expression,
            expression_attribute_names,
            expression_attribute_values,
        )

    async def update_item(
        self,
        key: Mapping[str, object],
        *,
        update_expression: str,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> PythonItem:
        del (
            key,
            update_expression,
            condition_expression,
            expression_attribute_names,
            expression_attribute_values,
        )
        return {}

    async def delete_item(
        self,
        key: Mapping[str, object],
        *,
        condition_expression: str | None = None,
        expression_attribute_names: Mapping[str, str] | None = None,
        expression_attribute_values: Mapping[str, object] | None = None,
    ) -> None:
        del (
            key,
            condition_expression,
            expression_attribute_names,
            expression_attribute_values,
        )

    async def transact_write(
        self,
        actions: Sequence[TransactionAction],
        *,
        client_request_token: str | None = None,
    ) -> None:
        del actions
        self.tokens.append(client_request_token)


def _running_job(started_at: datetime) -> IngestionJob:
    return IngestionJob(
        tenant_id=TenantId("tenant-a"),
        job_id=JobId("job-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        status=IngestionStatus.RUNNING,
        fingerprint="a" * 64,
        generation_id="generation-a",
        source_checksum="b" * 64,
        authorization_checksum="c" * 64,
        embedding_model_alias="default",
        embedding_profile_revision="profile-v1",
        resolved_embedding_model_id="model-v1",
        embedding_dimensions=256,
        chunking_version="paragraph-v1",
        pipeline_version="pipeline-v1",
        fencing_token=1,
        started_at=started_at,
    )


def _succeeded_job(completed_at: datetime) -> IngestionJob:
    return _running_job(NOW).model_copy(
        update={
            "status": IngestionStatus.SUCCEEDED,
            "chunks_created": 2,
            "vectors_created": 2,
            "completed_at": completed_at,
        }
    )


def _generation() -> IndexGeneration:
    return IndexGeneration(
        tenant_id=TenantId("tenant-a"),
        document_id=DocumentId("document-a"),
        source_version="v1",
        generation_id="generation-a",
        fingerprint="a" * 64,
        authorization_checksum="c" * 64,
        embedding_profile_revision="profile-v1",
        embedding_model_id="model-v1",
        embedding_dimensions=256,
        status=IndexGenerationStatus.READY,
        fencing_token=1,
        chunk_count=2,
        vector_count=2,
        created_at=NOW,
        ready_at=NOW,
    )


@pytest.mark.asyncio
async def test_claim_retry_reuses_token_only_for_identical_payload() -> None:
    first_table = RecordingTransactionTable()
    identical_table = RecordingTransactionTable()
    changed_table = RecordingTransactionTable()

    await DynamoIngestionJobRepository(cast(DynamoControlTable, first_table)).claim(
        _running_job(NOW)
    )
    await DynamoIngestionJobRepository(cast(DynamoControlTable, identical_table)).claim(
        _running_job(NOW)
    )
    await DynamoIngestionJobRepository(cast(DynamoControlTable, changed_table)).claim(
        _running_job(NOW + timedelta(seconds=1))
    )

    first_token = first_table.tokens[0]
    identical_token = identical_table.tokens[0]
    changed_token = changed_table.tokens[0]
    assert first_token is not None
    assert first_token == identical_token
    assert first_token != changed_token


def test_activation_token_changes_when_transaction_timestamp_changes() -> None:
    generation = _generation()
    succeeded_job = _succeeded_job(NOW)
    first_actions = DynamoIndexActivationRepository._activation_actions(
        generation=generation,
        succeeded_job=succeeded_job,
        expected_document_revision=0,
        activated_at=NOW,
    )
    changed_actions = DynamoIndexActivationRepository._activation_actions(
        generation=generation,
        succeeded_job=succeeded_job,
        expected_document_revision=0,
        activated_at=NOW + timedelta(seconds=1),
    )

    first_token = transaction_payload_token(
        namespace="activate-index-generation",
        table_name="control",
        actions=first_actions,
    )
    changed_token = transaction_payload_token(
        namespace="activate-index-generation",
        table_name="control",
        actions=changed_actions,
    )

    assert first_token != changed_token
