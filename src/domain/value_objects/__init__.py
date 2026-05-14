"""Domain Value Objects

Configuration and constants have been moved to:
- infrastructure.config: HelpPluginConfig and related
- shared.constants: DefaultCFG, InternalCFG, UserRole
"""

# Re-export from new locations for backward compatibility
from ...infrastructure.config import (
    CustomGroupCommand,
    CustomGroupConfig,
    HelpPluginConfig,
    RegexConfig,
    RenderingConfig,
    clear_config,
    get_config,
    init_config,
    refresh_config,
)
from ...shared.constants import DefaultCFG, InternalCFG, UserRole

__all__ = [
    "HelpPluginConfig",
    "RenderingConfig",
    "RegexConfig",
    "CustomGroupCommand",
    "CustomGroupConfig",
    "get_config",
    "init_config",
    "refresh_config",
    "clear_config",
    "UserRole",
    "DefaultCFG",
    "InternalCFG",
]
