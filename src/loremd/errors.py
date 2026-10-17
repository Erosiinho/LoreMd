"""Erreurs du domaine.

Le core lève ces erreurs ; les couches MCP et CLI décident comment les montrer.
"""


class LoremdError(Exception):
    """Erreur de base."""

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
