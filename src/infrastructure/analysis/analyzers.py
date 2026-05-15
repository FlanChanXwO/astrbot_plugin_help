"""分析器 - 单例模式

提供命令、事件、过滤器的分析功能。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from astrbot.core.agent.mcp_client import MCPTool
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.event_message_type import (
    EventMessageType,
    EventMessageTypeFilter,
)
from astrbot.core.star.filter.platform_adapter_type import (
    PlatformAdapterType,
    PlatformAdapterTypeFilter,
)
from astrbot.core.star.filter.regex import RegexFilter
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)

from ...domain import PluginCommandSummary, RenderNode
from ...infrastructure.config import get_config
from ...shared import InternalCFG
from ..context_holder import get_context
from ..utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger()


class BaseAnalyzer:
    """基础分析器"""

    def __init__(self):
        self.context = get_context()
        self.cfg = get_config()

    def _sanitize_display_desc(self, value) -> str:
        """清理显示描述"""
        desc = str(value or "").strip()
        if not desc:
            return ""

        desc = desc.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in desc.split("\n") if line.strip()]
        if not lines:
            return ""

        candidate = lines[0]
        if len(candidate) > 120:
            candidate = candidate[:117].rstrip() + "..."

        if self._looks_like_code(candidate):
            return ""

        return candidate

    def _sanitize_handler_desc(self, handler) -> str:
        """从处理器获取描述"""
        return self._sanitize_display_desc(getattr(handler, "desc", ""))

    def _looks_like_code(self, text: str) -> bool:
        """检查文本是否像代码"""
        lowered = text.lower()
        code_markers = (
            "def ",
            "class ",
            "return ",
            "import ",
            "from ",
            "async def ",
            "await ",
            "yield ",
            "```",
            "#include",
        )
        if any(marker in lowered for marker in code_markers):
            return True
        if text.count("(") >= 2 and text.count(")") >= 2:
            return True
        if re.search(r"\bself\.[A-Za-z_]", text):
            return True
        if re.search(r"[{};<>]{2,}", text):
            return True
        return False

    def get_plugins(
        self,
        query: str | None = None,
        allowed_plugins: set[str] | None = None,
    ) -> list[PluginCommandSummary]:
        """获取插件列表"""
        if not query:
            query = None

        try:
            structured_plugins = self.analyze_hierarchy(allowed_plugins=allowed_plugins)

            if not query:
                return structured_plugins

            q_lower = query.lower()
            filtered_plugins = []

            for p in structured_plugins:
                p_copy = p.clone()
                is_container_match = self._is_match(
                    p_copy.plugin, p_copy.display_name, p_copy.plugin_desc, q_lower
                )

                if is_container_match:
                    filtered_plugins.append(p_copy)
                else:
                    matched_nodes = self._filter_nodes_recursively(
                        p_copy.commands, q_lower
                    )
                    if matched_nodes:
                        p_copy.commands = matched_nodes
                        filtered_plugins.append(p_copy)

            return filtered_plugins

        except Exception as e:
            logger.error(f"分析失败: {e}", exc_info=True)
            return []

    def _is_match(self, name: str, display: str | None, desc: str, query: str) -> bool:
        """基础匹配检查"""
        if query in name.lower():
            return True
        if display and query in display.lower():
            return True
        if desc and query in desc.lower():
            return True
        return False

    def _filter_nodes_recursively(
        self, nodes: list[RenderNode], query: str
    ) -> list[RenderNode]:
        """递归过滤节点"""
        result = []
        for node in nodes:
            self_match = self._is_match(node.name, None, node.desc, query)

            if self_match:
                result.append(node)
            else:
                if node.children:
                    filtered_children = self._filter_nodes_recursively(
                        node.children, query
                    )
                    if filtered_children:
                        node.children = filtered_children
                        result.append(node)
        return result

    def analyze_hierarchy(
        self, allowed_plugins: set[str] | None = None
    ) -> list[PluginCommandSummary]:
        """分析层级结构（子类实现）"""
        raise NotImplementedError

    def _group_handlers_by_module(self) -> dict[str, list[StarHandlerMetadata]]:
        """按模块分组处理器"""
        mapping = defaultdict(list)
        for handler in star_handlers_registry:
            if isinstance(handler, StarHandlerMetadata) and handler.handler_module_path:
                mapping[handler.handler_module_path].append(handler)
        return mapping

    def _get_safe_plugin_info(self, star_meta) -> dict[str, str | None]:
        """安全获取插件信息"""
        if not star_meta:
            return {"name": "Unknown", "display_name": None, "version": "", "desc": ""}

        raw_name = getattr(star_meta, "name", None)
        raw_root_dir = getattr(star_meta, "root_dir_name", None)
        raw_module = getattr(star_meta, "module_path", None)

        if raw_name:
            safe_name = str(raw_name)
        elif raw_root_dir:
            safe_name = str(raw_root_dir)
        elif raw_module:
            parts = str(raw_module).split(".")
            safe_name = (
                parts[-2] if len(parts) > 2 and parts[-1] == "main" else parts[-1]
            )
        else:
            safe_name = f"Unknown_{id(star_meta)}"

        display = getattr(star_meta, "display_name", None)
        version = str(getattr(star_meta, "version", "")) or ""
        desc = str(getattr(star_meta, "desc", "")) or ""

        return {
            "name": safe_name,
            "display_name": display,
            "version": version,
            "desc": desc,
            "raw_module": raw_module,
        }


class CommandAnalyzer(BaseAnalyzer):
    """命令分析器"""

    def __init__(self):
        super().__init__()
        from .command_index import get_command_index

        self.command_index = get_command_index()

    def analyze_hierarchy(
        self, allowed_plugins: set[str] | None = None
    ) -> list[PluginCommandSummary]:
        """分析命令层级"""
        self.command_index.update_config()
        summaries = self.command_index.get_plugin_summaries(
            allowed_plugins=allowed_plugins
        )
        results: list[PluginCommandSummary] = []

        # _simple_commands 插件（简易命令）会显示在帮助菜单中
        # _custom_group_* 插件（用户创建的自定义组）也会显示

        logger.info(
            f"开始分析指令，共 {len(summaries)} 个插件摘要: {list(summaries.keys())}"
        )

        for summary in summaries.values():
            children = self._build_plugin_command_tree(summary.commands)
            logger.info(
                f"Plugin '{summary.plugin}': {len(summary.commands)} commands, {len(children)} nodes after tree build"
            )
            if not children:
                continue

            results.append(
                PluginCommandSummary(
                    plugin=summary.plugin,
                    plugin_display_name=summary.plugin_display_name,
                    plugin_version=summary.plugin_version,
                    plugin_desc=self._sanitize_display_desc(summary.plugin_desc),
                    commands=children,
                )
            )

        # Sort: virtual plugins (_custom_group_*) last, then by display_name
        results.sort(
            key=lambda item: (
                item.plugin.startswith("_custom_group_"),  # True sorts after False
                item.plugin_display_name is None,
                item.plugin_display_name or item.plugin,
            )
        )
        logger.info(
            f"指令分析完成，得到 {len(results)} 个插件: {[r.plugin for r in results]}"
        )
        return results

    def _build_plugin_command_tree(self, commands: list) -> list[RenderNode]:
        """构建插件命令树"""
        groups: dict[str, list] = defaultdict(list)
        standalone: list = []
        group_placeholders: dict[str, Any] = {}  # type=group 的占位命令
        group_aliases: dict[str, list[str]] = defaultdict(list)  # 分组别名

        for command in commands:
            group_name = getattr(command, "group_name", None)
            if group_name:
                if getattr(command, "type", "") == "group":
                    # 分组占位命令，收集别名后不加入 groups
                    group_placeholders[group_name] = command
                    # 收集分组别名
                    cmd_aliases = getattr(command, "aliases", [])
                    if cmd_aliases:
                        for alias in cmd_aliases:
                            alias_name = (
                                alias.lstrip("/") if alias.startswith("/") else alias
                            )
                            if alias_name != group_name:
                                group_aliases[group_name].append(alias_name)
                else:
                    groups[group_name].append(command)
            else:
                standalone.append(command)

        nodes: list[RenderNode] = []
        group_nodes: list[RenderNode] = []

        # 先添加独立命令（普通命令和正则）
        nodes.extend(self._parse_command_node(command) for command in standalone)

        # 处理有成员的分组（命令组）
        for group_name, group_commands in groups.items():
            children = [self._parse_command_node(command) for command in group_commands]

            # 获取分组描述和别名
            placeholder = group_placeholders.get(group_name)
            group_desc = ""
            group_tag = self._pick_group_tag(group_commands)
            if placeholder:
                group_desc = getattr(
                    placeholder, "description", ""
                ) or self._build_group_desc(group_commands)
                placeholder_tag = getattr(placeholder, "tag", "normal")
                if placeholder_tag == "admin":
                    group_tag = "admin"

            if len(children) == 1 and children[0].name == group_name:
                # 单命令分组扁平化，附加别名
                node = children[0]
                aliases = group_aliases.get(group_name, [])
                if aliases and not node.aliases:
                    node.aliases = aliases
                group_nodes.append(node)
            else:
                group_node = RenderNode(
                    name=group_name,
                    desc=group_desc or self._build_group_desc(group_commands),
                    is_group=True,
                    tag=group_tag,
                    type="group",
                    children=children,
                )
                # 附加分组别名
                aliases = group_aliases.get(group_name, [])
                if aliases:
                    group_node.aliases = aliases
                group_nodes.append(group_node)

        # 处理空分组（有占位命令但无子命令）
        for group_name, placeholder in group_placeholders.items():
            if group_name not in groups:
                aliases = group_aliases.get(group_name, [])
                group_nodes.append(
                    RenderNode(
                        name=group_name,
                        desc=getattr(placeholder, "description", "") or "空分组",
                        is_group=True,
                        tag=getattr(placeholder, "tag", "normal"),
                        type="group",
                        children=[],
                        aliases=aliases,
                    )
                )

        # 合并节点：普通命令 -> 正则命令 -> 分组命令（分组始终在最后）
        all_nodes = nodes + group_nodes
        all_nodes.sort(key=lambda x: x._sort_key())
        # 递归排序所有分组内部的子节点
        for node in all_nodes:
            node.sort_children()
        return all_nodes

    def _parse_command_node(self, command) -> RenderNode:
        """解析命令节点"""
        pattern = getattr(command, "pattern", "") or ""
        show_pattern = getattr(command, "show_pattern", True)
        is_regex = getattr(command, "type", "command") == "regex"

        if is_regex and pattern and show_pattern:
            # 正则命令：name 显示 pattern，不再单独显示 pattern 行（避免重复）
            display_name = pattern
            show_pattern = False
        else:
            display_name = getattr(command, "command", "")

        return RenderNode(
            name=display_name.lstrip("/"),
            desc=getattr(command, "description", "") or "",
            is_group=False,
            tag=getattr(command, "tag", "normal"),
            type=getattr(command, "type", "command") or "command",
            pattern=pattern if show_pattern else "",
            show_pattern=show_pattern,
            usage_hint=getattr(command, "usage_hint", "") or "",
            examples=list(getattr(command, "examples", []) or []),
            sub_commands=list(getattr(command, "sub_commands", []) or []),
            custom_groups=list(getattr(command, "custom_groups", []) or []),
            aliases=[a.lstrip("/") for a in getattr(command, "aliases", []) or []],
        )

    def _build_group_desc(self, commands: list) -> str:
        """构建分组描述"""
        # 优先检查自定义组标记
        if any(getattr(command, "custom_groups", []) for command in commands):
            return "自定义命令组"
        if any(getattr(command, "type", "command") == "group" for command in commands):
            return "自定义命令组"
        if any(getattr(command, "type", "command") == "regex" for command in commands):
            return "文本触发规则组"
        return ""

    def _pick_group_tag(self, commands: list) -> str:
        """选择分组标签"""
        if any(getattr(command, "tag", "normal") == "admin" for command in commands):
            return "admin"
        if any(getattr(command, "type", "command") == "regex" for command in commands):
            return "regex_pattern"
        return "normal"


class EventAnalyzer(BaseAnalyzer):
    """事件分析器"""

    def analyze_hierarchy(
        self, allowed_plugins: set[str] | None = None
    ) -> list[PluginCommandSummary]:
        """分析事件层级"""
        results = []

        # 映射模块路径到插件对象
        module_to_plugin = {}
        all_stars = self.context.get_all_stars()
        for star in all_stars:
            if star.module_path:
                module_to_plugin[star.module_path] = star

        # 处理函数工具 (Plugin Tools + MCP Tools)
        tool_manager = None
        if hasattr(self.context, "get_llm_tool_manager"):
            tool_manager = self.context.get_llm_tool_manager()

        if tool_manager:
            for tool in tool_manager.func_list:
                if not tool.active:
                    continue

                source_name = "Unknown"
                source_display = None
                source_version = ""
                tag = "tool"

                if MCPTool and isinstance(tool, MCPTool):
                    source_name = f"MCP/{tool.mcp_server_name}"
                    source_display = f"🔌 {tool.mcp_server_name}"
                    tag = "mcp"
                elif tool.handler_module_path:
                    plugin = module_to_plugin.get(tool.handler_module_path)
                    if plugin:
                        if plugin.name in self.cfg.ignored_plugins:
                            continue
                        if (
                            allowed_plugins is not None
                            and plugin.name not in allowed_plugins
                        ):
                            continue
                        source_name = plugin.name
                        source_display = getattr(plugin, "display_name", None)
                        source_version = getattr(plugin, "version", "")
                    else:
                        source_name = "Core/Unknown"

                desc = self._sanitize_display_desc(tool.description)
                node = RenderNode(name=tool.name, desc=desc, is_group=False, tag=tag)

                pm = PluginCommandSummary(
                    name=source_name,
                    display_name=source_display,
                    version=source_version,
                    desc="",
                    nodes=[node],
                )
                results.append(pm)

        # 处理普通事件
        event_groups = defaultdict(list)

        for handler in star_handlers_registry:
            if not isinstance(handler, StarHandlerMetadata):
                continue

            if self._is_command_handler(handler):
                continue
            if handler.event_type == EventType.OnCallingFuncToolEvent:
                continue

            if handler.handler_module_path in module_to_plugin:
                plugin = module_to_plugin[handler.handler_module_path]
                info = self._get_safe_plugin_info(plugin)
                if info["name"] in self.cfg.ignored_plugins:
                    continue
                if allowed_plugins is not None and info["name"] not in allowed_plugins:
                    continue
                if not plugin.activated:
                    continue
            else:
                continue

            event_groups[handler.event_type].append(handler)

        for evt_type, handlers in event_groups.items():
            card_title = InternalCFG.EVENT_TYPE_MAP.get(evt_type, str(evt_type.name))

            nodes = []
            for h in handlers:
                plugin = module_to_plugin.get(h.handler_module_path)
                p_info = (
                    self._get_safe_plugin_info(plugin)
                    if plugin
                    else {"name": "System", "display_name": None}
                )

                p_name = p_info["name"]
                p_display = p_info["display_name"]
                main_name = p_display if p_display else p_name

                raw_desc = self._sanitize_handler_desc(h)
                full_desc = ""
                if p_display:
                    full_desc = f"@{p_name}"
                if raw_desc:
                    if full_desc:
                        full_desc += f" · {raw_desc}"
                    else:
                        full_desc = raw_desc

                prio = h.extras_configs.get("priority", 0)
                nodes.append(
                    RenderNode(
                        name=main_name,
                        desc=full_desc,
                        is_group=False,
                        tag="event_listener",
                        priority=prio,
                    )
                )

            nodes.sort(key=lambda x: x.name)
            nodes.sort(
                key=lambda x: x.priority if x.priority is not None else 0, reverse=True
            )

            pm = PluginCommandSummary(
                name="event_group",
                display_name=card_title,
                version="",
                desc=f"共 {len(nodes)} 个挂载点",
                nodes=nodes,
            )
            results.append(pm)

        return results

    def _is_command_handler(self, handler: StarHandlerMetadata) -> bool:
        """检查是否为命令处理器"""
        if not handler.event_filters:
            return False
        for f in handler.event_filters:
            if isinstance(f, (CommandFilter, CommandGroupFilter)):
                return True
        return False


class FilterAnalyzer(BaseAnalyzer):
    """过滤器分析器"""

    def analyze_hierarchy(
        self, allowed_plugins: set[str] | None = None
    ) -> list[PluginCommandSummary]:
        """分析过滤器层级"""
        results = []
        module_to_plugin = {}
        all_stars = self.context.get_all_stars()
        for star in all_stars:
            if star.module_path:
                module_to_plugin[star.module_path] = star

        regex_data = defaultdict(list)
        platform_data = defaultdict(list)
        msgtype_data = defaultdict(list)

        for handler in star_handlers_registry:
            if not isinstance(handler, StarHandlerMetadata):
                continue

            if handler.handler_module_path in module_to_plugin:
                plugin = module_to_plugin[handler.handler_module_path]
                p_info = self._get_safe_plugin_info(plugin)
                if p_info["name"] in self.cfg.ignored_plugins:
                    continue
                if (
                    allowed_plugins is not None
                    and p_info["name"] not in allowed_plugins
                ):
                    continue
                if not plugin.activated:
                    continue
            else:
                continue

            if not handler.event_filters:
                continue

            for f in handler.event_filters:
                if isinstance(f, RegexFilter):
                    regex_data[handler.handler_module_path].append(
                        (f.regex_str, handler)
                    )
                elif isinstance(f, PlatformAdapterTypeFilter):
                    names = self._format_flags(f.platform_type, PlatformAdapterType)
                    key = f"🌍 {names}"
                    platform_data[key].append(handler)
                elif isinstance(f, EventMessageTypeFilter):
                    names = self._format_flags(f.event_message_type, EventMessageType)
                    key = f"📨 {names}"
                    msgtype_data[key].append(handler)

        # Regex 卡片
        if regex_data:
            nodes = []
            for mod_path, items in regex_data.items():
                plugin = module_to_plugin.get(mod_path)
                p_info = (
                    self._get_safe_plugin_info(plugin)
                    if plugin
                    else self._get_safe_plugin_info(None)
                )
                p_name = p_info["name"]
                p_display = p_info["display_name"]

                sorted_items = sorted(items, key=lambda x: x[0])

                children = []
                for r_str, h in sorted_items:
                    raw_desc = self._sanitize_handler_desc(h)
                    full_desc = f"#{h.handler_name}"
                    if raw_desc:
                        full_desc += f" · {raw_desc}"

                    children.append(
                        RenderNode(
                            name=r_str,
                            desc=full_desc,
                            is_group=False,
                            tag="regex_pattern",
                        )
                    )

                container_desc = f"@{p_name}" if p_display else ""
                container_name = p_display if p_display else p_name

                nodes.append(
                    RenderNode(
                        name=container_name,
                        desc=container_desc,
                        is_group=True,
                        tag="plugin_container",
                        children=children,
                    )
                )

            nodes.sort(key=lambda x: x.name)

            results.append(
                PluginCommandSummary(
                    name="filter_regex",
                    display_name="正则触发器 (Regex)",
                    version="",
                    desc=f"共 {len(nodes)} 个插件使用了正则",
                    nodes=nodes,
                )
            )

        # Platform 卡片
        if platform_data:
            results.append(
                self._build_criteria_card(
                    "平台限制 (Platform)", "platform", platform_data, module_to_plugin
                )
            )

        # MsgType 卡片
        if msgtype_data:
            results.append(
                self._build_criteria_card(
                    "消息类型限制 (MsgType)", "msg_type", msgtype_data, module_to_plugin
                )
            )

        return results

    def _build_criteria_card(
        self,
        title: str,
        tag_prefix: str,
        data: dict[str, list[StarHandlerMetadata]],
        module_to_plugin: dict,
    ) -> PluginCommandSummary:
        """构建条件卡片"""
        nodes = []
        sorted_keys = sorted(data.keys())

        for filter_str in sorted_keys:
            handlers = data[filter_str]
            children = []

            for h in handlers:
                plugin = module_to_plugin.get(h.handler_module_path)
                p_info = (
                    self._get_safe_plugin_info(plugin)
                    if plugin
                    else self._get_safe_plugin_info(None)
                )
                p_name = p_info["name"]
                p_display = p_info["display_name"]

                main_name = p_display if p_display else p_name

                raw_desc = self._sanitize_handler_desc(h)

                parts = []
                if p_display:
                    parts.append(f"@{p_name}")
                parts.append(f"#{h.handler_name}")
                if raw_desc:
                    parts.append(raw_desc)

                full_desc = " · ".join(parts)
                prio = h.extras_configs.get("priority", 0)

                children.append(
                    RenderNode(
                        name=main_name,
                        desc=full_desc,
                        is_group=False,
                        tag="event_listener",
                        priority=prio,
                    )
                )

            children.sort(key=lambda x: x.name)
            children.sort(
                key=lambda x: x.priority if x.priority is not None else 0, reverse=True
            )

            nodes.append(
                RenderNode(
                    name=filter_str,
                    desc=f"{len(children)} 个监听点",
                    is_group=True,
                    tag="filter_criteria",
                    children=children,
                )
            )

        return PluginCommandSummary(
            name=f"filter_{tag_prefix}",
            display_name=title,
            version="",
            desc=f"共 {len(data)} 种过滤条件",
            nodes=nodes,
        )

    def _format_flags(self, value, enum_cls):
        """格式化标志位"""
        if value is None:
            return "None"
        if hasattr(enum_cls, "ALL") and value == enum_cls.ALL:
            return "ALL"

        members = []
        for member in enum_cls:
            if member.name == "ALL":
                continue
            if member in value:
                members.append(member.name)

        if not members:
            return "None"
        return " | ".join(members)


# 分析器单例实例
_command_analyzer: CommandAnalyzer | None = None
_event_analyzer: EventAnalyzer | None = None
_filter_analyzer: FilterAnalyzer | None = None


def get_command_analyzer() -> CommandAnalyzer:
    """获取命令分析器单例。"""
    global _command_analyzer
    if _command_analyzer is None:
        _command_analyzer = CommandAnalyzer()
    return _command_analyzer


def get_event_analyzer() -> EventAnalyzer:
    """获取事件分析器单例。"""
    global _event_analyzer
    if _event_analyzer is None:
        _event_analyzer = EventAnalyzer()
    return _event_analyzer


def get_filter_analyzer() -> FilterAnalyzer:
    """获取过滤器分析器单例。"""
    global _filter_analyzer
    if _filter_analyzer is None:
        _filter_analyzer = FilterAnalyzer()
    return _filter_analyzer


def reset_analyzers() -> None:
    """重置所有分析器（用于测试）。"""
    global _command_analyzer, _event_analyzer, _filter_analyzer
    _command_analyzer = None
    _event_analyzer = None
    _filter_analyzer = None
