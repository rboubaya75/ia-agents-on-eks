# ADR-0005: Cognito access token as the trusted request identity

- Status: Accepted
- Date: 2026-07-11

## Context

The API is multi-tenant and must never trust a tenant identifier supplied in a request body, query parameter, or application-defined header. Cognito access tokens contain the trusted subject, app client, groups, scopes, issuer, token purpose, and expiration. The platform also requires an email address and tenant identifier in the authenticated context.

## Decision

The backend accepts only Cognito **access tokens** for API authorization. The verifier:

- permits only `RS256`;
- verifies the signature against the user-pool JWKS;
- validates `iss`, `client_id`, `exp`, `iat`, `jti`, and `token_use=access`;
- caches keys by `kid` and refreshes on expiration or an unknown `kid`;
- derives `userId` from `sub` without enforcing UUID syntax;
- derives `tenantId` only from the configured `custom:tenant_id` claim;
- requires the configured email claim;
- maps only recognized Cognito groups to application roles;
- validates OAuth scopes before protected operations.

A token without a tenant, email, recognized role, valid signature, trusted issuer, or trusted client ID is rejected. Request payloads contain no trusted `tenantId` field.

## Consequences

The Cognito configuration must ensure that access tokens contain `custom:tenant_id`, `email`, and at least one recognized group. This can require a pre-token-generation customization. The application remains fail-closed when claims are absent or malformed.

The current role-to-classification ceiling is:

- `user` -> `internal`;
- `support` and `tenant-admin` -> `confidential`;
- `platform-admin` -> `restricted`.

Document-level policy and retrieval filters remain authoritative and can further reduce access.
