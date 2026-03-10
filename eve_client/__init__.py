"""Eve client installer package."""

from ._version import __version__
from .auth import CredentialRecord, CredentialStore, LocalCredentialStore
from .cli import app, main
from .config import ResolvedConfig
from .integrations.provider import ToolProvider
from .models import ApplyResult, InstallPlan, PlannedAction, RollbackResult, ToolPlan

__all__ = [
    "ApplyResult",
    "CredentialRecord",
    "CredentialStore",
    "InstallPlan",
    "LocalCredentialStore",
    "PlannedAction",
    "ResolvedConfig",
    "RollbackResult",
    "ToolPlan",
    "ToolProvider",
    "__version__",
    "app",
    "main",
]
