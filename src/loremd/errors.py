"""Domain errors.

The core raises them; the MCP and CLI layers decide how to present them.
"""


class LoremdError(Exception):
    """Base error."""

    code = "error"


class ConfigError(LoremdError):
    code = "config_error"


class NotFound(LoremdError):
    code = "not_found"


class ValidationError(LoremdError):
    code = "validation_error"


class SecretDetected(ValidationError):
    code = "secret_detected"


class AuthError(LoremdError):
    code = "unauthorized"


class ForbiddenError(LoremdError):
    code = "forbidden"


class ConflictError(LoremdError):
    code = "conflict"


class GitError(LoremdError):
    code = "git_error"


class RateLimited(LoremdError):
    code = "rate_limited"
