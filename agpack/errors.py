"""Exception types raised across the agpack pipeline.

Centralised here so every module can ``from agpack.errors import X`` without dragging in the modules that *raise* the
exception. Keeps every other module a clean leaf with respect to the error surface.
"""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when an agpack config (``agpack.yml``, ``.env``, or a ``${VAR}`` reference) is invalid."""


class EditFileError(Exception):
    """Raised when an edit-file deployment or cleanup fails."""


class DeployError(Exception):
    """Raised when a copy-kind deployment fails."""


class FetchError(Exception):
    """Raised when a git fetch operation fails."""


class TargetSchemaError(Exception):
    """Raised when a target manifest fails to parse or validate."""
