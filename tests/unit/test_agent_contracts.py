from decimal import Decimal

import pytest
from ia_agent_contracts import (
    AgentConstraints,
    AgentRequest,
    AgentResponse,
    AgentUsage,
    ProposedAction,
    SecurityContext,
)
from ia_domain import Classification, RequestId, Role, SessionId, TenantId, UserId
from pydantic import ValidationError


def _security_context() -> SecurityContext:
    return SecurityContext(
        tenant_id=TenantId("tenant-a"),
        user_id=UserId("user-a"),
        roles=frozenset({Role.USER}),
        scopes=frozenset({"chat:write"}),
        maximum_classification=Classification.INTERNAL,
    )


def test_agent_constraints_reject_excessive_iterations() -> None:
    with pytest.raises(ValidationError):
        AgentConstraints(max_iterations=21)


def test_agent_request_requires_trusted_security_context() -> None:
    request = AgentRequest(
        request_id=RequestId("request-1"),
        session_id=SessionId("session-1"),
        input_text="Find the applicable policy",
        security_context=_security_context(),
    )
    assert request.security_context.tenant_id == TenantId("tenant-a")


def test_destructive_action_requires_confirmation_token() -> None:
    with pytest.raises(ValidationError):
        ProposedAction(action_type="delete-document", parameters={}, destructive=True)


def test_agent_response_is_structured() -> None:
    response = AgentResponse(
        content="Supported answer",
        usage=AgentUsage(
            model_id="fake-model",
            input_tokens=10,
            output_tokens=5,
            latency_ms=20,
            estimated_cost_usd=Decimal("0.001"),
        ),
        specialist_agent="knowledge",
    )
    assert response.specialist_agent == "knowledge"
