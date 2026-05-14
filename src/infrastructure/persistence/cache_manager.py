"""缓存管理器 - 单例模式"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from ...infrastructure.config import get_config
from ..utils.logger import get_logger
from ..utils.paths import get_data_dir

if TYPE_CHECKING:
    pass

logger = get_logger()


class CacheManager:
    """图片缓存管理器"""

    def __init__(self):
        self._image_cache: dict[str, str] = {}
        self._cache_lock = asyncio.Lock()
        self.data_dir = get_data_dir()

    async def get_cached_image(self, cache_key: str) -> str | None:
        """获取缓存的图片路径"""
        async with self._cache_lock:
            cached_path = self._image_cache.get(cache_key)
            if cached_path and Path(cached_path).exists():
                return cached_path
            # 缓存失效，清理
            if cache_key in self._image_cache:
                del self._image_cache[cache_key]
            return None

    async def set_cached_image(self, cache_key: str, image_path: str):
        """设置缓存的图片路径"""
        async with self._cache_lock:
            self._image_cache[cache_key] = image_path

    async def clear_cache(self):
        """清空图片缓存"""
        async with self._cache_lock:
            self._image_cache.clear()
        logger.info("图片缓存已清空")

    def get_cache_key(self, mode: str, query: str | None, is_admin: bool) -> str:
        """生成缓存键"""
        import hashlib
        import json

        from ..analysis.command_index import get_command_index

        config = get_config()
        command_index = get_command_index()

        # 获取所有插件名称并排序
        try:
            context = command_index.context
            all_stars = context.get_all_stars()
            plugin_names = sorted(
                [
                    getattr(star, "name", "")
                    for star in all_stars
                    if getattr(star, "activated", False)
                ]
            )
        except Exception:
            plugin_names = []

        # 组合缓存键数据
        cache_data = {
            "plugins": plugin_names,
            "mode": mode,
            "query": query,
            "is_admin": is_admin,
            "html_theme": config.html_theme,
            "custom_groups": sorted(
                g.group_name for g in config.custom_groups
            ),
        }

        # 生成哈希
        cache_str = json.dumps(cache_data, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(cache_str.encode()).hexdigest()


# 单例实例
_cache_manager_instance: CacheManager | None = None


def get_cache_manager() -> CacheManager:
    """获取缓存管理器单例。

    Returns:
        CacheManager 实例
    """
    global _cache_manager_instance
    if _cache_manager_instance is None:
        _cache_manager_instance = CacheManager()
    return _cache_manager_instance


def reset_cache_manager() -> None:
    """重置缓存管理器（用于测试）。"""
    global _cache_manager_instance
    _cache_manager_instance = None
