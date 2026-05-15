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


@dataclass
class RenderNode:
    """渲染节点"""

    name: str
    desc: str = ""
    is_group: bool = False
    tag: str = "normal"
    priority: int | None = None
    type: str = ""
    pattern: str = ""
    show_pattern: bool = True
    usage_hint: str = ""
    examples: list[str] = field(default_factory=list)
    sub_commands: list[str] = field(default_factory=list)
    custom_groups: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    children: list[RenderNode] = field(default_factory=list)

    def sort_children(self) -> None:
        """递归排序子节点：普通命令 -> 正则命令 -> 分组命令"""
        self.children.sort(key=lambda x: x._sort_key())
        for child in self.children:
            child.sort_children()

    def _sort_key(self):
        """排序键：普通命令(0) -> 正则命令(1) -> 分组命令(2)，同类型按名称排序"""
        # 分组命令（含子节点）排最后
        is_group = 2 if (self.is_group or self.children) else 0
        # 正则命令排中间
        is_regex = 1 if self.type == "regex" else 0
        return (is_group + is_regex, self.name)
