class DomainError(Exception):
    """Base exception for domain-level failures."""


class TenantIsolationError(DomainError):
    """Raised when a cross-tenant operation is attempted."""
