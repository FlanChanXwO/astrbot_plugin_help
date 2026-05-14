"""文本处理工具函数"""

from __future__ import annotations


def replace_prefix(text: str, prefix: str) -> str:
    """替换命令前缀（默认为 /）。"""
    if text.startswith("/"):
        return prefix + text[1:]
    return text


def looks_like_regex(pattern: str) -> bool:
    """检查字符串是否看起来像正则表达式。"""
    if not pattern:
        return False
    regex_chars = set(".*+?^$[]{}()\\|")
    return any(c in pattern for c in regex_chars)


def normalize_detail_query(query: str, prefixes: list[str]) -> str:
    """规范化命令查询。

    - 去除首尾空格
    - 去除命令前缀
    """
    query = query.strip()
    for prefix in prefixes:
        if query.startswith(prefix):
            query = query[len(prefix) :]
            break
    return query.strip()


def looks_like_custom_group_command(command: str, custom_groups=None) -> bool:
    """检查命令是否属于自定义命令组。

    Args:
        command: 要检查的命令（不含前缀）
        custom_groups: 自定义命令组列表，如果为None则从配置获取

    Returns:
        如果命令属于任何自定义命令组则返回True
    """
    if not command:
        return False

    # 如果没有提供自定义命令组，从配置获取
    if custom_groups is None:
        try:
            from ...infrastructure.config import get_config

            custom_groups = get_config().custom_groups
        except Exception:
            return False

    if not custom_groups:
        return False

    command_normalized = command.strip().lstrip("/")

    # 检查命令是否属于任何自定义命令组
    for group in custom_groups:
        if group.hidden:
            continue

        for cmd_config in group.commands:
            if cmd_config.hidden:
                continue

            # 检查命令名称
            if cmd_config.command and cmd_config.command.strip() == command_normalized:
                return True

            # 检查别名
            if cmd_config.aliases:
                for alias in cmd_config.aliases:
                    if alias.strip() == command_normalized:
                        return True

    return False
