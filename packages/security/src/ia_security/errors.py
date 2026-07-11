class AuthenticationError(Exception):
    """Base class for expected authentication failures."""


class InvalidTokenError(AuthenticationError):
    """Raised when a token is malformed, untrusted, or has invalid claims."""


class ExpiredTokenError(AuthenticationError):
    """Raised when an otherwise valid token has expired."""


class InsufficientScopeError(AuthenticationError):
    """Raised when a verified token lacks a required OAuth scope."""
