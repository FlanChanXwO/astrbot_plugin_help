"""Main Help Service

Coordinates all infrastructure to complete business use cases.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image
from astrbot.core import sp

from ...infrastructure import (
    clear_cache_dir,
    get_cache_dir,
    get_cache_manager,
    get_command_analyzer,
    get_command_executor,
    get_command_index,
    get_context,
    get_event_analyzer,
    get_filter_analyzer,
    get_html_renderer,
    get_logger,
    init_plugin_paths,
    invalidate_command_cache,
    replace_prefix,
    set_context,
)
from ...infrastructure.config import get_config, init_config, refresh_config
from ..dto import (
    CommandDetailResponse,
    ListCustomGroupsResponse,
    ListPluginsResponse,
    SearchCommandResponse,
)

logger = get_logger()


class HelpService:
    """Help Plugin Main Service"""

    def __init__(self):
        self.config = get_config()
        self.context = get_context()
        self.command_analyzer = get_command_analyzer()
        self.event_analyzer = get_event_analyzer()
        self.filter_analyzer = get_filter_analyzer()
        self.renderer = get_html_renderer()
        self.cache = get_cache_manager()
        self.command_index = get_command_index()
        self.command_executor = get_command_executor()

        self.prefixes: list[str] = ["/"]
        self.cache_dir = get_cache_dir()

        # Internal state
        self._last_error: str | None = None

    async def initialize(self):
        """Initialize service"""
        self._init_prefixes()
        logger.info("Initialization completed")

    async def terminate(self):
        """Terminate service"""
        try:
            await self.renderer.close()
        except Exception as exc:
            logger.warning(f"Failed to close HTML renderer: {exc}")

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
        if raw_config:
            refresh_config(raw_config)
        self.config = get_config()
        self.command_index.update_config()
        self.command_executor._cfg = get_config()
        self.renderer.set_theme(self.config.rendering.html_theme)

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

    def _get_cache_key(self, mode: str, query: str | None, is_admin: bool) -> str:
        """Generate cache key"""
        try:
            all_stars = self.context.get_all_stars()
            plugin_names = sorted(
                [
                    getattr(star, "name", "")
                    for star in all_stars
                    if getattr(star, "activated", False)
                ]
            )
        except Exception:
            plugin_names = []

        cache_data = {
            "plugins": plugin_names,
            "mode": mode,
            "query": query,
            "is_admin": is_admin,
            "html_theme": self.config.rendering.html_theme,
            "use_t2i": self.config.rendering.use_t2i,
            "custom_groups": sorted(
                g.group_name for g in self.config.custom_groups
            ),
        }

        cache_str = json.dumps(cache_data, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(cache_str.encode()).hexdigest()

    async def _cleanup_temp_files(self):
        """Clean up temporary files"""
        try:
            temp_files = list(self.cache_dir.glob("temp_*"))
            if not temp_files:
                return
            for file_path in temp_files:
                try:
                    if file_path.exists():
                        file_path.unlink()
                except OSError:
                    pass
        except Exception as exc:
            logger.warning(f"Cleanup failed: {exc}")

    async def _render_with_html(
        self,
        analyzer,
        title: str,
        query: str | None,
        allowed_plugins: set[str] | None,
        cache_key: str | None,
    ) -> str | None:
        """Render help image using HTML renderer"""
        try:
            # Get plugin data
            plugins = analyzer.get_plugins(query, allowed_plugins=allowed_plugins)
            if not plugins:
                self._last_error = "empty"
                return None

            # Prepare output path
            display_title = f'Search results: "{query}"' if query else title
            output_filename = (
                f"help_{cache_key}.jpg"
                if cache_key
                else f"help_search_{hash(query)}.jpg"
            )
            output_path = self.cache_dir / output_filename

            # Render
            image_paths = await self.renderer.render(
                plugins=[p.to_dict() for p in plugins],
                output_path=output_path,
                title=display_title,
                prefixes=self.prefixes,
            )

            if image_paths and image_paths[0]:
                if cache_key:
                    await self.cache.set_cached_image(cache_key, image_paths[0])
                return image_paths[0]

            self._last_error = "Rendering did not generate image"
            return None

        except Exception as exc:
            logger.error(f"HTML rendering failed: {exc}", exc_info=True)
            self._last_error = f"HTML rendering failed: {exc}"
            return None

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

    def _find_matching_custom_groups(self, keyword: str) -> list:
        """Find matching custom groups"""
        if not self.config.custom_groups:
            return []

        keyword_lower = keyword.lower().strip()
        matched = []

        for group in self.config.custom_groups:
            if keyword_lower in group.group_name.lower():
                matched.append(group)
                continue

            if group.commands:
                for cmd in group.commands:
                    if (
                        cmd.type == "command"
                        and keyword_lower in cmd.command.lower().lstrip("/")
                    ):
                        matched.append(group)
                        break
                    if (
                        cmd.type == "regex"
                        and cmd.pattern
                        and keyword_lower in cmd.pattern.lower()
                    ):
                        matched.append(group)
                        break

        return matched

    async def show_help(
        self, event: AstrMessageEvent, query: str = "", is_admin: bool = False
    ):
        """Display help menu"""

        allowed_plugins = await self._resolve_allowed_plugins(event)
        cache_key = self._get_cache_key("command", query, is_admin)

        # Check cache
        if not query:
            cached_image = await self.cache.get_cached_image(cache_key)
            if cached_image:
                yield event.chain_result([Image.fromFileSystem(cached_image)])
                return

        # HTML render
        result = await self._render_with_html(
            analyzer=self.command_analyzer,
            title="Astrbot 指令帮助",
            query=query,
            allowed_plugins=allowed_plugins,
            cache_key=cache_key if not query else None,
        )

        if result:
            yield event.chain_result([Image.fromFileSystem(result)])
        elif result is None and not query:
            yield event.plain_result("Rendering failed, please try again later")
        elif self._last_error:
            yield event.plain_result(self._last_error)
        else:
            yield event.plain_result(f"No commands found matching '{query}'")

    async def refresh_cache(self) -> str:
        """Refresh help cache"""
        self.sync_config()
        self.command_index.reset_cache()
        await self.cache.clear_cache()
        return "Help cache has been refreshed."

    async def search_command(
        self, event: AstrMessageEvent, keyword: str, permission_filter: str = "all"
    ) -> str:
        """Search for commands or get detailed command information.

        Smart behavior:
        - If keyword is empty: returns all available plugins and their commands
        - If multiple matches: returns a list of matching commands grouped by plugin
        - If single exact match: returns detailed information for that command
        """
        # If keyword is empty, return all commands
        if not keyword or not keyword.strip():
            return await self.list_all_plugins_and_commands(event)

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

            logger.debug(
                f"After permission filter ({permission_filter}): {len(results)} results remaining"
            )

            matched_custom_groups = self._find_matching_custom_groups(keyword)

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

    async def execute_command(self, event: AstrMessageEvent, command: str) -> str:
        """Execute command"""
        allowed_plugins = await self._resolve_allowed_plugins(event)

        def search_suggestions_func(
            stripped_cmd: str, allowed: set[str] | None
        ) -> list[str]:
            display_prefix = self.prefixes[0] if self.prefixes else "/"
            return [
                replace_prefix(item["command"], display_prefix)
                for item in self.command_index.search_commands(
                    stripped_cmd,
                    limit=3,
                    allowed_plugins=allowed,
                )
            ]

        result = await self.command_executor.execute(
            event=event,
            command=command,
            allowed_plugins=allowed_plugins,
            search_suggestions_func=search_suggestions_func,
        )

        # Convert StarHandlerMetadata objects to dictionaries for JSON serialization
        if "matched_handlers" in result:
            serialized_handlers = []
            for handler in result["matched_handlers"]:
                if hasattr(handler, "__dict__"):
                    # Convert object to dictionary
                    handler_dict = {
                        "handler_module_path": getattr(
                            handler, "handler_module_path", ""
                        ),
                        "handler_name": getattr(handler, "handler_name", ""),
                        "handler_type": getattr(handler, "handler_type", ""),
                    }
                    # Add any other relevant attributes
                    for attr in ["priority", "description", "command"]:
                        if hasattr(handler, attr):
                            handler_dict[attr] = getattr(handler, attr)
                    serialized_handlers.append(handler_dict)
                else:
                    # Fallback: use string representation
                    serialized_handlers.append({"handler": str(handler)})
            result["matched_handlers"] = serialized_handlers

        return json.dumps(result, ensure_ascii=False, indent=2)

    async def list_all_plugins_and_commands(self, event: AstrMessageEvent) -> str:
        """List all plugins and commands"""
        try:
            summaries = self.command_index.get_plugin_summaries()
            result = []
            for summary in summaries.values():
                if summary.plugin.startswith("_custom_group_"):
                    continue

                commands = []
                for cmd in summary.commands:
                    if cmd.is_alias_of:
                        continue
                    commands.append(
                        {
                            "command": cmd.command,
                            "description": cmd.description,
                            "pattern": cmd.pattern,
                            "type": cmd.type,
                        }
                    )

                result.append(
                    {
                        "plugin_name": summary.plugin,
                        "display_name": summary.display_name,
                        "version": summary.plugin_version,
                        "description": summary.plugin_desc,
                        "command_count": len(commands),
                        "commands": commands,
                    }
                )

            return ListPluginsResponse(
                success=True,
                plugin_count=len(result),
                command_prefix=self.prefixes,
                plugins=result,
            ).to_json()
        except Exception as exc:
            logger.error(f"Failed to list plugins and commands: {exc}")
            return ListPluginsResponse(
                success=False,
                plugin_count=0,
                plugins=[],
                error=str(exc),
            ).to_json()

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
    # Initialize global singletons - order matters!
    # Must init paths first because config loading needs data_dir
    set_context(context)
    init_plugin_paths(plugin_dir)  # Must be called before init_config
    init_config(config)

    # Get help service instance (this creates CommandIndex)
    service = get_help_service()

    # IMPORTANT: Update command index config to load custom groups!
    # This must be called before any cache building to ensure custom_groups are loaded
    service.command_index.update_config()

    # Clear cache directory and command index on plugin load to ensure fresh cache
    try:
        clear_cache_dir()
        invalidate_command_cache()
        logger.info("Cache directory cleared on plugin load")
    except Exception as exc:
        logger.warning(f"Failed to clear cache directory: {exc}")

    return get_help_service()
