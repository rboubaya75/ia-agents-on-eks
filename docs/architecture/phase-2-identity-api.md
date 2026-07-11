# Phase 2 — Identity, API foundation, and DynamoDB repositories

## Implemented scope

- Cognito access-token verification with JWKS caching and signing-key rotation support.
- Trusted `Principal` derived from verified claims only.
- FastAPI application factory with normalized errors and correlation headers.
- Health, identity, and chat-session endpoints.
- DynamoDB adapters for user profiles, chat sessions, chat messages, and usage records.
- Local fakes and contract/integration tests without an AWS dependency.

## Runtime configuration

The application factory reads these environment variables through `pydantic-settings`:

- `IA_AWS_REGION` — optional; SDK resolution is used when omitted.
- `IA_COGNITO_ISSUER`.
- `IA_COGNITO_CLIENT_ID`.
- `IA_COGNITO_REQUIRED_SCOPES`.
- `IA_USER_PROFILE_TABLE`.
- `IA_CHAT_SESSION_TABLE`.
- `IA_CHAT_MESSAGE_TABLE`.
- `IA_USAGE_RECORD_TABLE`.

No default AWS account ID, ARN, region, table name, secret, or user-pool ID is embedded in the code.

## API authorization

| Endpoint | Required scope |
|---|---|
| `GET /api/v1/me` | `platform/profile.read` |
| session reads | `platform/chat.read` |
| session creation/deletion | `platform/chat.write` |

The scope names are application configuration and will later be aligned with the Cognito resource-server Terraform module.

## Deferred within later phases

- document APIs, ingestion, RAG, and S3 Vectors;
- agent invocation and streaming chat messages;
- Terraform and Helm resources;
- pagination cursor signing and idempotency storage;
- full OpenTelemetry exporters and structured production logging.
