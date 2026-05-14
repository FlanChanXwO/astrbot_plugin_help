"""Context 单例管理

提供 AstrBot Context 的单例访问。
"""

from __future__ import annotations

from astrbot.api.star import Context

_context_instance: Context | None = None


def set_context(ctx: Context) -> None:
    """设置 Context 实例（在插件 __init__ 中调用一次）。

    Args:
        ctx: AstrBot Context 实例
    """
    global _context_instance
    _context_instance = ctx


def get_context() -> Context:
    """获取 Context 单例。

    Returns:
        Context 实例

    Raises:
        RuntimeError: 如果 Context 未设置
    """
    if _context_instance is None:
        raise RuntimeError("Context not initialized, call set_context() first")
    return _context_instance


def clear_context() -> None:
    """清除 Context 单例（用于测试）。"""
    global _context_instance
    _context_instance = None
