"""AstrBot Help Plugin。

提供 HTML 帮助菜单、命令搜索、AI 命令执行和自定义命令组管理能力。
"""

import json
from pathlib import Path
from typing import Any

from quart import jsonify, request

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .src import get_custom_group_service, get_help_service, init_plugin_service


class HelpPlugin(Star):
    """Help Plugin Main Class"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        init_plugin_service(context, config, Path(__file__).parent)
        self._register_web_apis()

    def _register_web_apis(self):
        """注册兼容既有管理页面的自定义命令组接口。"""
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
        await get_help_service().initialize()

    async def terminate(self):
        """Plugin termination (async)"""
        await get_help_service().terminate()

    @filter.command("helps", alias={"帮助"})
    async def show_menu(self, event: AstrMessageEvent, query: str = ""):
        """显示命令帮助菜单。"""
        async for result in get_help_service().show_help(
            event, query, is_admin=event.is_admin()
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("help_refresh", alias={"刷新帮助缓存"})
    async def refresh_cache(self, event: AstrMessageEvent):
        """刷新帮助插件缓存（仅管理员）。"""
        message = await get_help_service().refresh_cache()
        yield event.plain_result(message)

    @filter.llm_tool(name="search_astrbot_command")
    async def search_command(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        permission_filter: str = "auto",
    ) -> str:
        """搜索 AstrBot 命令或触发式，并返回可执行的完整命令信息。"""
        service = get_help_service()
        if permission_filter == "auto":
            is_admin = event.is_admin() if hasattr(event, "is_admin") else False
            permission_filter = "all" if is_admin else "normal"
        return await service.search_command(event, keyword, permission_filter)

    @filter.llm_tool(name="execute_astrbot_command")
    async def execute_astrbot_command(
        self,
        event: AstrMessageEvent,
        command: str = "",
        actor: str = "user",
        result_mode: str = "auto",
        wait_seconds: float | None = None,
    ) -> str:
        """执行已搜索到的 AstrBot 命令，并按需监听本次命令的结果。

        ``auto`` 默认监听配置的短窗口；``background`` 在调度启动后立即返回；
        ``custom`` 需要正的 ``wait_seconds``，且不得超过配置上限。命令输出始终
        继续发送到当前聊天，tool 仅返回本次 synthetic event 可归因的摘要。
        """
        service = get_help_service()
        return await service.execute_command(
            event, command, actor, result_mode, wait_seconds
        )

    @filter.llm_tool(name="list_custom_groups")
    async def list_custom_groups(self, event: AstrMessageEvent) -> str:
        """读取自定义命令目录；普通用户不会看到隐藏或管理员目录项。"""
        return self._tool_json(
            await get_custom_group_service().list_groups(is_admin=event.is_admin())
        )

    @filter.llm_tool(name="create_custom_group")
    async def create_custom_group(
        self,
        event: AstrMessageEvent,
        group_name: str,
        description: str = "",
        priority: int = 0,
        hidden: bool = False,
    ) -> str:
        """管理员创建空目录组；目录条目只描述已有命令，不会创建 handler。"""
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().create_group(
                group_name, description, priority, hidden
            )
        )

    @filter.llm_tool(name="update_custom_group")
    async def update_custom_group(
        self,
        event: AstrMessageEvent,
        group_name: str,
        new_group_name: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        hidden: bool | None = None,
    ) -> str:
        """管理员按分组名称修改目录组的元数据，不会创建命令 handler。"""
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().update_group(
                group_name,
                new_group_name=new_group_name,
                description=description,
                priority=priority,
                hidden=hidden,
            )
        )

    @filter.llm_tool(name="preview_delete_custom_group")
    async def preview_delete_custom_group(
        self, event: AstrMessageEvent, group_name: str
    ) -> str:
        """管理员预览整组删除并获取一次性 token；必须再调用确认工具。"""
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().preview_delete_group(group_name)
        )

    @filter.llm_tool(name="confirm_delete_custom_group")
    async def confirm_delete_custom_group(
        self, event: AstrMessageEvent, group_name: str, delete_token: str
    ) -> str:
        """管理员使用 preview 返回的一次性 token 确认删除整组目录。"""
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().confirm_delete_group(
                group_name, delete_token
            )
        )

    @filter.llm_tool(name="add_custom_group_command")
    async def add_custom_group_command(
        self,
        event: AstrMessageEvent,
        group_name: str,
        command_type: str,
        command: str | None = None,
        pattern: str | None = None,
        description: str = "",
        is_admin: bool = False,
        hidden: bool = False,
        aliases: list[str] | None = None,
        examples: list[str] | None = None,
        sub_commands: list[str] | None = None,
    ) -> str:
        """管理员新增目录条目；未验证真实命令只会返回 warning，不创建 handler。"""
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().add_command(
                group_name,
                command_type,
                command=command,
                pattern=pattern,
                description=description,
                is_admin=is_admin,
                hidden=hidden,
                aliases=aliases,
                examples=examples,
                sub_commands=sub_commands,
            )
        )

    @filter.llm_tool(name="update_custom_group_command")
    async def update_custom_group_command(
        self,
        event: AstrMessageEvent,
        group_name: str,
        command_type: str,
        current_trigger: str,
        command: str | None = None,
        pattern: str | None = None,
        description: str | None = None,
        is_admin: bool | None = None,
        hidden: bool | None = None,
        aliases: list[str] | None = None,
        examples: list[str] | None = None,
        sub_commands: list[str] | None = None,
    ) -> str:
        """管理员按命令触发式更新目录条目；不会创建或修改 handler。"""
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().update_command(
                group_name,
                command_type,
                current_trigger,
                command=command,
                pattern=pattern,
                description=description,
                is_admin=is_admin,
                hidden=hidden,
                aliases=aliases,
                examples=examples,
                sub_commands=sub_commands,
            )
        )

    @filter.llm_tool(name="delete_custom_group_command")
    async def delete_custom_group_command(
        self,
        event: AstrMessageEvent,
        group_name: str,
        command_type: str,
        trigger: str,
    ) -> str:
        """管理员按精确触发式删除单条目录命令；空分组仍保留。"""
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().delete_command(
                group_name, command_type, trigger
            )
        )

    # === Web API Handlers for Custom Groups Page ===

    async def api_get_custom_groups(self):
        """保持原页面数据格式，并从同一服务读取完整管理员视图。"""
        response = await get_custom_group_service().list_groups(is_admin=True)
        if not response["success"]:
            return self._web_response(response)
        return jsonify(
            {
                "success": True,
                "data": [self._web_group(group) for group in response["groups"]],
            }
        )

    async def api_create_custom_group(self):
        """兼容原 POST payload，并以一次服务提交创建整组。"""
        data = await self._request_json_object()
        if isinstance(data, tuple):
            return data
        response = await get_custom_group_service().create_group_with_commands(
            data.get("group_name", ""),
            description=data.get("description", ""),
            priority=data.get("priority", 0),
            hidden=data.get("hidden", False),
            commands=data["commands"] if "commands" in data else [],
        )
        return self._web_response(response)

    async def api_update_custom_group(self):
        """兼容 index 定位，先解析当前组名后按自然键原子整组替换。"""
        data = await self._request_json_object()
        if isinstance(data, tuple):
            return data
        index = data.get("index")
        if type(index) is not int:
            return jsonify({"success": False, "error": "Group index is required"}), 400
        group_data = data.get("group")
        if not isinstance(group_data, dict):
            return jsonify({"success": False, "error": "Group is required"}), 400
        listed = await get_custom_group_service().list_groups(is_admin=True)
        if not listed["success"]:
            return self._web_response(listed)
        groups = listed["groups"]
        if index < 0 or index >= len(groups):
            return jsonify({"success": False, "error": "Group not found"}), 404
        response = await get_custom_group_service().replace_group(
            groups[index]["group_name"],
            group_name=group_data.get("group_name", ""),
            description=group_data.get("description", ""),
            priority=group_data.get("priority", 0),
            hidden=group_data.get("hidden", False),
            commands=group_data["commands"] if "commands" in group_data else [],
        )
        return self._web_response(response)

    async def api_delete_custom_group(self):
        """兼容原单步删除 payload；AI 删除仍坚持 preview→confirm。"""
        data = await self._request_json_object()
        if isinstance(data, tuple):
            return data
        index = data.get("index")
        if type(index) is not int:
            return jsonify({"success": False, "error": "Group index is required"}), 400
        listed = await get_custom_group_service().list_groups(is_admin=True)
        if not listed["success"]:
            return self._web_response(listed)
        groups = listed["groups"]
        if index < 0 or index >= len(groups):
            return jsonify({"success": False, "error": "Group not found"}), 404
        response = await get_custom_group_service().delete_group_for_web(
            groups[index]["group_name"]
        )
        return self._web_response(response)

    @staticmethod
    def _tool_json(response: dict[str, Any]) -> str:
        """将服务结构化结果交给 LLM tool。"""
        return json.dumps(response, ensure_ascii=False)

    @classmethod
    def _write_permission(cls, event: AstrMessageEvent) -> str | None:
        """写工具必须在调用服务前显式拒绝非管理员。"""
        if event.is_admin():
            return None
        return cls._tool_json(
            {
                "success": False,
                "error": "permission_denied",
                "message": "只有管理员可以修改自定义命令目录",
                "warnings": [],
            }
        )

    @staticmethod
    def _web_group(group: dict[str, Any]) -> dict[str, Any]:
        """保留管理页面此前使用的命令对象字段形状。"""
        commands = []
        for command in group["commands"]:
            item = {
                "type": command["type"],
                "description": command["description"],
                "is_admin": command["is_admin"],
                "hidden": command["hidden"],
            }
            if command["type"] == "regex":
                item.update(pattern=command["pattern"], examples=command["examples"])
            else:
                item.update(command=command["command"], aliases=command["aliases"])
            commands.append(item)
        return {
            "group_name": group["group_name"],
            "description": group["description"],
            "priority": group["priority"],
            "hidden": group["hidden"],
            "commands": commands,
        }

    @staticmethod
    async def _request_json_object() -> dict[str, Any] | tuple[Any, int]:
        """将无效 JSON 显式映射为 400，避免被包装成服务器成功。"""
        try:
            data = await request.get_json()
        except Exception:
            return jsonify({"success": False, "error": "Invalid JSON body"}), 400
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "JSON object is required"}), 400
        return data

    @staticmethod
    def _web_response(response: dict[str, Any]):
        """将服务错误映射为真实 HTTP 状态，同时维持 success/error 外形。"""
        if response["success"]:
            # 保持旧成功响应的最小形状；运行态失效等警告不能在 Web 边界丢失。
            payload: dict[str, Any] = {"success": True}
            if response.get("warnings"):
                payload["warnings"] = response["warnings"]
            return jsonify(payload)
        error = response.get("error")
        status = (
            500
            if error == "persistence_failed"
            else 404
            if error
            in {
                "group_not_found",
                "command_not_found",
            }
            else 400
        )
        return jsonify({"success": False, "error": response["message"]}), status
