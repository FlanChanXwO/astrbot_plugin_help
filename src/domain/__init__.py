"""领域层

领域层包含业务实体、值对象和领域逻辑。
"""

from .entities.command import CommandEntry, MatchedHandlerInfo
from .entities.plugin import PluginCommandSummary, RenderNode
from .exceptions import (
    CommandNotFoundError,
    ConfigNotInitializedError,
    ContextNotInitializedError,
    HelpPluginError,
    RenderError,
)

__all__ = [
    # Entities
    "CommandEntry",
    "MatchedHandlerInfo",
    "PluginCommandSummary",
    "RenderNode",
    # Exceptions
    "HelpPluginError",
    "ConfigNotInitializedError",
    "ContextNotInitializedError",
    "RenderError",
    "CommandNotFoundError",
]
