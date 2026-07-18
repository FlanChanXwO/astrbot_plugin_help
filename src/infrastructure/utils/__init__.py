"""基础设施工具模块"""

from .logger import get_logger, logger
from .paths import (
    get_cache_dir,
    get_custom_groups_path,
    get_data_dir,
    get_plugin_data_dir,
    get_plugin_dir,
    init_plugin_paths,
)
from .text import (
    looks_like_regex,
    normalize_detail_query,
    replace_prefix,
)

__all__ = [
    # Logger
    "get_logger",
    "logger",
    # Paths
    "init_plugin_paths",
    "get_plugin_dir",
    "get_data_dir",
    "get_cache_dir",
    "get_plugin_data_dir",
    "get_custom_groups_path",
    # Text
    "looks_like_regex",
    "normalize_detail_query",
    "replace_prefix",
]
