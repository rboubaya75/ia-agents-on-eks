from decimal import Decimal

import pytest
from ia_agent_contracts import AgentResponse, AgentUsage
from ia_application import (
    EmbeddingRequest,
    EmbeddingResponse,
    ModelRequest,
    ModelResponse,
)
from test_support import (
    FakeAgentRuntimeClient,
    FakeEmbeddingProvider,
    FakeModelProvider,
    FakeSecretsProvider,
)


async def test_fake_model_provider_records_requests() -> None:
    response = ModelResponse(
        model_id="fake-model",
        content="response",
        input_tokens=2,
        output_tokens=1,
        latency_ms=3,
        estimated_cost_usd=Decimal("0"),
    )
    provider = FakeModelProvider([response])
    request = ModelRequest(
        model_alias="economy",
        system_prompt="system",
        messages=({"role": "user", "content": "hello"},),
        max_output_tokens=100,
    )
    assert await provider.converse(request) == response
    assert provider.requests == [request]


async def test_fake_embedding_provider_records_requests() -> None:
    response = EmbeddingResponse(
        model_id="fake-embedding",
        dimensions=2,
        vectors=((1.0, 0.0),),
    )
    provider = FakeEmbeddingProvider([response])
    request = EmbeddingRequest(model_alias="default", texts=("hello",))
    assert await provider.embed(request) == response
    assert provider.requests == [request]


async def test_fake_providers_fail_when_not_configured() -> None:
    with pytest.raises(RuntimeError):
        await FakeModelProvider([]).converse(
            ModelRequest(
                model_alias="economy",
                system_prompt="system",
                messages=({"role": "user", "content": "hello"},),
                max_output_tokens=100,
            )
        )

    with pytest.raises(RuntimeError):
        await FakeEmbeddingProvider([]).embed(
            EmbeddingRequest(model_alias="default", texts=("hello",))
        )


async def test_fake_agent_runtime_and_secrets() -> None:
    response = AgentResponse(
        content="ok",
        usage=AgentUsage(
            model_id="fake-model",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            estimated_cost_usd=Decimal("0"),
        ),
        specialist_agent="knowledge",
    )
    runtime = FakeAgentRuntimeClient([response])
    assert runtime.requests == []

    secrets = FakeSecretsProvider({"confirmation-key": "secret"})
    assert await secrets.get_secret("confirmation-key") == "secret"
    with pytest.raises(KeyError):
        await secrets.get_secret("missing")
