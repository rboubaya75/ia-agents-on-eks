# ADR-0006: DynamoDB repositories and physical access patterns

- Status: Accepted
- Date: 2026-07-11

## Context

The application model defines logical repositories for profiles, sessions, messages, usage, and ingestion. Domain and application code must not depend on boto3. Every access path must carry the trusted tenant identifier.

## Decision

DynamoDB is accessed through typed repository ports. The boto3 integration is isolated in `packages/aws-clients` behind an asynchronous `DynamoTable` interface. Blocking boto3 table operations are moved to worker threads at the adapter boundary.

The initial physical access patterns are:

| Logical table | Partition key | Sort key / secondary access |
|---|---|---|
| UserProfile | `tenantId` | `userId` |
| ChatSession | `tenantId` | `sessionId`; GSI `tenantUserKey` + `lastActivity` |
| ChatMessage | `tenantSessionKey` | `messageId` |
| UsageRecord | `tenantUserKey` | `timestampRequestKey` |

All repository reads and deletes include the trusted tenant in the physical key. Datetimes are stored as timezone-aware ISO-8601 strings, monetary values remain `Decimal`, and floats are converted to `Decimal` before persistence.

`ChatSessionCommandRepository` extends the read/write session repository with deletion. This keeps the original Phase 1 read contract stable while making destructive capability explicit.

## Consequences

Terraform must create matching keys and indexes in the infrastructure phase. Table and index names are configuration, never hardcoded AWS identifiers. Repository contract tests remain AWS-independent; the boto3 facade is tested with a synchronous fake table resource.

Pagination and idempotency keys for high-volume list/write operations must be finalized before exposing the corresponding high-volume endpoints.
