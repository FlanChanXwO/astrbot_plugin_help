"""命令索引器 - 单例模式

提供命令索引和搜索功能。
"""

from __future__ import annotations

import collections
import json
import re
from collections.abc import Iterable

from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.permission import PermissionTypeFilter
from astrbot.core.star.filter.regex import RegexFilter
from astrbot.core.star.star_handler import StarHandlerMetadata, star_handlers_registry

from ...domain import CommandEntry, PluginCommandSummary
from ...infrastructure.config import CustomGroupConfig, get_config
from ..context_holder import get_context
from ..utils.logger import get_logger
from ..utils.paths import get_commands_cache_path
from .regex_helper import build_regex_usage_hint, generate_regex_examples

logger = get_logger()


class CommandIndex:
    """命令索引器"""

    def __init__(self):
        self._command_cache: dict[str, dict] | None = None
        self._plugin_cache: dict[str, PluginCommandSummary] | None = None
        self._handler_index: dict[str, list[StarHandlerMetadata]] | None = None
        self._last_star_count = 0
        self._last_custom_groups_signature: str = ""
        self._regex_example_limit = 10
        self._custom_groups: list[CustomGroupConfig] = []

    def update_ignored_plugins(self, ignored_plugins: Iterable[str]):
        """更新忽略插件列表"""
        config = get_config()
        new_set = set(ignored_plugins)
        if new_set != config.ignored_plugins:
            config.ignored_plugins = new_set
            self.reset_cache()

    def update_config(self):
        """从配置更新设置"""
        config = get_config()
        self._regex_example_limit = config.regex.max_examples
        self._custom_groups = list(config.custom_groups)
        logger.debug(
            f"Updated config with {len(self._custom_groups)} custom groups: {[g.group_name for g in self._custom_groups]}"
        )

    def reset_cache(self):
        """重置缓存"""
        self._command_cache = None
        self._plugin_cache = None
        self._handler_index = None
        self._last_star_count = 0
        # Clear persistent cache
        self._clear_persistent_cache()

    def _clear_persistent_cache(self):
        """清除持久化缓存文件"""
        try:
            cache_path = get_commands_cache_path()
            if cache_path.exists():
                cache_path.unlink()
                logger.debug("Persistent command cache cleared")
        except Exception as exc:
            logger.warning(f"Failed to clear persistent cache: {exc}")

    def _save_to_persistent_cache(self, commands_dict: dict, plugin_dict: dict):
        """保存命令索引到持久化缓存"""
        try:
            cache_path = get_commands_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            # Convert to serializable format
            cache_data = {
                "version": 1,
                "commands": commands_dict,
                "plugins": {
                    name: {
                        "plugin": summary.plugin,
                        "plugin_display_name": summary.plugin_display_name,
                        "plugin_version": summary.plugin_version,
                        "plugin_desc": summary.plugin_desc,
                        "commands": [cmd.to_dict() for cmd in summary.commands],
                    }
                    for name, summary in plugin_dict.items()
                },
            }

            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            logger.debug(
                f"Saved command index to persistent cache: {len(commands_dict)} commands"
            )
        except Exception as exc:
            logger.warning(f"Failed to save persistent cache: {exc}")

    def _load_from_persistent_cache(
        self,
    ) -> tuple[dict[str, dict], dict[str, PluginCommandSummary]] | None:
        """从持久化缓存加载命令索引"""
        try:
            cache_path = get_commands_cache_path()
            if not cache_path.exists():
                return None

            with open(cache_path, encoding="utf-8") as f:
                cache_data = json.load(f)

            if cache_data.get("version") != 1:
                logger.debug("Persistent cache version mismatch, rebuilding")
                return None

            commands_dict = cache_data.get("commands", {})
            plugin_dict = {}

            for name, data in cache_data.get("plugins", {}).items():
                from ...domain import CommandEntry

                commands = []
                for cmd_data in data.get("commands", []):
                    try:
                        entry = CommandEntry(
                            command=cmd_data["command"],
                            description=cmd_data.get("description", ""),
                            plugin=cmd_data.get("plugin", name),
                            plugin_display_name=cmd_data.get("plugin_display_name"),
                            plugin_version=cmd_data.get("plugin_version", ""),
                            aliases=cmd_data.get("aliases", []),
                            is_alias_of=cmd_data.get("is_alias_of"),
                            group_name=cmd_data.get("group_name"),
                            tag=cmd_data.get("tag", "normal"),
                            type=cmd_data.get("type", "command"),
                            pattern=cmd_data.get("pattern", ""),
                            show_pattern=cmd_data.get("show_pattern", True),
                            examples=cmd_data.get("examples", []),
                            sub_commands=cmd_data.get("sub_commands", []),
                            usage_hint=cmd_data.get("usage_hint", ""),
                            handler_name=cmd_data.get("handler_name", ""),
                            custom_groups=cmd_data.get("custom_groups", []),
                            priority=cmd_data.get("priority"),
                        )
                        commands.append(entry)
                    except Exception as e:
                        logger.debug(f"Failed to load command entry: {e}")
                        continue

                summary = PluginCommandSummary(
                    plugin=data["plugin"],
                    plugin_display_name=data.get("plugin_display_name"),
                    plugin_version=data.get("plugin_version", ""),
                    plugin_desc=data.get("plugin_desc", ""),
                    commands=commands,
                )
                plugin_dict[name] = summary

            logger.debug(
                f"Loaded command index from persistent cache: {len(commands_dict)} commands"
            )
            return commands_dict, plugin_dict
        except Exception as exc:
            logger.debug(f"Failed to load persistent cache: {exc}")
            return None

    def replace_prefix(self, command: str, prefix: str) -> str:
        """替换命令前缀"""
        if command.startswith("/"):
            return prefix + command[1:]
        return command

    def _sanitize_display_desc(self, value: str | None) -> str:
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

    def _sanitize_handler_desc(self, handler: StarHandlerMetadata) -> str:
        """从处理器获取描述"""
        return self._sanitize_display_desc(getattr(handler, "desc", ""))

    def _looks_like_code(self, text: str) -> bool:
        """检查文本是否看起来像代码"""
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

    def get_all_commands(self) -> dict[str, dict]:
        """获取所有命令"""
        # 如果内存缓存非空且有数据，且不需要刷新，则返回缓存
        if (
            self._command_cache is not None
            and len(self._command_cache) > 0
            and not self._should_refresh_cache()
        ):
            return self._command_cache

        # 尝试从持久化缓存加载
        if self._command_cache is None or len(self._command_cache) == 0:
            cached = self._load_from_persistent_cache()
            if cached:
                self._command_cache, self._plugin_cache = cached
                logger.info(
                    f"Loaded {len(self._plugin_cache)} plugins, {len(self._command_cache)} triggers from persistent cache"
                )
                # 重新应用自定义分组，因为缓存可能过时
                if self._custom_groups:
                    # 先移除旧的虚拟插件和命令
                    stale_plugin_keys = [
                        k for k in self._plugin_cache if k.startswith("_custom_group_")
                    ]
                    for k in stale_plugin_keys:
                        del self._plugin_cache[k]
                    stale_cmd_keys = [
                        k
                        for k, v in self._command_cache.items()
                        if v.get("plugin", "").startswith("_custom_group_")
                    ]
                    for k in stale_cmd_keys:
                        del self._command_cache[k]
                    # 重新应用自定义分组
                    self._apply_custom_groups(self._plugin_cache, self._command_cache)
                return self._command_cache

        # 重新构建索引
        self._command_cache, self._plugin_cache = self._build_index()
        logger.info(
            f"Indexed {len(self._plugin_cache)} plugins, {len(self._command_cache)} triggers"
        )

        # 保存到持久化缓存
        self._save_to_persistent_cache(self._command_cache, self._plugin_cache)

        return self._command_cache

    def get_plugin_summaries(
        self, allowed_plugins: set[str] | None = None
    ) -> dict[str, PluginCommandSummary]:
        """获取插件摘要"""
        self.get_all_commands()
        plugins = self._plugin_cache or {}
        summaries = {
            name: summary.clone()
            for name, summary in plugins.items()
            if allowed_plugins is None
            or name in allowed_plugins
            or name.startswith("_custom_group_")
        }
        for summary in summaries.values():
            summary.commands = [
                command for command in summary.commands if not command.is_alias_of
            ]
        return summaries

    def search_commands(
        self,
        keyword: str,
        limit: int = 5,
        allowed_plugins: set[str] | None = None,
    ) -> list[dict]:
        """搜索命令（使用智能搜索）

        支持分词、多维度匹配和相关性排序：
        - 命令名匹配（权重最高）
        - 插件名匹配
        - 别名匹配
        - 描述匹配
        - 示例匹配
        """
        keyword_stripped = keyword.strip()
        if not keyword_stripped:
            return []

        all_commands = self.get_all_commands()

        # 过滤掉禁用插件的命令
        all_commands = {
            k: v for k, v in all_commands.items() if not v.get("inactive", False)
        }

        # 过滤允许的插件
        if allowed_plugins is not None:
            all_commands = {
                k: v
                for k, v in all_commands.items()
                if self._is_plugin_allowed(v, allowed_plugins)
            }

        # 使用智能搜索
        from .keyword_search import get_keyword_searcher

        searcher = get_keyword_searcher()
        results = searcher.search_intelligent(
            commands=all_commands,
            query=keyword_stripped,
            limit=limit,
            min_score=30,  # 最低得分阈值
        )

        # 移除临时的 relevance_score 字段
        for result in results:
            result.pop("relevance_score", None)

        logger.debug(f"Search for '{keyword}' returned {len(results)} results")
        logger.debug(
            f"Search results plugins: {[r.get('plugin', 'unknown') for r in results]}"
        )
        logger.debug(
            f"Search results commands: {[r.get('command', 'unknown') for r in results]}"
        )
        return results

    def get_command_detail(
        self, command_name: str, allowed_plugins: set[str] | None = None
    ) -> dict | None:
        """获取命令详情"""
        normalized = command_name.strip()
        if not normalized:
            return None
        if not normalized.startswith("/") and not normalized.startswith("regex:"):
            normalized = "/" + normalized
        detail = self.get_all_commands().get(normalized)
        if detail and self._is_plugin_allowed(detail, allowed_plugins):
            return detail
        if normalized.startswith("/"):
            return self._find_by_pattern_or_alias(normalized[1:], allowed_plugins)
        return self._find_by_pattern_or_alias(normalized, allowed_plugins)

    def get_related_commands(
        self,
        command_name: str,
        limit: int = 3,
        allowed_plugins: set[str] | None = None,
    ) -> list[str]:
        """获取相关命令"""
        target = self.get_command_detail(command_name, allowed_plugins=allowed_plugins)
        if not target:
            return []

        related: list[str] = []
        for name, cmd in self.get_all_commands().items():
            if name == target["command"]:
                continue
            if not self._is_plugin_allowed(cmd, allowed_plugins):
                continue
            if "is_alias_of" in cmd:
                continue
            if cmd["plugin"] == target["plugin"]:
                related.append(cmd["command"])
            if len(related) >= limit:
                break
        return related

    def list_plugin_commands(
        self,
        plugin_name: str = "",
        allowed_plugins: set[str] | None = None,
    ) -> tuple[list[str] | PluginCommandSummary | None, bool]:
        """列出插件命令"""
        plugins = self.get_plugin_summaries(allowed_plugins=allowed_plugins)
        if not plugin_name:
            names = sorted(summary.display_name for summary in plugins.values())
            return names, True

        keyword = plugin_name.lower().strip()
        for summary in plugins.values():
            if (
                keyword in summary.plugin.lower()
                or keyword in summary.display_name.lower()
            ):
                return summary, False
        return None, False

    def _find_by_pattern_or_alias(
        self, keyword: str, allowed_plugins: set[str] | None = None
    ) -> dict | None:
        """通过模式或别名查找"""
        keyword_lower = keyword.lower().strip()
        for trigger in self.get_all_commands().values():
            if not self._is_plugin_allowed(trigger, allowed_plugins):
                continue
            pattern = trigger.get("pattern", "")
            if pattern:
                try:
                    if re.search(pattern, keyword_lower, re.IGNORECASE):
                        return trigger
                except re.error:
                    if keyword_lower == pattern.lower():
                        return trigger
            if any(
                keyword_lower == alias.lower().lstrip("/")
                for alias in trigger.get("aliases", [])
            ):
                return trigger
        return None

    def _is_plugin_allowed(
        self, trigger_info: dict, allowed_plugins: set[str] | None
    ) -> bool:
        """检查插件是否允许"""
        plugin_name = trigger_info.get("plugin", "")

        # 允许自定义组的命令通过（无论 allowed_plugins 是什么）
        if plugin_name and plugin_name.startswith("_custom_group_"):
            logger.debug(f"Allowing custom group plugin: {plugin_name}")
            return True

        # 检查命令是否属于自定义组（通过 custom_groups 字段）
        custom_groups = trigger_info.get("custom_groups", [])
        if custom_groups:
            # 如果命令属于任何自定义组，则允许通过
            logger.debug(
                f"Allowing command with custom_groups: {plugin_name}, groups: {custom_groups}"
            )
            return True

        if allowed_plugins is None:
            return True

        is_allowed = plugin_name in allowed_plugins
        if not is_allowed:
            logger.debug(
                f"Filtering out plugin {plugin_name}, not in allowed_plugins: {allowed_plugins}"
            )
        return is_allowed

    def _build_handler_index(self) -> dict[str, list[StarHandlerMetadata]]:
        """构建处理器索引"""
        handler_index: dict[str, list[StarHandlerMetadata]] = collections.defaultdict(
            list
        )
        for handler in star_handlers_registry:
            if isinstance(handler, StarHandlerMetadata) and handler.handler_module_path:
                handler_index[handler.handler_module_path].append(handler)
        return handler_index

    def _should_refresh_cache(self) -> bool:
        """检查是否需要刷新缓存"""
        try:
            context = get_context()
            all_stars = context.get_all_stars()
            # 只检查已激活的stars数量变化
            activated_count = len(
                [star for star in all_stars if getattr(star, "activated", False)]
            )
            # 如果缓存为空，或者激活插件数量变化，都需要刷新
            if self._command_cache is None or len(self._command_cache) == 0:
                logger.debug("Cache is empty, need to rebuild")
                return True
            if activated_count != self._last_star_count:
                logger.debug(
                    f"Activated star count changed from {self._last_star_count} to {activated_count}, need to rebuild"
                )
                self._last_star_count = activated_count
                self._handler_index = None
                return True
            # 检查自定义分组是否变化
            current_sig = ",".join(
                sorted(g.group_name for g in self._custom_groups if not g.hidden)
            )
            if current_sig != self._last_custom_groups_signature:
                logger.debug(
                    f"Custom groups changed (signature: {self._last_custom_groups_signature} -> {current_sig}), need to rebuild"
                )
                self._last_custom_groups_signature = current_sig
                return True
            return False
        except Exception as exc:
            logger.error(f"Failed to check command index cache: {exc}")
            return True

    def _build_index(self) -> tuple[dict[str, dict], dict[str, PluginCommandSummary]]:
        """构建命令索引"""
        commands_dict: dict[str, dict] = {}
        plugin_dict: dict[str, PluginCommandSummary] = {}
        config = get_config()

        try:
            context = get_context()
            # 获取所有 stars，包括禁用的，以便在索引中保留位置
            all_stars = list(context.get_all_stars())
            activated_stars = [
                star for star in all_stars if getattr(star, "activated", False)
            ]
            logger.debug(
                f"Found {len(all_stars)} total stars, {len(activated_stars)} activated, building index..."
            )
        except Exception as exc:
            logger.error(f"Failed to get plugin list: {exc}")
            return {}, {}

        if self._handler_index is None:
            self._handler_index = self._build_handler_index()
        handler_index = self._handler_index

        for star in all_stars:
            plugin_name = getattr(star, "name", "未知插件")
            if plugin_name in config.ignored_plugins:
                continue

            module_path = getattr(star, "module_path", None)
            if not module_path:
                continue

            # 检查插件是否已激活
            is_activated = getattr(star, "activated", False)
            if not is_activated:
                # 禁用的插件仍然索引，但标记为未激活
                logger.debug(
                    f"Plugin '{plugin_name}' is disabled, indexing but marking as inactive"
                )

            summary = PluginCommandSummary(
                plugin=plugin_name,
                plugin_display_name=getattr(star, "display_name", None),
                plugin_version=str(getattr(star, "version", "") or ""),
                plugin_desc=self._sanitize_display_desc(getattr(star, "desc", "")),
                commands=[],
            )

            handlers = handler_index.get(module_path, [])
            for handler in handlers:
                entry = self._extract_command(handler, summary)
                if not entry:
                    continue

                # 标记禁用插件的命令
                if not is_activated:
                    entry.inactive = True

                summary.commands.append(entry)
                commands_dict[entry.command] = entry.to_dict()

                for alias in entry.aliases:
                    alias_entry = CommandEntry(
                        command=alias,
                        description=entry.description,
                        plugin=entry.plugin,
                        plugin_display_name=entry.plugin_display_name,
                        plugin_version=entry.plugin_version,
                        aliases=[],  # 别名条目不再递归携带别名列表
                        is_alias_of=entry.command,
                        group_name=entry.group_name,
                        tag=entry.tag,
                        type=entry.type,
                        pattern=entry.pattern,
                        show_pattern=entry.show_pattern,
                        examples=list(entry.examples),
                        usage_hint=entry.usage_hint,
                        handler_name=entry.handler_name,
                        custom_groups=list(entry.custom_groups),
                        priority=entry.priority,
                    )
                    commands_dict[alias] = alias_entry.to_dict()

            if summary.commands:
                summary.commands.sort(
                    key=lambda item: (
                        # 排序：普通命令(0) -> 正则命令(1) -> 命令组(2)
                        (0 if item.type == "command" else 1 if item.type == "regex" else 2),
                        item.command,
                    )
                )
                plugin_dict[plugin_name] = summary

        self._apply_custom_groups(plugin_dict, commands_dict)
        self._last_star_count = len(activated_stars)

        logger.info(
            f"Index built: {len(plugin_dict)} plugins, {len(commands_dict)} triggers from {len(activated_stars)} activated stars (total: {len(all_stars)})"
        )
        return commands_dict, plugin_dict

    def _apply_custom_groups(
        self,
        plugin_dict: dict[str, PluginCommandSummary],
        commands_dict: dict[str, dict],
    ):
        """应用自定义分组 - 从配置直接创建虚拟插件和命令"""
        logger.debug(
            f"_apply_custom_groups called with {len(self._custom_groups)} groups"
        )
        if not self._custom_groups:
            logger.debug("No custom groups to apply, returning")
            return

        visible_groups = sorted(
            [g for g in self._custom_groups if not g.hidden],
            key=lambda g: (g.priority, g.group_name),
        )

        if not visible_groups:
            logger.debug("No visible custom groups after filtering hidden ones")
            return

        logger.info(f"Applying {len(visible_groups)} visible custom groups")

        # 处理每个自定义分组 - 直接从配置创建命令
        for group in visible_groups:
            group_commands: list[CommandEntry] = []

            for cmd_config in group.commands:
                if cmd_config.hidden:
                    continue

                # 构建命令名称
                cmd_name = cmd_config.command
                cmd_type = cmd_config.type

                # 正则命令：使用 regex:pattern 作为命令名
                if cmd_type == "regex" and cmd_config.pattern:
                    cmd_name = f"regex:{cmd_config.pattern}"
                elif not cmd_name or not cmd_name.strip():
                    # 空命令名称，尝试使用别名
                    if cmd_config.aliases and cmd_config.aliases[0]:
                        cmd_name = cmd_config.aliases[0]
                        logger.warning(
                            f"Custom group '{group.group_name}' has empty command field, using first alias '{cmd_name}' instead"
                        )
                    else:
                        logger.warning(
                            f"Custom group '{group.group_name}' has empty command with no aliases, skipping"
                        )
                        continue

                if not cmd_name.startswith("/") and cmd_type == "command":
                    cmd_name = f"/{cmd_name}"

                # 确定标签
                if cmd_config.is_admin:
                    tag = "admin"
                elif cmd_config.type == "regex":
                    tag = "regex_pattern"
                else:
                    tag = "normal"

                # 正则命令自动生成示例和用法提示（优先保留用户配置的示例）
                examples: list[str] = list(cmd_config.examples) if cmd_config.examples else []
                usage_hint = ""
                if cmd_config.type == "regex" and cmd_config.pattern:
                    if not examples:
                        auto_examples, _ = generate_regex_examples(
                            cmd_config.pattern, self._regex_example_limit
                        )
                        if auto_examples:
                            examples = auto_examples
                    usage_hint = build_regex_usage_hint(cmd_config.pattern, examples)

                # aliases 供AI读取（识别不同叫法），不在菜单中显示
                aliases = list(cmd_config.aliases) if cmd_config.aliases else []

                # 创建命令条目 - 不设置 group_name，自定义命令作为独立命令平铺显示
                entry = CommandEntry(
                    command=cmd_name,
                    description="",
                    plugin=f"_custom_group_{group.group_name}",
                    plugin_display_name=group.group_name,
                    plugin_version="",
                    aliases=aliases,
                    is_alias_of=None,
                    group_name=None,
                    tag=tag,
                    type=cmd_config.type,
                    pattern=cmd_config.pattern if cmd_config.type == "regex" else "",
                    show_pattern=True,
                    examples=examples,
                    sub_commands=[],
                    usage_hint=usage_hint,
                    handler_name="",
                    custom_groups=[group.group_name],
                    priority=group.priority,
                )
                group_commands.append(entry)
                commands_dict[cmd_name] = entry.to_dict()
                logger.debug(
                    f"Custom group '{group.group_name}' added command '{cmd_name}' to commands_dict with custom_groups={entry.custom_groups}"
                )

            logger.info(
                f"Custom group '{group.group_name}' created {len(group_commands)} commands: {[e.command for e in group_commands]}"
            )

            if not group_commands:
                continue

            # 检查是否已存在同名插件（不是原生分组，而是真实插件）
            matching_plugin = None

            for plugin_name, summary in plugin_dict.items():
                # 只匹配非虚拟插件（不以_开头）
                if not plugin_name.startswith("_"):
                    if (
                        plugin_name == group.group_name
                        or summary.display_name == group.group_name
                    ):
                        matching_plugin = summary
                        break

            if matching_plugin:
                # 合并到同名插件
                matching_plugin.commands.extend(group_commands)
                logger.info(
                    f"Merged custom group '{group.group_name}' into existing plugin '{matching_plugin.plugin}'"
                )
            else:
                # 创建虚拟插件，显示在帮助图片中
                custom_summary = PluginCommandSummary(
                    plugin=f"_custom_group_{group.group_name}",
                    plugin_display_name=group.group_name,
                    plugin_version="",
                    plugin_desc=group.description or "自定义命令组",
                    commands=group_commands,
                )
                plugin_dict[f"_custom_group_{group.group_name}"] = custom_summary
                logger.info(
                    f"Created virtual plugin '_custom_group_{group.group_name}' with {len(group_commands)} commands"
                )

        logger.info(
            f"Custom groups applied. Plugin dict now has {len(plugin_dict)} plugins: {list(plugin_dict.keys())}"
        )
        logger.info(f"Commands dict now has {len(commands_dict)} commands total")

        # 验证自定义命令是否真的在 commands_dict 中
        custom_cmds = [k for k, v in commands_dict.items() if v.get("custom_groups")]
        logger.info(
            f"Found {len(custom_cmds)} commands with custom_groups field: {custom_cmds}"
        )

        for summary in plugin_dict.values():
            summary.commands.sort(
                key=lambda item: (
                    # 排序：普通命令(0) -> 正则命令(1) -> 命令组(2)
                    (0 if item.type == "command" else 1 if item.type == "regex" else 2),
                    item.command,
                )
            )

    def _extract_command(
        self, handler: StarHandlerMetadata, summary: PluginCommandSummary
    ) -> CommandEntry | None:
        """从处理器提取命令"""
        command_name: str | None = None
        aliases: list[str] = []
        group_name: str | None = None
        type_ = "command"
        pattern = ""
        examples: list[str] = []
        usage_hint = ""
        show_pattern = True

        event_filters = getattr(handler, "event_filters", []) or []

        group_alias: list[str] = []
        for filter_ in event_filters:
            if isinstance(filter_, CommandGroupFilter):
                group_name = filter_.group_name
                raw_alias = getattr(filter_, "alias", None)
                if isinstance(raw_alias, set):
                    group_alias = sorted(raw_alias)
                elif isinstance(raw_alias, list):
                    group_alias = list(raw_alias)
                elif isinstance(raw_alias, tuple):
                    group_alias = list(raw_alias)
                break

        for filter_ in event_filters:
            if isinstance(filter_, CommandFilter) and not isinstance(
                filter_, CommandGroupFilter
            ):
                command_name = filter_.command_name
                raw_alias = getattr(filter_, "alias", None)
                if isinstance(raw_alias, set):
                    aliases = sorted(raw_alias)
                elif isinstance(raw_alias, list):
                    aliases = list(raw_alias)
                elif isinstance(raw_alias, tuple):
                    aliases = list(raw_alias)

                if not group_name:
                    parent_names = getattr(filter_, "parent_command_names", None)
                    if parent_names and parent_names != [""]:
                        group_name = parent_names[0]
                break
            if isinstance(filter_, RegexFilter):
                pattern = filter_.regex_str
                command_name = f"regex:{pattern}"
                type_ = "regex"
                examples, show_pattern = generate_regex_examples(
                    pattern, self._regex_example_limit
                )
                usage_hint = build_regex_usage_hint(pattern, examples)
                break

        if not command_name:
            if group_name:
                tag = "admin" if self._has_admin_permission(handler) else "normal"
                normalized_group_alias = [
                    a if a.startswith("/") else "/" + a for a in group_alias
                ]
                return CommandEntry(
                    command=f"/{group_name}",
                    description=self._sanitize_handler_desc(handler) or group_name,
                    plugin=summary.plugin,
                    plugin_display_name=summary.plugin_display_name,
                    plugin_version=summary.plugin_version,
                    aliases=normalized_group_alias,
                    group_name=group_name,
                    tag=tag,
                    type="group",
                    handler_name=handler.handler_name,
                )
            return None

        if type_ == "command" and not command_name.startswith("/"):
            command_name = "/" + command_name

        normalized_aliases = []
        for alias in aliases:
            normalized_aliases.append(alias if alias.startswith("/") else "/" + alias)

        desc = self._sanitize_handler_desc(handler) or "无描述"
        tag = "admin" if self._has_admin_permission(handler) else "normal"
        if type_ == "regex":
            tag = "regex_pattern"

        return CommandEntry(
            command=command_name,
            description=desc,
            plugin=summary.plugin,
            plugin_display_name=summary.plugin_display_name,
            plugin_version=summary.plugin_version,
            aliases=normalized_aliases,
            group_name=group_name,
            tag=tag,
            type=type_,
            pattern=pattern,
            show_pattern=show_pattern,
            examples=examples,
            usage_hint=usage_hint,
            handler_name=handler.handler_name,
        )

    def _has_admin_permission(self, handler: StarHandlerMetadata) -> bool:
        """检查是否有管理员权限"""
        for filter_ in getattr(handler, "event_filters", []) or []:
            if isinstance(filter_, PermissionTypeFilter):
                return True
        return False


def invalidate_command_cache():
    """使命令缓存失效。

    当自定义命令组发生变化时调用，强制下次重新构建索引。
    """
    global _command_index_instance
    if _command_index_instance is not None:
        _command_index_instance._clear_persistent_cache()
        _command_index_instance.reset_cache()
        logger.info("Command cache invalidated due to custom groups change")


# 单例实例
_command_index_instance: CommandIndex | None = None


def get_command_index() -> CommandIndex:
    """获取命令索引器单例。

    Returns:
        CommandIndex 实例
    """
    global _command_index_instance
    if _command_index_instance is None:
        _command_index_instance = CommandIndex()
    return _command_index_instance


def reset_command_index() -> None:
    """重置命令索引器（用于测试）。"""
    global _command_index_instance
    _command_index_instance = None
