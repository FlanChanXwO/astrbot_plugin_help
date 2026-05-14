"""领域实体"""

from .command import CommandEntry, MatchedHandlerInfo
from .plugin import PluginCommandSummary, RenderNode

__all__ = [
    "CommandEntry",
    "MatchedHandlerInfo",
    "PluginCommandSummary",
    "RenderNode",
]
