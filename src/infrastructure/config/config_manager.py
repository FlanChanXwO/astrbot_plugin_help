"""配置管理模块

提供统一的、类型安全的插件配置访问。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..utils.logger import get_logger
from .datamodels import CustomGroupConfig, HelpPluginConfig

# ---------------------------------------------------------------------------
# 单例管理 + 自定义组持久化
# ---------------------------------------------------------------------------

_config_instance: HelpPluginConfig | None = None


def init_config(raw_config: dict[str, Any] | None) -> HelpPluginConfig | None:
    """初始化配置单例（在插件 __init__ 中调用一次）"""
    global _config_instance
    _config_instance = HelpPluginConfig.from_astrbot_config(raw_config)
    # v2 的权威源是 SQLite。此时 runtime 尚未建库，初始化完成后由
    # HelpService 从 CommandCatalog 发布同一份内存快照。
    _config_instance.custom_groups = []
    return _config_instance


def get_config() -> HelpPluginConfig:
    """获取配置单例

    Raises:
        ConfigNotInitializedError: 如果配置未初始化
    """
    if _config_instance is None:
        from ...domain.exceptions import ConfigNotInitializedError

        raise ConfigNotInitializedError(
            "Config not initialized. Call init_config() first."
        )
    return _config_instance


def refresh_config(raw_config: dict[str, Any] | None) -> HelpPluginConfig:
    """刷新配置（配置变更时调用）"""
    global _config_instance
    current_groups = _config_instance.custom_groups if _config_instance else []
    _config_instance = HelpPluginConfig.from_astrbot_config(raw_config)
    _config_instance.custom_groups = current_groups
    return _config_instance


def clear_config() -> None:
    """清除配置单例（测试用）"""
    global _config_instance
    _config_instance = None


def update_custom_groups_in_config(groups: list[CustomGroupConfig]) -> None:
    """更新内存中配置的自定义分组"""
    global _config_instance
    if _config_instance is not None:
        _config_instance.custom_groups = groups


# ---------------------------------------------------------------------------
# 自定义分组 JSON 持久化
# ---------------------------------------------------------------------------


def _get_custom_groups_storage_path() -> Path:
    """获取自定义分组存储路径"""
    from ..utils.paths import get_custom_groups_path

    return get_custom_groups_path()


def _load_custom_groups_from_storage() -> list[CustomGroupConfig]:
    """从持久化 JSON 加载自定义分组"""
    storage_path = _get_custom_groups_storage_path()
    logger = get_logger()

    if not storage_path.exists():
        logger.debug(f"Custom groups storage not found at {storage_path}")
        return []

    try:
        import json

        logger.debug(f"Loading custom groups from {storage_path}")
        with open(storage_path, encoding="utf-8") as f:
            data = json.load(f)
        logger.debug(
            f"Loaded JSON data with {len(data) if isinstance(data, list) else 0} items"
        )

        if not isinstance(data, list):
            logger.warning(f"Custom groups data is not a list: {type(data)}")
            return []

        groups = [CustomGroupConfig.model_validate(item) for item in data]
        logger.info(
            f"Successfully loaded {len(groups)} custom groups: {[g.group_name for g in groups]}"
        )
        return groups
    except Exception as e:
        logger.warning(f"Failed to load custom groups from storage: {e}")
        return []


def _sync_storage_directory(directory: Path) -> None:
    """尽力同步原子替换后的目录项，不支持时不影响已成功的保存。"""
    logger = get_logger()
    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except (AttributeError, OSError) as e:
        logger.warning(
            f"Custom groups file was saved but directory sync is unavailable for "
            f"{directory}: {e}"
        )
        return

    try:
        os.fsync(directory_fd)
    except OSError as e:
        logger.warning(
            f"Custom groups file was saved but directory sync failed for {directory}: {e}"
        )
    finally:
        try:
            os.close(directory_fd)
        except OSError as e:
            logger.warning(f"Failed to close custom groups directory handle: {e}")


def save_custom_groups_to_storage(groups: list[CustomGroupConfig]) -> bool:
    """通过同目录临时文件原子保存自定义分组到持久化 JSON。"""
    storage_path = _get_custom_groups_storage_path()
    logger = get_logger()
    temporary_path: Path | None = None

    try:
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = [group.model_dump() for group in groups]

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=storage_path.parent,
            prefix=f".{storage_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temporary_path = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temporary_path, storage_path)
        _sync_storage_directory(storage_path.parent)
        return True
    except Exception as e:
        logger.exception(f"Failed to save custom groups to {storage_path}: {e}")
        return False
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as e:
                logger.exception(
                    f"Failed to remove temporary custom groups file {temporary_path}: {e}"
                )
