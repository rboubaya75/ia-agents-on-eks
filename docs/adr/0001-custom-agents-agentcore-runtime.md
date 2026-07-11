# ADR-0001: Custom agents with AgentCore Runtime as execution infrastructure

- Status: Accepted
- Date: 2026-07-11

## Decision

All agent logic, prompts, policies, tools and tests remain in this repository under `/agents`. Amazon Bedrock AgentCore Runtime is used only to host and execute those custom agents. The backend depends on the `AgentClient` port rather than AgentCore APIs.

## Consequences

- Bedrock managed Agents are prohibited.
- Agents remain locally testable with fake providers.
- AgentCore-specific SDK usage is confined to an adapter package.
