"""AstrBot Help Plugin

Provides HTML-rendered help menus, command search and detail queries, AI agent command execution, and other features.
"""

from pathlib import Path

from quart import jsonify, request

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .src import (
    CustomGroupCommand,
    CustomGroupConfig,
    get_cache_manager,
    get_config,
    get_help_service,
    init_plugin_service,
    invalidate_command_cache,
    save_custom_groups_to_storage,
    update_custom_groups_in_config,
)


class HelpPlugin(Star):
    """Help Plugin Main Class"""

    def __init__(self, context: Context, config: AstrBotConfig):
        """Initialize plugin

        Args:
            context: AstrBot Context instance
            config: AstrBot configuration
        """
        super().__init__(context)

        init_plugin_service(context, config, Path(__file__).parent)
        self._register_web_apis()

    def _register_web_apis(self):
        """Register web APIs for custom groups page"""
        plugin_name = "astrbot_plugin_helpinfo"
        self.context.register_web_api(
            f"/{plugin_name}/custom-groups",
            self.api_get_custom_groups,
            ["GET"],
            "Get custom groups list",
        )
        self.context.register_web_api(
            f"/{plugin_name}/custom-groups/create",
            self.api_create_custom_group,
            ["POST"],
            "Create custom group",
        )
        self.context.register_web_api(
            f"/{plugin_name}/custom-groups/update",
            self.api_update_custom_group,
            ["POST"],
            "Update custom group",
        )
        self.context.register_web_api(
            f"/{plugin_name}/custom-groups/delete",
            self.api_delete_custom_group,
            ["POST"],
            "Delete custom group",
        )

    async def initialize(self):
        """Plugin initialization (async)"""
        service = get_help_service()
        await service.initialize()

    async def terminate(self):
        """Plugin termination (async)"""
        service = get_help_service()
        await service.terminate()

    @filter.command("helps", alias={"帮助"})
    async def show_menu(self, event: AstrMessageEvent, query: str = ""):
        """Display command menu

        Args:
            event: Message event
            query: Search keyword (optional)
        """
        service = get_help_service()
        async for result in service.show_help(event, query, is_admin=event.is_admin()):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("help_refresh", alias={"刷新帮助缓存"})
    async def refresh_cache(self, event: AstrMessageEvent):
        """Refresh help plugin cache

        Requires admin permission.
        """
        service = get_help_service()
        message = await service.refresh_cache()
        yield event.plain_result(message)

    @filter.llm_tool(name="search_astrbot_command")
    async def search_command(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        permission_filter: str = "auto",
    ) -> str:
        """Search for AstrBot commands or triggers, or get detailed command information.

        Smart behavior based on your input:
        - When keyword is empty (optional): Lists all available plugins and commands
        - When keyword matches multiple commands: Returns a list organized by plugin
        - When keyword exactly matches one command: Returns detailed information for that command

        Args:
            keyword (str): Search keyword. Can be:
                - Empty string "" to list all commands
                - Part of a command name for fuzzy search (e.g., "help", "status")
                - Exact command name for detailed info (e.g., "/help", "status")
                - Regex trigger pattern (e.g., "来份色图")
            permission_filter (str): Permission filter, options: "auto" (default, auto-detect from user role),
                "normal" (normal member commands only), "admin" (admin commands only), "all" (all commands).

        Returns:
            For multiple matches: Results organized by plugin with command lists. Each command has:
                - "command": The full command with the correct prefix (e.g., "/help", "!status")
                - "type": Command type - "command" (regular) or "regex" (regex pattern)
            For single exact match: Detailed command information. IMPORTANT: Use the "command" field
                from the result directly when calling execute_astrbot_command, as it includes the correct prefix.
            All responses include "command_prefix" field showing available prefixes (e.g., ["/"], ["!", "#"])
        """
        service = get_help_service()

        # 自动检测用户权限
        if permission_filter == "auto":
            # 判断用户是否为管理员
            is_admin = event.is_admin() if hasattr(event, "is_admin") else False
            if is_admin:
                permission_filter = "all"  # 管理员可以看到所有命令
            else:
                permission_filter = "normal"  # 普通用户只能看到普通命令

        return await service.search_command(event, keyword, permission_filter)

    @filter.llm_tool(name="execute_astrbot_command")
    async def execute_astrbot_command(
        self,
        event: AstrMessageEvent,
        command: str = "",
    ) -> str:
        """Execute an AstrBot command.

        Important: Always get the command string from search_command results first, as it includes
        the correct prefix configured in the user's system (e.g., "/", "!", "#").

        Important Limitations: Some commands require @mentioning other users (e.g., "设置被鹿 开 @user").
        Since @ formats vary across platforms and you may not be able to obtain user IDs,
        it is recommended to only call commands that don't require @others. If @ is needed,
        guide the user to execute manually.

        Note: The results returned by search_command include an "invokable" field.
        If this field is false, it means the command's plugin is in the AI call blacklist,
        and you should not attempt to call it.

        Args:
            command (str): The command to execute. IMPORTANT: Use the exact "command" field value
                returned by search_command. This will already have the correct prefix.
                - For regular commands: Already includes prefix, e.g., "/help", "!status"
                - For regex commands: Use "regex:pattern" format, e.g., "regex:来份色图"
                  The executor will automatically convert this to actual matching text
                - Avoid calling commands that require @user, e.g., "设置被鹿 开 @user"

        Returns:
            Command execution result, including success status, matched handler, generated messages, etc.
        """
        service = get_help_service()
        return await service.execute_command(event, command)

    # === Web API Handlers for Custom Groups Page ===

    async def api_get_custom_groups(self):
        """Get all custom groups"""
        try:
            cfg = get_config()
            groups = []
            for g in cfg.custom_groups:
                commands = []
                for c in g.commands:
                    if c.type == "regex":
                        commands.append(
                            {
                                "type": "regex",
                                "pattern": c.pattern,
                                "examples": c.examples,
                                "is_admin": c.is_admin,
                                "hidden": c.hidden,
                            }
                        )
                    else:
                        commands.append(
                            {
                                "type": "command",
                                "command": c.command,
                                "aliases": c.aliases,
                                "is_admin": c.is_admin,
                                "hidden": c.hidden,
                            }
                        )
                groups.append(
                    {
                        "group_name": g.group_name,
                        "description": g.description,
                        "priority": g.priority,
                        "hidden": g.hidden,
                        "commands": commands,
                    }
                )
            return jsonify({"success": True, "data": groups})
        except Exception as e:
            import traceback

            print(f"api_get_custom_groups error: {e}")
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    async def api_create_custom_group(self):
        """Create a new custom group"""
        try:
            data = await request.get_json()
            print(f"[api_create_custom_group] Received data: {data}")
            if not data or not data.get("group_name"):
                return jsonify(
                    {"success": False, "error": "Group name is required"}
                ), 400

            cfg = get_config()
            custom_groups = list(cfg.custom_groups)  # Copy current groups

            # Check for duplicate names
            for g in custom_groups:
                if g.group_name == data["group_name"]:
                    return jsonify(
                        {"success": False, "error": "Group name already exists"}
                    ), 400

            # Build commands list
            commands = []
            for cmd in data.get("commands", []):
                print(f"[api_create_custom_group] Processing command: {cmd}")
                cmd_type = cmd.get("type", "command")
                if cmd_type == "regex":
                    # Regex type: pattern is required
                    if cmd.get("pattern"):
                        commands.append(
                            CustomGroupCommand(
                                type="regex",
                                pattern=cmd["pattern"],
                                examples=[str(e) for e in cmd.get("examples", []) if e],
                                is_admin=cmd.get("is_admin", False),
                                hidden=cmd.get("hidden", False),
                            )
                        )
                    else:
                        return jsonify(
                            {
                                "success": False,
                                "error": "Regex command requires 'pattern' field",
                            }
                        ), 400
                else:
                    # Command type: command or aliases is required
                    cmd_name = cmd.get("command", "").strip()
                    aliases = cmd.get("aliases", [])

                    if not cmd_name and not aliases:
                        return jsonify(
                            {
                                "success": False,
                                "error": "Command must have either 'command' or 'aliases' field",
                            }
                        ), 400

                    sub_commands = cmd.get("sub_commands", [])
                    print(
                        f"[api_create_custom_group] Command '{cmd_name or aliases[0]}' sub_commands: {sub_commands}"
                    )
                    commands.append(
                        CustomGroupCommand(
                            type="command",
                            command=cmd_name,
                            aliases=[str(a) for a in aliases if a] if aliases else [],
                            sub_commands=[str(s) for s in sub_commands if s],
                            is_admin=cmd.get("is_admin", False),
                            hidden=cmd.get("hidden", False),
                        )
                    )

            new_group = CustomGroupConfig(
                group_name=data["group_name"],
                description=data.get("description", ""),
                priority=data.get("priority", 0),
                hidden=data.get("hidden", False),
                commands=commands,
            )
            print(
                f"[api_create_custom_group] Created group with commands: {[{'cmd': c.command, 'sub': c.sub_commands} for c in commands]}"
            )

            custom_groups.append(new_group)

            # Update in-memory config first
            update_custom_groups_in_config(custom_groups)

            # Invalidate command cache to rebuild with new custom groups
            invalidate_command_cache()

            # Clear image cache so next /helps regenerates with new custom groups
            await get_cache_manager().clear_cache()

            # Save to storage (best-effort, in-memory state is already consistent)
            if not save_custom_groups_to_storage(custom_groups):
                return jsonify(
                    {"success": False, "error": "Failed to save to storage"}
                ), 500

            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    async def api_update_custom_group(self):
        """Update an existing custom group"""
        try:
            data = await request.get_json()
            if not data or "index" not in data:
                return jsonify(
                    {"success": False, "error": "Group index is required"}
                ), 400

            index = data["index"]
            group_data = data.get("group", {})

            cfg = get_config()
            custom_groups = list(cfg.custom_groups)  # Copy current groups

            if index < 0 or index >= len(custom_groups):
                return jsonify({"success": False, "error": "Group not found"}), 404

            # Check for duplicate names (excluding current index)
            for i, g in enumerate(custom_groups):
                if i != index and g.group_name == group_data.get("group_name"):
                    return jsonify(
                        {"success": False, "error": "Group name already exists"}
                    ), 400

            # Build commands list
            commands = []
            for cmd in group_data.get("commands", []):
                print(f"[api_update_custom_group] Processing command: {cmd}")
                cmd_type = cmd.get("type", "command")
                if cmd_type == "regex":
                    # Regex type: pattern is required
                    if cmd.get("pattern"):
                        commands.append(
                            CustomGroupCommand(
                                type="regex",
                                pattern=cmd["pattern"],
                                examples=[str(e) for e in cmd.get("examples", []) if e],
                                is_admin=cmd.get("is_admin", False),
                                hidden=cmd.get("hidden", False),
                            )
                        )
                    else:
                        return jsonify(
                            {
                                "success": False,
                                "error": "Regex command requires 'pattern' field",
                            }
                        ), 400
                else:
                    # Command type: command or aliases is required
                    cmd_name = cmd.get("command", "").strip()
                    aliases = cmd.get("aliases", [])

                    if not cmd_name and not aliases:
                        return jsonify(
                            {
                                "success": False,
                                "error": "Command must have either 'command' or 'aliases' field",
                            }
                        ), 400

                    sub_commands = cmd.get("sub_commands", [])
                    print(
                        f"[api_update_custom_group] Command '{cmd_name or aliases[0]}' sub_commands: {sub_commands}"
                    )
                    commands.append(
                        CustomGroupCommand(
                            type="command",
                            command=cmd_name,
                            aliases=[str(a) for a in aliases if a] if aliases else [],
                            sub_commands=[str(s) for s in sub_commands if s],
                            is_admin=cmd.get("is_admin", False),
                            hidden=cmd.get("hidden", False),
                        )
                    )

            custom_groups[index] = CustomGroupConfig(
                group_name=group_data["group_name"],
                description=group_data.get("description", ""),
                priority=group_data.get("priority", 0),
                hidden=group_data.get("hidden", False),
                commands=commands,
            )

            # Update in-memory config first
            update_custom_groups_in_config(custom_groups)

            # Invalidate command cache to rebuild with updated custom groups
            invalidate_command_cache()

            # Clear image cache so next /helps regenerates with updated custom groups
            await get_cache_manager().clear_cache()

            # Save to storage (best-effort, in-memory state is already consistent)
            if not save_custom_groups_to_storage(custom_groups):
                return jsonify(
                    {"success": False, "error": "Failed to save to storage"}
                ), 500

            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    async def api_delete_custom_group(self):
        """Delete a custom group"""
        try:
            data = await request.get_json()
            if not data or "index" not in data:
                return jsonify(
                    {"success": False, "error": "Group index is required"}
                ), 400

            index = data["index"]

            cfg = get_config()
            custom_groups = list(cfg.custom_groups)  # Copy current groups

            if index < 0 or index >= len(custom_groups):
                return jsonify({"success": False, "error": "Group not found"}), 404

            custom_groups.pop(index)

            # Update in-memory config first
            update_custom_groups_in_config(custom_groups)

            # Invalidate command cache to rebuild without deleted custom group
            invalidate_command_cache()

            # Clear image cache so next /helps regenerates without deleted custom group
            await get_cache_manager().clear_cache()

            # Save to storage (best-effort, in-memory state is already consistent)
            if not save_custom_groups_to_storage(custom_groups):
                return jsonify(
                    {"success": False, "error": "Failed to save to storage"}
                ), 500

            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
