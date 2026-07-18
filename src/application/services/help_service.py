"""Main Help Service

Coordinates all infrastructure to complete business use cases.
"""

from __future__ import annotations

import json
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot.core import sp

from ...infrastructure import (
    get_command_executor,
    get_command_index,
    get_context,
    get_logger,
    get_data_dir,
    init_plugin_paths,
    replace_prefix,
    reset_command_executor,
    reset_command_index,
    set_context,
)
from ...infrastructure.config import (
    CustomGroupConfig,
    get_config,
    init_config,
    refresh_config,
)
from .custom_group_service import bind_custom_group_catalog, reset_custom_group_service
from .command_runtime_service import CommandRuntimeService
from .delegated_command_service import DelegatedCommandService
from ..dto import (
    CommandDetailResponse,
    ListCustomGroupsResponse,
    SearchCommandResponse,
)

logger = get_logger()


class HelpService:
    """Help Plugin Main Service"""

    def __init__(self):
        self.config = get_config()
        self.context = get_context()
        self.command_index = get_command_index()
        self.command_executor = get_command_executor()
        self.command_runtime = CommandRuntimeService(
            data_dir=get_data_dir(),
            config=self.config,
            context=self.context,
            command_index=self.command_index,
            command_executor=self.command_executor,
        )
        self.delegated_command_service = DelegatedCommandService(
            runtime=self.command_runtime,
            command_executor=self.command_executor,
            command_index=self.command_index,
            config_getter=lambda: self.config,
            prefixes_getter=lambda: self.prefixes,
            resolve_target=self._resolve_target,
            resolve_allowed_plugins=self._resolve_allowed_plugins,
            is_command_invokable=self._is_command_invokable,
        )

        self.prefixes: list[str] = ["/"]

    async def initialize(self):
        """Initialize service"""
        self.command_runtime.initialize()
        bind_custom_group_catalog(self.command_runtime.catalog)
        self.config.custom_groups = [
            CustomGroupConfig.model_validate(group)
            for group in self.command_runtime.catalog.list_custom_groups()
        ]
        self.command_index.update_config()
        self._init_prefixes()

        logger.info("Initialization completed")

    async def terminate(self):
        """Terminate service"""
        await self.command_executor.shutdown()
        self.command_runtime.terminate()

    def _init_prefixes(self):
        """Initialize command prefixes"""
        try:
            global_config = self.context.get_config()
            raw = global_config.get("wake_prefix", ["/"])
            self.prefixes = [raw] if isinstance(raw, str) else list(raw)
        except Exception as exc:
            logger.warning(f"Failed to get wake prefix, using default '/': {exc}")
            self.prefixes = ["/"]
        # Sync update CommandExecutor and CommandIndex prefixes
        self.command_executor.update_prefixes(self.prefixes)
        self.command_index.prefixes = self.prefixes

    def sync_config(self, raw_config: AstrBotConfig | None = None):
        """Sync config state"""
        if raw_config is not None:
            refresh_config(raw_config)
            # 删除确认 token 仅绑定当前配置快照；重载后必须失效。
            reset_custom_group_service()
        self.config = get_config()
        self.command_index.update_config()
        self.command_executor.cfg = get_config()
        if hasattr(self, "command_runtime"):
            self.command_runtime.reconfigure(self.config)

    async def _get_session_disabled_plugins(self, event: AstrMessageEvent) -> set[str]:
        """Get session disabled plugins"""
        session_id = getattr(event, "unified_msg_origin", "") or getattr(
            event, "session_id", ""
        )
        if not session_id:
            return set()

        session_plugin_config = await sp.get_async(
            scope="umo",
            scope_id=session_id,
            key="session_plugin_config",
            default={},
        )
        session_config = session_plugin_config.get(session_id, {})
        return {
            str(name)
            for name in session_config.get("disabled_plugins", [])
            if str(name).strip()
        }

    async def _resolve_allowed_plugins(
        self, event: AstrMessageEvent
    ) -> set[str] | None:
        """Resolve allowed plugins"""
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin)
        except Exception as exc:
            logger.warning(
                f"Failed to get current config, using full plugin view: {exc}"
            )
            return None

        plugin_set = cfg.get("plugin_set", ["*"])
        if plugin_set == ["*"]:
            allowed_plugins = None
        elif isinstance(plugin_set, str):
            allowed_plugins = {plugin_set}
        else:
            allowed_plugins = {str(name) for name in plugin_set if str(name).strip()}

        try:
            disabled_plugins = await self._get_session_disabled_plugins(event)
        except Exception as exc:
            logger.warning(
                f"Failed to get session disabled plugins, ignoring session filter: {exc}"
            )
            return allowed_plugins

        if not disabled_plugins:
            return allowed_plugins
        if allowed_plugins is None:
            return None
        return allowed_plugins - disabled_plugins

    def _is_command_invokable(self, plugin_name: str) -> bool:
        """Check if command can be invoked by AI"""
        if not plugin_name:
            return True

        return not any(
            plugin_name.startswith(bl) or plugin_name == bl
            for bl in self.config.ai_command_blacklist
        )

    def _format_search_results_by_plugin(self, results: list[dict]) -> dict:
        """Format search results grouped by plugin"""
        primary_results = [item for item in results if not item.get("is_alias_of")]

        matched_items = []
        matched_group_keys = set()

        for item in primary_results:
            matched_items.append(item)
            group_name = item.get("group_name")
            plugin_name = item.get("plugin", "Unknown Plugin")

            if group_name and item.get("type") != "group":
                matched_group_keys.add((plugin_name, group_name))

        groups_to_include = {}
        for item in primary_results:
            plugin_name = item.get("plugin", "Unknown Plugin")
            cmd_type = item.get("type", "command")
            group_name = item.get("group_name")

            if cmd_type == "group" and group_name:
                key = (plugin_name, group_name)
                if key in matched_group_keys and key not in groups_to_include:
                    groups_to_include[key] = item

        final_items = []
        for key, group_item in groups_to_include.items():
            final_items.append(group_item)

        for item in matched_items:
            group_name = item.get("group_name")
            cmd_type = item.get("type", "command")

            # 包含自定义命令组的命令，即使它们没有 group_name
            is_custom_group_cmd = bool(item.get("custom_groups"))

            # 调试日志
            if is_custom_group_cmd:
                logger.debug(
                    f"Found custom group command: {item.get('command')}, custom_groups: {item.get('custom_groups')}"
                )

            if is_custom_group_cmd or (cmd_type != "group" and not group_name):
                final_items.append(item)

        logger.debug(
            f"Final items count: {len(final_items)}, commands: {[item.get('command') for item in final_items]}"
        )

        plugins_dict = {}
        for item in final_items:
            plugin_name = item.get("plugin", "Unknown Plugin")
            if plugin_name not in plugins_dict:
                plugins_dict[plugin_name] = {
                    "plugin": plugin_name,
                    "plugin_display_name": item.get("plugin_display_name"),
                    "plugin_version": item.get("plugin_version", ""),
                    "author": "",
                    "commands": [],
                }

            cmd_type = item.get("type", "command")
            group_name = item.get("group_name")

            if cmd_type == "group" and group_name:
                sub_commands = self._find_sub_commands(
                    primary_results, group_name, plugin_name
                )
                command_info = {
                    "command": item["command"].lstrip("/"),
                    "description": item.get("description", "No description"),
                    "type": "group",
                    "tag": item.get("tag", "normal"),
                    "invokable": self._is_command_invokable(item.get("plugin", "")),
                    "sub_commands": sub_commands,
                }
            else:
                command_info = {
                    "command": item["command"].lstrip("/"),
                    "description": item.get("description", "No description"),
                    "aliases": [alias.lstrip("/") for alias in item.get("aliases", [])],
                    "type": item.get("type", "command"),
                    "tag": item.get("tag", "normal"),
                    "invokable": self._is_command_invokable(item.get("plugin", "")),
                }
                if item.get("pattern"):
                    command_info["pattern"] = item["pattern"]
                if item.get("examples"):
                    command_info["examples"] = list(item["examples"])[:3]
                if item.get("usage_hint"):
                    command_info["usage_hint"] = item["usage_hint"]
                # 标记这是自定义命令组的命令
                if item.get("custom_groups"):
                    command_info["custom_groups"] = item.get("custom_groups")

            plugins_dict[plugin_name]["commands"].append(command_info)

        plugin_list = []
        for plugin_data in plugins_dict.values():
            if plugin_data["commands"]:
                plugin_data_clean = {k: v for k, v in plugin_data.items() if v}
                plugin_list.append(plugin_data_clean)

        plugin_list.sort(key=lambda x: x["plugin"])

        return {
            "plugin_count": len(plugin_list),
            "command_count": sum(len(p["commands"]) for p in plugin_list),
            "plugins": plugin_list,
        }

    def _find_sub_commands(
        self, results: list[dict], group_name: str, plugin_name: str
    ) -> list[dict]:
        """Find sub-commands for a command group"""
        sub_commands = []
        for item in results:
            if (
                item.get("group_name") == group_name
                and item.get("plugin") == plugin_name
                and item.get("type") != "group"
                and not item.get("is_alias_of")
            ):
                sub_cmd = {
                    "command": item["command"].lstrip("/"),
                    "description": item.get("description", "No description"),
                    "aliases": [alias.lstrip("/") for alias in item.get("aliases", [])],
                    "type": item.get("type", "command"),
                    "tag": item.get("tag", "normal"),
                    "invokable": self._is_command_invokable(item.get("plugin", "")),
                }
                if item.get("pattern"):
                    sub_cmd["pattern"] = item["pattern"]
                if item.get("examples"):
                    sub_cmd["examples"] = list(item["examples"])[:3]
                if item.get("usage_hint"):
                    sub_cmd["usage_hint"] = item["usage_hint"]
                sub_commands.append(sub_cmd)

        sub_commands.sort(key=lambda x: x["command"])
        return sub_commands

    def _format_command_detail_result(self, item: dict) -> dict:
        """Format single command detail"""
        # Use first prefix from platform wake prefix config
        display_prefix = self.prefixes[0] if self.prefixes else "/"
        result = {
            "command": replace_prefix(item["command"], display_prefix),
            "description": item["description"],
            "plugin": item["plugin"],
            "aliases": [
                replace_prefix(alias, display_prefix)
                for alias in item.get("aliases", [])
            ],
            "type": item.get("type", "command"),
            "invokable": self._is_command_invokable(item.get("plugin", "")),
        }
        if item.get("plugin_display_name"):
            result["plugin_display_name"] = item["plugin_display_name"]
        if item.get("plugin_version"):
            result["plugin_version"] = item["plugin_version"]
        if item.get("group_name"):
            result["group_name"] = item["group_name"]
        if item.get("tag"):
            result["tag"] = item["tag"]
        if item.get("is_alias_of"):
            result["is_alias_of"] = replace_prefix(item["is_alias_of"], display_prefix)
        if item.get("pattern") and item.get("show_pattern", True):
            result["pattern"] = item["pattern"]
        if item.get("examples"):
            result["examples"] = list(item.get("examples", []))
        if item.get("usage_hint"):
            result["usage_hint"] = item["usage_hint"]
        if item.get("custom_groups"):
            result["custom_groups"] = list(item.get("custom_groups", []))
        return result

    def _find_matching_custom_groups(
        self, keyword: str, *, is_admin: bool
    ) -> list[CustomGroupConfig]:
        """按原请求者权限返回可安全用于 fallback/note 的目录副本。"""
        if not self.config.custom_groups:
            return []

        keyword_lower = keyword.lower().strip()
        matched = []

        for group in self.config.custom_groups:
            if group.hidden and not is_admin:
                continue
            visible_commands = [
                command
                for command in group.commands
                if is_admin
                or (not command.hidden and command.permission_level != "admin")
            ]
            public_group = group.model_copy(
                deep=True, update={"commands": visible_commands}
            )
            if keyword_lower in group.group_name.lower():
                matched.append(public_group)
                continue

            if visible_commands:
                for cmd in visible_commands:
                    if (
                        cmd.type == "command"
                        and keyword_lower in cmd.command.lower().lstrip("/")
                    ):
                        matched.append(public_group)
                        break
                    if (
                        cmd.type == "regex"
                        and cmd.pattern
                        and keyword_lower in cmd.pattern.lower()
                    ):
                        matched.append(public_group)
                        break

        return matched

    async def _resolve_target(
        self, event: AstrMessageEvent, target_user: str = ""
    ) -> tuple[dict[str, object], str | None]:
        """解析目标并仅在后端取回 UID；LLM 结果继续使用 opaque ref。"""
        requester_id = str(event.get_sender_id())
        if not target_user.strip():
            return (
                {
                    "status": "resolved",
                    "display_name": event.get_sender_name() or requester_id,
                    "source": "requester",
                    "identity_freshness": "event",
                    "operable": True,
                },
                requester_id,
            )
        resolution = await self.command_runtime.identity_service.resolve(
            event, target_user, requester_id=requester_id
        )
        if resolution.get("status") != "resolved":
            return resolution, None
        target_id = self.command_runtime.catalog.find_identity_reference(
            platform_id=str(event.get_platform_id()),
            session_id=str(event.unified_msg_origin),
            target_ref=str(resolution["target_ref"]),
        )
        if target_id is None:
            return {"status": "error", "error": "目标引用已失效"}, None
        return resolution, target_id

    async def resolve_user(self, event: AstrMessageEvent, reference: str) -> str:
        """解析自然语言、@、引用、UID 或个人别名。"""
        result = await self.command_runtime.identity_service.resolve(
            event, reference, requester_id=str(event.get_sender_id())
        )
        return json.dumps(result, ensure_ascii=False)

    async def set_user_alias(
        self, event: AstrMessageEvent, alias: str, target_user: str
    ) -> str:
        try:
            result = await self.command_runtime.identity_service.set_alias(
                event,
                requester_id=str(event.get_sender_id()),
                alias=alias,
                target_reference=target_user,
            )
            return json.dumps({"success": True, "target": result}, ensure_ascii=False)
        except Exception as error:
            return json.dumps(
                {"success": False, "error": str(error)}, ensure_ascii=False
            )

    def list_user_aliases(self, event: AstrMessageEvent) -> str:
        result = self.command_runtime.identity_service.list_aliases(
            event, requester_id=str(event.get_sender_id())
        )
        return json.dumps({"success": True, "aliases": result}, ensure_ascii=False)

    def delete_user_alias(self, event: AstrMessageEvent, alias: str) -> str:
        deleted = self.command_runtime.identity_service.delete_alias(
            event, requester_id=str(event.get_sender_id()), alias=alias
        )
        return json.dumps({"success": deleted, "deleted": deleted}, ensure_ascii=False)

    async def search_command(
        self,
        event: AstrMessageEvent,
        keyword: str,
        permission_filter: str = "auto",
        target_user: str = "",
        preference_mode: str = "auto",
    ) -> str:
        """Search for commands or get detailed command information.

        Smart behavior:
        - If keyword is empty: returns the current user's recent or frequent commands
        - If multiple matches: returns a list of matching commands grouped by plugin
        - If single exact match: returns detailed information for that command
        """
        is_admin = bool(event.is_admin())
        if permission_filter == "auto":
            permission_filter = "all" if is_admin else "normal"
        if permission_filter not in {"normal", "admin", "all"}:
            return json.dumps(
                {"success": False, "error": "invalid_permission_filter"},
                ensure_ascii=False,
            )
        if not is_admin and permission_filter != "normal":
            return json.dumps(
                {
                    "success": False,
                    "error": "permission_filter_exceeds_requester",
                    "message": "普通请求者只能搜索普通权限命令",
                },
                ensure_ascii=False,
            )
        if preference_mode not in {"auto", "recent", "frequent", "off"}:
            return json.dumps(
                {
                    "success": False,
                    "error": "preference_mode 仅支持 auto、recent、frequent、off",
                },
                ensure_ascii=False,
            )
        target, target_id = await self._resolve_target(event, target_user)
        if target_id is None:
            return json.dumps(
                {"success": False, "identity": target}, ensure_ascii=False
            )
        requester_id = str(event.get_sender_id())
        platform_id = str(event.get_platform_id())
        if not keyword or not keyword.strip():
            if target_id != requester_id and not is_admin:
                return json.dumps(
                    {"success": False, "error": "空关键词不能查询其他用户的偏好"},
                    ensure_ascii=False,
                )
            if preference_mode == "off":
                return json.dumps(
                    {
                        "success": False,
                        "error": "空关键词需要 recent、frequent 或 auto 偏好模式",
                    },
                    ensure_ascii=False,
                )
            if preference_mode == "frequent":
                items = self.command_runtime.history_service.list_frequent(
                    platform_id=platform_id,
                    target_user_id=target_id,
                    requester_user_id=requester_id,
                    is_admin=is_admin,
                )
            else:
                items = self.command_runtime.history_service.list_recent(
                    platform_id=platform_id,
                    target_user_id=target_id,
                    requester_user_id=requester_id,
                    is_admin=is_admin,
                )
            return json.dumps(
                {
                    "success": True,
                    "target": target,
                    "preference_mode": preference_mode,
                    "commands": items,
                },
                ensure_ascii=False,
            )

        try:
            allowed_plugins = await self._resolve_allowed_plugins(event)
            logger.debug(
                f"Searching for '{keyword}' with allowed_plugins: {allowed_plugins}"
            )
            results = self.command_index.search_commands(
                keyword,
                limit=10,
                allowed_plugins=allowed_plugins,
            )
            from ...infrastructure.analysis.keyword_search import get_keyword_searcher

            searcher = get_keyword_searcher()
            tokens = searcher.tokenize(keyword)
            for result in results:
                result["relevance_score"] = searcher.calculate_relevance_score(
                    result, tokens, keyword.strip()
                )
                result["exact"] = self._is_exact_match(keyword, result)
                result["permission_allowed"] = not (
                    result.get("tag") == "admin" and not is_admin
                )
            logger.debug(f"Search command returned {len(results)} results")
            for i, r in enumerate(results):
                logger.debug(
                    f"  Result {i + 1}: command={r.get('command')}, plugin={r.get('plugin')}, custom_groups={r.get('custom_groups')}, type={r.get('type')}"
                )

            # Filter by permission
            if permission_filter == "normal":
                results = [r for r in results if r.get("tag") != "admin"]
            elif permission_filter == "admin":
                results = [r for r in results if r.get("tag") == "admin"]

            results = self.command_runtime.history_service.apply_preference_boost(
                results,
                platform_id=platform_id,
                target_user_id=target_id,
                requester_user_id=requester_id,
                keyword=keyword,
                preference_mode=preference_mode,
                is_admin=is_admin,
            )
            for result in results:
                result.pop("relevance_score", None)
                result.pop("exact", None)
                result.pop("permission_allowed", None)

            logger.debug(
                f"After permission filter ({permission_filter}): {len(results)} results remaining"
            )

            matched_custom_groups = self._find_matching_custom_groups(
                keyword, is_admin=is_admin
            )

            if not results:
                if matched_custom_groups:
                    group = matched_custom_groups[0]
                    # Build command details
                    cmd_details = []
                    for cmd in group.commands:
                        if cmd.type == "command":
                            cmd_details.append(
                                f"  - {cmd.command} (type=command, is_admin={cmd.is_admin}, hidden={cmd.hidden})"
                            )
                        else:
                            cmd_details.append(
                                f"  - pattern={cmd.pattern} (type=regex, is_admin={cmd.is_admin}, hidden={cmd.hidden})"
                            )

                    error_msg = f"""No triggers or commands found matching '{keyword}'.

Found a custom group configuration '{group.group_name}' in WebUI with rules:
- commands:
{chr(10).join(cmd_details)}
- priority: {group.priority}
- hidden: {group.hidden}

This group currently matches no actual commands. Possible reasons:
1. Configured command names don't match actual commands
2. Configured command type differs from actual command type
3. The target plugin is not loaded

Suggestion: Check WebUI configuration or search with different keywords."""
                    return SearchCommandResponse.error_response(error_msg).to_json()
                return SearchCommandResponse.error_response(
                    f"No triggers or commands found matching '{keyword}'"
                ).to_json()

            # Smart response: if only one exact match, return detailed information
            if len(results) == 1 and self._is_exact_match(keyword, results[0]):
                return await self._format_command_detail(results[0], allowed_plugins)

            # Multiple results or fuzzy match: return list grouped by plugin
            formatted = self._format_search_results_by_plugin(results)

            message = f"Found {formatted['command_count']} trigger(s) or command(s) matching '{keyword}'"
            if matched_custom_groups:
                group_names = [g.group_name for g in matched_custom_groups]
                message += f"\n\n(Note: This keyword also matches custom group configurations: {', '.join(group_names)})"

            return SearchCommandResponse.success_response(
                message=message,
                command_count=formatted["command_count"],
                plugin_count=formatted["plugin_count"],
                command_prefix=self.prefixes,
                plugins=formatted["plugins"],
            ).to_json()
        except Exception as exc:
            logger.error(f"Command search failed: {exc}")
            return SearchCommandResponse.error_response(
                f"Search failed: {exc}"
            ).to_json()

    def _is_exact_match(self, keyword: str, result: dict) -> bool:
        """Check if keyword exactly matches the command"""
        keyword_normalized = keyword.strip().lower()
        command = result.get("command", "").lower()

        # Exact match (with or without prefix)
        if command == keyword_normalized:
            return True
        if command.endswith(keyword_normalized):
            return True
        if command.endswith(keyword_normalized):
            return True

        return False

    async def _format_command_detail(
        self, detail: dict, allowed_plugins: set[str] | None
    ) -> str:
        """Format single command as detailed response"""
        display_prefix = self.prefixes[0] if self.prefixes else "/"
        similar = [
            replace_prefix(item, display_prefix)
            for item in self.command_index.get_related_commands(
                detail["command"],
                allowed_plugins=allowed_plugins,
            )
        ]
        response = CommandDetailResponse.from_command_entry(detail, similar)
        return response.to_json()

    async def execute_command(
        self,
        event: AstrMessageEvent,
        command: str,
        actor: str = "user",
        result_mode: str = "auto",
        wait_seconds: float | None = None,
        target_user: str = "",
    ) -> str:
        """保持旧公开入口，委托给独立的跨用户命令应用服务。"""
        return await self.delegated_command_service.execute(
            event,
            command,
            actor=actor,
            result_mode=result_mode,
            wait_seconds=wait_seconds,
            target_user=target_user,
        )

    async def list_custom_groups(self) -> str:
        """List custom groups"""
        try:
            groups = []
            for group in self.config.custom_groups:
                command_list = []
                for cmd in group.commands:
                    if cmd.type == "regex":
                        command_list.append(f"regex:{cmd.pattern}")
                    else:
                        command_list.append(cmd.command)
                groups.append(
                    {
                        "group_name": group.group_name,
                        "description": group.description,
                        "commands": command_list,
                        "priority": group.priority,
                        "hidden": group.hidden,
                    }
                )

            return ListCustomGroupsResponse(
                success=True,
                group_count=len(groups),
                groups=groups,
            ).to_json()
        except Exception as exc:
            logger.error(f"Failed to list custom groups: {exc}")
            return ListCustomGroupsResponse(
                success=False,
                group_count=0,
                groups=[],
                error=str(exc),
            ).to_json()


