"""领域实体"""

from .command import CommandEntry, MatchedHandlerInfo
from .identity import IdentityCandidate, IdentityResolution, normalize_identity_name
from .plugin import PluginCommandSummary

__all__ = [
    "CommandEntry",
    "MatchedHandlerInfo",
    "IdentityCandidate",
    "IdentityResolution",
    "normalize_identity_name",
    "PluginCommandSummary",
]
