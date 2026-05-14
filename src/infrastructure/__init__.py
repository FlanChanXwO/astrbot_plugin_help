"""基础设施层

基础设施层包含技术实现细节，如分析器、渲染、缓存等。
"""

from .analysis import (
    CommandAnalyzer,
    CommandExecutor,
    CommandIndex,
    EventAnalyzer,
    FilterAnalyzer,
    get_command_analyzer,
    get_command_executor,
    get_command_index,
    get_event_analyzer,
    get_filter_analyzer,
    invalidate_command_cache,
    reset_analyzers,
    reset_command_executor,
    reset_command_index,
)
from .config import (
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
from .context_holder import clear_context, get_context, set_context
from .persistence import CacheManager, get_cache_manager, reset_cache_manager
from .rendering import (
    HTMLHelpRenderer,
    HTMLTemplateManager,
    get_html_renderer,
    reset_html_renderer,
)
from .utils import (
    clear_cache_dir,
    get_cache_dir,
    get_custom_groups_path,
    get_data_dir,
    get_logger,
    get_plugin_data_dir,
    get_plugin_dir,
    get_resources_dir,
    get_templates_dir,
    init_plugin_paths,
    logger,
    looks_like_regex,
    normalize_detail_query,
    replace_prefix,
)

__all__ = [
    # Config
    "HelpPluginConfig",
    "RenderingConfig",
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
    "get_templates_dir",
    "get_resources_dir",
    "get_custom_groups_path",
    "clear_cache_dir",
    "replace_prefix",
    "looks_like_regex",
    "normalize_detail_query",
    # Analysis
    "CommandIndex",
    "get_command_index",
    "reset_command_index",
    "invalidate_command_cache",
    "CommandAnalyzer",
    "EventAnalyzer",
    "FilterAnalyzer",
    "get_command_analyzer",
    "get_event_analyzer",
    "get_filter_analyzer",
    "reset_analyzers",
    "CommandExecutor",
    "get_command_executor",
    "reset_command_executor",
    # Rendering
    "HTMLHelpRenderer",
    "HTMLTemplateManager",
    "get_html_renderer",
    "reset_html_renderer",
    # Persistence
    "CacheManager",
    "get_cache_manager",
    "reset_cache_manager",
]
