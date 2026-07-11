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
| ChatSession | `tenantId` | `sessionId`; configurable GSI `tenantUserKey` + `lastActivity` |
| ChatMessage | `tenantSessionKey` | `createdAtMessageKey` (`createdAtUtc#messageId`) |
| UsageRecord | `tenantUserKey` | `timestampRequestKey` (`timestampUtc#requestId`) |

Composite partition keys use a length-prefixed encoding for every component. They are never constructed by joining unvalidated identifiers with a delimiter. Repository results are revalidated against the requested tenant and principal identifiers before being returned.

All repository reads and deletes include the trusted tenant in the physical key. Datetimes are stored as timezone-aware ISO-8601 strings, monetary values remain `Decimal`, and floats are converted to `Decimal` before persistence. Integer decoders accept only integral `int` or `Decimal` values.

The current list contracts return complete tuples, so the boto3 adapter follows `LastEvaluatedKey` until every DynamoDB query page has been read. A bounded cursor-based API can replace this contract before high-volume production usage.

`ChatSessionCommandRepository` extends the read/write session repository with deletion. This keeps the original Phase 1 read contract stable while making destructive capability explicit.

## Consequences

Terraform must create matching keys and indexes in the infrastructure phase. Table and index names are mandatory configuration, never hardcoded AWS identifiers. Repository contract tests remain AWS-independent; the boto3 facade is tested with a synchronous fake table resource that reproduces pagination and `Decimal` deserialization.

The chronological message sort key is part of the physical schema and must be preserved during migrations. Pagination cursors, request limits and idempotency keys must be finalized before exposing high-volume endpoints.
