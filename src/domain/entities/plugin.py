"""插件实体定义"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class PluginCommandSummary:
    """插件命令摘要"""

    plugin: str
    plugin_display_name: str | None = None
    plugin_version: str = ""
    plugin_desc: str = ""
    commands: list = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """获取显示名称"""
        return self.plugin_display_name or self.plugin

    @property
    def name(self) -> str:
        """获取插件名（别名）"""
        return self.plugin

    @property
    def desc(self) -> str:
        """获取描述（别名）"""
        return self.plugin_desc

    @property
    def nodes(self) -> list:
        """获取节点列表（别名）"""
        return self.commands

    def clone(self) -> PluginCommandSummary:
        """创建副本"""
        return PluginCommandSummary(
            plugin=self.plugin,
            plugin_display_name=self.plugin_display_name,
            plugin_version=self.plugin_version,
            plugin_desc=self.plugin_desc,
            commands=list(self.commands),
        )

    def to_dict(self) -> dict:
        """转换为模板可用的字典（纯净数据，不包含显示逻辑）"""
        return {
            "name": self.plugin,
            "display_name": self.display_name,
            "version": self.plugin_version,
            "desc": self.plugin_desc,
            "nodes": [
                asdict(node) if hasattr(node, "__dataclass_fields__") else node
                for node in self.commands
            ],
        }
