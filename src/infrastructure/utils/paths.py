"""Plugin path management singleton

Provides singleton access to plugin directory and data directory.
Uses StarTools for data directory management.
"""

from __future__ import annotations

from pathlib import Path

_plugin_dir: Path | None = None
_plugin_name: str | None = None


def init_plugin_paths(plugin_root: Path) -> None:
    """Initialize plugin paths (called once in plugin __init__).

    Args:
        plugin_root: Plugin root directory path
    """
    global _plugin_dir, _plugin_name
    _plugin_dir = plugin_root
    _plugin_name = plugin_root.name


def get_plugin_dir() -> Path:
    """Get plugin root directory.

    Returns:
        Plugin root directory path

    Raises:
        RuntimeError: If paths not initialized
    """
    if _plugin_dir is None:
        raise RuntimeError(
            "Plugin paths not initialized, call init_plugin_paths() first"
        )
    return _plugin_dir


def get_data_dir() -> Path:
    """Get plugin data directory using StarTools.

    Returns:
        Plugin data directory path (data/plugin_data/<plugin_name>)
    """
    from astrbot.api.star import StarTools

    if _plugin_name is None:
        raise RuntimeError(
            "Plugin paths not initialized, call init_plugin_paths() first"
        )
    return StarTools.get_data_dir(_plugin_name)


def get_cache_dir() -> Path:
    """Get cache directory for storing the command index JSON.

    Returns:
        Cache directory path under data/plugin_data/<plugin_name>/cache
    """
    cache_dir = get_data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_plugin_data_dir() -> Path:
    """Get plugin data directory for storing custom groups configuration.

    Returns:
        Data directory path under data/plugin_data/<plugin_name>/data
    """
    data_dir = get_data_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_custom_groups_path() -> Path:
    """Get custom groups configuration file path.

    Returns:
        Path to custom_groups.json
    """
    return get_plugin_data_dir() / "custom_groups.json"


def get_commands_cache_path() -> Path:
    """Get commands info cache JSON file path.

    Returns:
        Path to commands_cache.json
    """
    return get_cache_dir() / "commands_cache.json"
