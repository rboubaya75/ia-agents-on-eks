from decimal import Decimal
from typing import Annotated, Any

from ia_domain import Citation, Classification, RequestId, Role, SessionId, TenantId, UserId
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SecurityContext(StrictModel):
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    user_id: Annotated[UserId, Field(min_length=1, max_length=128)]
    roles: Annotated[frozenset[Role], Field(min_length=1)]
    scopes: frozenset[str] = Field(default_factory=frozenset)
    maximum_classification: Classification


class AgentConstraints(StrictModel):
    max_iterations: Annotated[int, Field(ge=1, le=20)] = 4
    max_tool_calls: Annotated[int, Field(ge=0, le=20)] = 5
    timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 30.0
    max_input_tokens: Annotated[int, Field(ge=1, le=200_000)] = 16_000
    max_output_tokens: Annotated[int, Field(ge=1, le=32_000)] = 2_000
    top_k: Annotated[int, Field(ge=1, le=100)] = 10


class AgentRequest(StrictModel):
    request_id: Annotated[RequestId, Field(min_length=1, max_length=128)]
    session_id: Annotated[SessionId, Field(min_length=1, max_length=128)]
    input_text: Annotated[str, Field(min_length=1, max_length=100_000)]
    security_context: SecurityContext
    constraints: AgentConstraints = Field(default_factory=AgentConstraints)
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentUsage(StrictModel):
    model_id: Annotated[str, Field(min_length=1, max_length=300)]
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[int, Field(ge=0)]
    estimated_cost_usd: Annotated[Decimal, Field(ge=0)]
    iterations: Annotated[int, Field(ge=0)] = 0
    tool_calls: Annotated[int, Field(ge=0)] = 0


class ProposedAction(StrictModel):
    action_type: Annotated[str, Field(min_length=1, max_length=128)]
    parameters: dict[str, Any]
    destructive: bool = False
    confirmation_token: Annotated[str, Field(min_length=32, max_length=4096)] | None = None

    @model_validator(mode="after")
    def require_confirmation_for_destructive_action(self) -> "ProposedAction":
        if self.destructive and self.confirmation_token is None:
            msg = "destructive actions require a confirmation token"
            raise ValueError(msg)
        return self


class AgentResponse(StrictModel):
    content: Annotated[str, Field(min_length=1, max_length=200_000)]
    citations: tuple[Citation, ...] = ()
    usage: AgentUsage
    proposed_actions: tuple[ProposedAction, ...] = ()
    specialist_agent: Annotated[str, Field(min_length=1, max_length=128)]


class ToolSpec(StrictModel):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")]
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    destructive: bool = False
    required_scopes: frozenset[str] = Field(default_factory=frozenset)


class ToolCall(StrictModel):
    call_id: Annotated[str, Field(min_length=1, max_length=128)]
    tool_name: Annotated[str, Field(min_length=1, max_length=64)]
    arguments: dict[str, Any]


class ToolResult(StrictModel):
    call_id: Annotated[str, Field(min_length=1, max_length=128)]
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error_code: Annotated[str, Field(min_length=1, max_length=128)] | None = None
