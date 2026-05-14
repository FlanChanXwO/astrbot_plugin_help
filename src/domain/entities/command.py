"""命令实体定义"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandEntry:
    """命令条目实体"""

    command: str
    description: str
    plugin: str
    plugin_display_name: str | None = None
    plugin_version: str = ""
    aliases: list[str] = field(default_factory=list)
    is_alias_of: str | None = None
    group_name: str | None = None
    tag: str = "normal"
    type: str = "command"
    pattern: str = ""
    show_pattern: bool = True
    examples: list[str] = field(default_factory=list)
    sub_commands: list[str] = field(default_factory=list)
    usage_hint: str = ""
    handler_name: str = ""
    custom_groups: list[str] = field(default_factory=list)
    priority: int | None = None
    inactive: bool = False  # 标记是否来自禁用的插件

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "command": self.command,
            "description": self.description,
            "plugin": self.plugin,
            "plugin_display_name": self.plugin_display_name,
            "plugin_version": self.plugin_version,
            "aliases": self.aliases,
            "is_alias_of": self.is_alias_of,
            "group_name": self.group_name,
            "tag": self.tag,
            "type": self.type,
            "pattern": self.pattern,
            "show_pattern": self.show_pattern,
            "examples": self.examples,
            "sub_commands": self.sub_commands,
            "usage_hint": self.usage_hint,
            "handler_name": self.handler_name,
            "custom_groups": self.custom_groups,
            "priority": self.priority,
            "inactive": self.inactive,
        }


@dataclass
class MatchedHandlerInfo:
    """匹配的处理器信息"""

    handler_full_name: str
    handler_name: str
    plugin: str

    def to_dict(self) -> dict[str, str]:
        return {
            "handler_full_name": self.handler_full_name,
            "handler_name": self.handler_name,
            "plugin": self.plugin,
        }