# Service singleton instance
_help_service_instance: HelpService | None = None


def get_help_service() -> HelpService:
    """Get help service singleton.

    Returns:
        HelpService instance
    """
    global _help_service_instance
    if _help_service_instance is None:
        _help_service_instance = HelpService()
    return _help_service_instance


def reset_help_service() -> None:
    """Reset help service (for testing)."""
    global _help_service_instance
    if (
        _help_service_instance is not None
        and _help_service_instance.command_executor._background_tasks
    ):
        raise RuntimeError("HelpService 仍有后台命令任务；必须先 await terminate()")
    _help_service_instance = None


def init_plugin_service(
    context, config: AstrBotConfig, plugin_dir: Path
) -> HelpService:
    """Initialize plugin service (called in __init__).

    Args:
        context: AstrBot Context
        config: AstrBotConfig
        plugin_dir: Plugin directory

    Returns:
        HelpService instance
    """
    # 插件重复初始化必须彻底重绑 context/config；不得复用旧运行时单例。
    reset_help_service()
    reset_command_executor()
    reset_command_index()
    # Initialize global singletons - order matters!
    # Must init paths first because config loading needs data_dir
    set_context(context)
    init_plugin_paths(plugin_dir)  # Must be called before init_config
    init_config(config)
    # 插件重新初始化意味着新的配置快照，旧删除确认 token 不得跨边界复用。
    reset_custom_group_service()

    # Get help service instance (this creates CommandIndex)
    service = get_help_service()

    # IMPORTANT: Update command index config to load custom groups!
    # This must be called before any cache building to ensure custom_groups are loaded
    service.command_index.update_config()

    return get_help_service()
