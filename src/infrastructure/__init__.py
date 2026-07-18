"""基础设施层

基础设施层包含技术实现细节，如分析器、渲染、缓存等。
"""

from .analysis import (
    CommandExecutor,
    CommandIndex,
    get_command_executor,
    get_command_index,
    invalidate_command_cache,
    reset_command_executor,
    reset_command_index,
)
from .config import (
    CustomGroupCommand,
    CustomGroupConfig,
    HelpPluginConfig,
    RegexConfig,
    clear_config,
    get_config,
    init_config,
    refresh_config,
)
from .context_holder import clear_context, get_context, set_context
from .utils import (
    get_cache_dir,
    get_custom_groups_path,
    get_data_dir,
    get_logger,
    get_plugin_data_dir,
    get_plugin_dir,
    init_plugin_paths,
    logger,
    looks_like_regex,
    normalize_detail_query,
    replace_prefix,
)

__all__ = [
    # Config
    "HelpPluginConfig",
    "RegexConfig",
    "CustomGroupCommand",
    "CustomGroupConfig",
    "get_config",
    "init_config",
    "refresh_config",
    "clear_config",
    # Context
    "set_context",
    "get_context",
    "clear_context",
    # Utils
    "get_logger",
    "logger",
    "init_plugin_paths",
    "get_plugin_dir",
    "get_data_dir",
    "get_plugin_data_dir",
    "get_cache_dir",
    "get_custom_groups_path",
    "replace_prefix",
    "looks_like_regex",
    "normalize_detail_query",
    # Analysis
    "CommandIndex",
    "get_command_index",
    "reset_command_index",
    "invalidate_command_cache",
    "CommandExecutor",
    "get_command_executor",
    "reset_command_executor",
]
