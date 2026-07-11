# ADR-0003: Hexagonal monorepo with typed ports and adapters

- Status: Accepted
- Date: 2026-07-11

## Decision

Domain and application packages expose strict typed models and protocols. AWS SDKs, FastAPI, AgentCore and persistence clients are adapters at the edge of the system.

## Consequences

- Domain code cannot import boto3.
- AWS implementations can be replaced by in-memory fakes in tests.
- Cross-package dependencies flow inward toward domain contracts.
