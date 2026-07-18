"""领域层

领域层包含业务实体、值对象和领域逻辑。
"""

from .entities.command import CommandEntry, MatchedHandlerInfo
from .entities.plugin import PluginCommandSummary
from .exceptions import (
    CommandNotFoundError,
    ConfigNotInitializedError,
    ContextNotInitializedError,
    HelpPluginError,
)

__all__ = [
    # Entities
    "CommandEntry",
    "MatchedHandlerInfo",
    "PluginCommandSummary",
    # Exceptions
    "HelpPluginError",
    "ConfigNotInitializedError",
    "ContextNotInitializedError",
    "CommandNotFoundError",
]
