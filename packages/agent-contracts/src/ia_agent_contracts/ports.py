from typing import Protocol, runtime_checkable

from ia_agent_contracts.models import (
    AgentRequest,
    AgentResponse,
    SecurityContext,
    ToolCall,
    ToolResult,
    ToolSpec,
)


@runtime_checkable
class AgentClient(Protocol):
    async def invoke(self, request: AgentRequest) -> AgentResponse: ...


@runtime_checkable
class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    async def execute(self, call: ToolCall, context: SecurityContext) -> ToolResult: ...


@runtime_checkable
class ToolRegistry(Protocol):
    def get(self, name: str) -> Tool | None: ...

    def list_specs(self) -> tuple[ToolSpec, ...]: ...
