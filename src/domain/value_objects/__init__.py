"""Domain Value Objects

Configuration and constants have been moved to:
- infrastructure.config: HelpPluginConfig and related
- shared.constants: DefaultCFG, UserRole
"""

# Re-export from new locations for backward compatibility
from ...infrastructure.config import (
    CustomGroupCommand,
    CustomGroupConfig,
    HelpPluginConfig,
    RegexConfig,
    clear_config,
    get_config,
    init_config,
    refresh_config,
)
from ...shared.constants import DefaultCFG, UserRole

__all__ = [
    "HelpPluginConfig",
    "RegexConfig",
    "CustomGroupCommand",
    "CustomGroupConfig",
    "get_config",
    "init_config",
    "refresh_config",
    "clear_config",
    "UserRole",
    "DefaultCFG",
]
