"""AstrBot 智能命令代理插件。

提供 AI 命令目录、搜索、委托执行、身份解析和自定义目录管理。
"""

import json
from pathlib import Path
from typing import Any

from quart import jsonify, request

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.tool import FunctionTool

from .src import get_custom_group_service, get_help_service, init_plugin_service
from .src.infrastructure.analysis import invalidate_command_cache
from .src.infrastructure.config.datamodels import CustomGroupConfig


EXECUTE_ASTRBOT_COMMAND_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "要执行的完整命令文本。",
        },
        "actor": {
            "type": "string",
            "enum": ["user", "self"],
            "description": "执行身份；默认使用当前用户。",
        },
        "result_mode": {
            "type": "string",
            "enum": ["auto", "background", "custom"],
            "description": "结果监听方式；默认 auto。",
        },
        "wait_seconds": {
            "type": "number",
            "description": "custom 模式的监听秒数。",
        },
        "target_user": {
            "type": "string",
            "description": "可选目标用户：昵称、UID、@、reply_target 或 resolve 返回的 target_ref。",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}

SEARCH_ASTRBOT_COMMAND_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "description": "命令、触发式或描述关键词；可留空查询本人偏好。",
        },
        "permission_filter": {
            "type": "string",
            "enum": ["auto", "normal", "admin", "all"],
        },
        "target_user": {
            "type": "string",
            "description": "可选目标用户昵称、UID、@、reply_target 或 target_ref。",
        },
        "preference_mode": {
            "type": "string",
            "enum": ["auto", "recent", "frequent", "off"],
        },
    },
    "additionalProperties": False,
}


def _string_tool_schema(*required: str, **descriptions: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": description}
            for name, description in descriptions.items()
        },
        "required": list(required),
        "additionalProperties": False,
    }


def _strict_tool_schema(properties: dict[str, Any], *required: str) -> dict[str, Any]:
    """构造 AstrBot FunctionTool 可直接消费的严格对象 schema。"""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _custom_entry_tool_properties() -> dict[str, Any]:
    return {
        "group_name": {"type": "string", "description": "现有目录组名称。"},
        "command_type": {
            "type": "string",
            "enum": ["command", "regex"],
            "description": "目录条目类型。",
        },
        "command": {"type": "string", "description": "普通命令触发式。"},
        "pattern": {"type": "string", "description": "正则触发模式。"},
        "description": {"type": "string", "description": "目录说明。"},
        "is_admin": {
            "type": "boolean",
            "description": "旧版兼容权限字段；省略时由 permission_level 派生。",
        },
        "permission_level": {
            "type": "string",
            "enum": ["normal", "admin"],
            "description": "权威权限等级。",
        },
        "delegation_policy": {
            "type": "string",
            "enum": ["normal", "sensitive", "forbidden"],
        },
        "history_mode": {
            "type": "string",
            "enum": ["none", "command", "full"],
        },
        "hidden": {"type": "boolean"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "examples": {"type": "array", "items": {"type": "string"}},
        "sub_commands": {"type": "array", "items": {"type": "string"}},
        "linked_plugin": {"type": "string"},
        "availability": {
            "type": "string",
            "enum": ["available", "missing_plugin"],
        },
    }


class HelpPlugin(Star):
    """Help Plugin Main Class"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        init_plugin_service(context, config, Path(__file__).parent)
        self._register_web_apis()

    def _register_execute_command_tool(self) -> None:
        """以显式 JSON Schema 覆盖装饰器注册，确保 Agent 始终能传入 command。"""
        tool = FunctionTool(
            name="execute_astrbot_command",
            description=(
                "以原请求者权限在当前聊天执行 AstrBot 命令，可为已解析目标用户委托。"
                "completed 表示完成；accepted、external_dispatched 和 duplicate_suppressed "
                "均表示已受理且不得重复调用；仅 retryable=true 的 failed 可重试。"
            ),
            parameters=EXECUTE_ASTRBOT_COMMAND_PARAMETERS,
            handler=self.execute_astrbot_command,
        )
        self.context.add_llm_tools(tool)
        # 此处 handler 已绑定当前插件实例，故在 StarManager 绑定阶段结束后注册，
        # 并显式保留插件归属，保证会话筛选与插件卸载仍能识别该工具。
        tool.handler_module_path = self.__class__.__module__

    def _register_runtime_tools(self) -> None:
        """显式注册上下文身份与搜索 schema，避免装饰器解析退化为空参数。"""
        definitions = (
            (
                "search_astrbot_command",
                "按原请求者权限搜索 AstrBot 命令；可对目标用户做不泄露统计的保守偏好排序。",
                SEARCH_ASTRBOT_COMMAND_PARAMETERS,
                self.search_command,
            ),
            (
                "resolve_astrbot_user",
                "在当前会话解析昵称、UID、@、引用或个人别名；重名时返回候选，禁止猜测。",
                _string_tool_schema(
                    "reference",
                    reference="要解析的用户称呼、UID、@、reply_target 或 target_ref。",
                ),
                self.resolve_astrbot_user,
            ),
            (
                "set_astrbot_user_alias",
                "为当前请求者在当前会话保存个人用户别名；目标必须先能唯一解析。",
                _string_tool_schema(
                    "alias",
                    "target_user",
                    alias="个人别名。",
                    target_user="目标用户引用。",
                ),
                self.set_astrbot_user_alias,
            ),
            (
                "list_astrbot_user_aliases",
                "列出当前请求者在当前会话保存的个人用户别名。",
                {"type": "object", "properties": {}, "additionalProperties": False},
                self.list_astrbot_user_aliases,
            ),
            (
                "delete_astrbot_user_alias",
                "删除当前请求者在当前会话保存的一个个人用户别名。",
                _string_tool_schema("alias", alias="要删除的精确别名。"),
                self.delete_astrbot_user_alias,
            ),
        )
        tools = []
        for name, description, parameters, handler in definitions:
            tool = FunctionTool(
                name=name,
                description=description,
                parameters=parameters,
                handler=handler,
            )
            tool.handler_module_path = self.__class__.__module__
            tools.append(tool)
        self.context.add_llm_tools(*tools)
        # AstrBot 会在注册时覆盖预设归属，必须在写入 manager 后恢复。
        for tool in tools:
            tool.handler_module_path = self.__class__.__module__

    def _register_custom_group_tools(self) -> None:
        """显式注册目录工具，禁止依赖 Args 文档推导或重复装饰器注册。"""
        group_fields = {
            "group_name": {"type": "string", "description": "目录组名称。"},
            "description": {"type": "string", "description": "目录组说明。"},
            "priority": {"type": "integer", "description": "排序优先级。"},
            "hidden": {"type": "boolean", "description": "是否隐藏。"},
        }
        update_group_fields = {
            **group_fields,
            "new_group_name": {"type": "string", "description": "新目录组名称。"},
        }
        entry_fields = _custom_entry_tool_properties()
        update_entry_fields = {
            **entry_fields,
            "current_trigger": {
                "type": "string",
                "description": "当前精确命令或正则触发式。",
            },
            "clear_linked_plugin": {
                "type": "boolean",
                "description": "显式清除插件关联；与非空 linked_plugin 互斥。",
            },
        }
        definitions = (
            (
                "list_custom_groups",
                "按请求者权限列出自定义命令目录。",
                _strict_tool_schema({}),
                self.list_custom_groups,
            ),
            (
                "create_custom_group",
                "管理员创建空的自定义目录组。",
                _strict_tool_schema(group_fields, "group_name"),
                self.create_custom_group,
            ),
            (
                "update_custom_group",
                "管理员按名称更新自定义目录组元数据。",
                _strict_tool_schema(update_group_fields, "group_name"),
                self.update_custom_group,
            ),
            (
                "preview_delete_custom_group",
                "管理员预览整组删除并取得一次性确认 token。",
                _strict_tool_schema({"group_name": {"type": "string"}}, "group_name"),
                self.preview_delete_custom_group,
            ),
            (
                "confirm_delete_custom_group",
                "管理员使用预览 token 确认删除整个目录组。",
                _strict_tool_schema(
                    {
                        "group_name": {"type": "string"},
                        "delete_token": {"type": "string"},
                    },
                    "group_name",
                    "delete_token",
                ),
                self.confirm_delete_custom_group,
            ),
            (
                "add_custom_group_command",
                "管理员新增普通或正则目录条目；permission_level 是权威权限字段。",
                _strict_tool_schema(entry_fields, "group_name", "command_type"),
                self.add_custom_group_command,
            ),
            (
                "update_custom_group_command",
                "管理员按当前触发式更新一个目录条目。",
                _strict_tool_schema(
                    update_entry_fields,
                    "group_name",
                    "command_type",
                    "current_trigger",
                ),
                self.update_custom_group_command,
            ),
            (
                "delete_custom_group_command",
                "管理员直接删除一个目录条目，空组继续保留。",
                _strict_tool_schema(
                    {
                        "group_name": {"type": "string"},
                        "command_type": {
                            "type": "string",
                            "enum": ["command", "regex"],
                        },
                        "trigger": {"type": "string"},
                    },
                    "group_name",
                    "command_type",
                    "trigger",
                ),
                self.delete_custom_group_command,
            ),
        )
        tools = []
        for name, description, parameters, handler in definitions:
            tool = FunctionTool(
                name=name,
                description=description,
                parameters=parameters,
                handler=handler,
            )
            tool.handler_module_path = self.__class__.__module__
            tools.append(tool)
        self.context.add_llm_tools(*tools)
        # AstrBot 4.25 的 Context.add_llm_tools 会按 FunctionTool 类模块重写
        # 整批工具归属；注册后逐项恢复，插件卸载才能精确清理这些工具。
        for tool in tools:
            tool.handler_module_path = self.__class__.__module__

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
            f"/{plugin_name}/custom-groups/delete-preview",
            self.api_preview_delete_custom_group,
            ["POST"],
            "Preview custom group deletion",
        )
        self.context.register_web_api(
            f"/{plugin_name}/custom-groups/delete",
            self.api_delete_custom_group,
            ["POST"],
            "Delete custom group",
        )
        self.context.register_web_api(
            f"/{plugin_name}/commands",
            self.api_get_commands,
            ["GET"],
            "List command catalog",
        )
        self.context.register_web_api(
            f"/{plugin_name}/commands/policy",
            self.api_update_command_policy,
            ["POST"],
            "Update command policy",
        )

    async def initialize(self):
        """Plugin initialization (async)"""
        self._register_execute_command_tool()
        self._register_runtime_tools()
        self._register_custom_group_tools()
        await get_help_service().initialize()

    async def terminate(self):
        """Plugin termination (async)"""
        await get_help_service().terminate()

    @filter.on_astrbot_loaded()
    async def handle_astrbot_loaded(self):
        """AstrBot 完成加载后以 active plugin 快照重建 runtime 目录。"""
        service = get_help_service()
        service.command_index.reset_cache()
        service.command_runtime.sync_all()
        service.command_runtime.cleanup()

    @filter.on_plugin_loaded()
    async def handle_plugin_loaded(self, metadata):
        """插件加载后增量同步其 runtime 目录并恢复 linked custom。"""
        service = get_help_service()
        service.command_index.reset_cache()
        service.command_runtime.sync_plugin(metadata)
        service.command_runtime.cleanup()

    @filter.on_plugin_unloaded()
    async def handle_plugin_unloaded(self, metadata):
        """插件卸载后移除 runtime；linked custom 仅标为 missing。"""
        service = get_help_service()
        service.command_runtime.unload_plugin(metadata)
        service.command_runtime.cleanup()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def observe_group_identity(self, event: AstrMessageEvent):
        """被动记录群聊最小身份元数据；synthetic event 由服务自动跳过。"""
        await get_help_service().command_runtime.identity_service.observe_event(event)

    @filter.command("ai_command_privacy")
    async def ai_command_privacy(
        self,
        event: AstrMessageEvent,
        action: str = "status",
        target_user: str = "",
        mode: str = "",
    ):
        """查看或修改 AI 命令代操作隐私设置。"""
        service = get_help_service()
        identity = service.command_runtime.identity_service
        platform_id = str(event.get_platform_id())
        requester_id = str(event.get_sender_id())
        if action == "status":
            yield event.plain_result(
                json.dumps(
                    identity.get_user_settings(platform_id, requester_id),
                    ensure_ascii=False,
                )
            )
            return
        if action in {"allow", "deny_sensitive", "deny_all"}:
            target_id = requester_id
            selected_mode = action
        elif action == "set":
            if not event.is_admin():
                yield event.plain_result("只有管理员可以修改其他用户的隐私设置")
                return
            resolution, target_id = await identity.resolve_for_management(
                event, target_user, requester_id=requester_id
            )
            if target_id is None:
                yield event.plain_result(
                    json.dumps(
                        {"success": False, "identity": resolution}, ensure_ascii=False
                    )
                )
                return
            selected_mode = mode
        else:
            yield event.plain_result(
                "用法: /ai_command_privacy status|allow|deny_sensitive|deny_all|set <目标> <模式>"
            )
            return
        if selected_mode not in {"allow", "deny_sensitive", "deny_all"}:
            yield event.plain_result("模式仅支持 allow、deny_sensitive、deny_all")
            return
        settings = identity.set_user_settings(
            platform_id,
            target_id,
            allow_llm_operation=selected_mode != "deny_all",
            allow_sensitive_delegation=selected_mode == "allow",
        )
        yield event.plain_result(json.dumps(settings, ensure_ascii=False))

    @filter.command("ai_command_alias")
    async def ai_command_alias(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        alias: str = "",
        target_user: str = "",
    ):
        """管理当前请求者在当前会话的个人用户别名。"""
        identity = get_help_service().command_runtime.identity_service
        requester_id = str(event.get_sender_id())
        try:
            if action == "list":
                result: object = identity.list_aliases(event, requester_id=requester_id)
            elif action == "set":
                if not alias or not target_user:
                    yield event.plain_result(
                        "用法: /ai_command_alias set <别名> <目标>"
                    )
                    return
                result = await identity.set_alias(
                    event,
                    requester_id=requester_id,
                    alias=alias,
                    target_reference=target_user,
                )
            elif action == "delete":
                result = {
                    "deleted": identity.delete_alias(
                        event, requester_id=requester_id, alias=alias
                    )
                }
            elif action == "clear":
                result = {
                    "deleted": identity.clear_aliases(event, requester_id=requester_id)
                }
            else:
                yield event.plain_result(
                    "用法: /ai_command_alias list|set|delete|clear"
                )
                return
        except (ValueError, RuntimeError) as error:
            yield event.plain_result(
                json.dumps({"success": False, "error": str(error)}, ensure_ascii=False)
            )
            return
        yield event.plain_result(json.dumps(result, ensure_ascii=False))

    @filter.command("ai_command_history")
    async def ai_command_history(
        self,
        event: AstrMessageEvent,
        action: str = "",
        target_user: str = "",
    ):
        """清除命令历史；不提供会泄露偏好明细的聊天查询入口。"""
        if action != "clear":
            yield event.plain_result("用法: /ai_command_history clear [目标]")
            return

        service = get_help_service()
        runtime = service.command_runtime
        requester_id = str(event.get_sender_id())
        is_admin = bool(event.is_admin())
        target_text = target_user.strip()

        if target_text:
            if not is_admin:
                yield event.plain_result("只有管理员可以清除其他用户的命令历史")
                return
            (
                resolution,
                target_id,
            ) = await runtime.identity_service.resolve_for_management(
                event,
                target_text,
                requester_id=requester_id,
            )
            if target_id is None:
                yield event.plain_result(
                    json.dumps(
                        {"success": False, "identity": resolution},
                        ensure_ascii=False,
                    )
                )
                return
            target = resolution
        else:
            target_id = requester_id
            target = {"source": "requester"}

        counts = runtime.history_service.clear_user_history(
            platform_id=str(event.get_platform_id()),
            target_user_id=target_id,
            requester_user_id=requester_id,
            is_admin=is_admin,
        )
        yield event.plain_result(
            json.dumps(
                {"success": True, "target": target, **counts},
                ensure_ascii=False,
            )
        )

    @filter.llm_tool(name="search_astrbot_command")
    async def search_command(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        permission_filter: str = "auto",
        target_user: str = "",
        preference_mode: str = "auto",
    ) -> str:
        """搜索 AstrBot 命令或触发式，并返回可执行的完整命令信息。

        Args:
            keyword(string): 命令名称、触发式或描述中的搜索关键词；留空可列出匹配项。
            permission_filter(string): 权限筛选；``auto`` 按调用者权限筛选，或使用 ``normal``、``admin``、``all``。
            target_user(string): 可选目标用户昵称、UID、@、reply_target 或 target_ref。
            preference_mode(string): 偏好方式；``auto``、``recent``、``frequent`` 或 ``off``。
        """
        service = get_help_service()
        if permission_filter == "auto":
            is_admin = event.is_admin() if hasattr(event, "is_admin") else False
            permission_filter = "all" if is_admin else "normal"
        return await service.search_command(
            event, keyword, permission_filter, target_user, preference_mode
        )

    @filter.llm_tool(name="execute_astrbot_command")
    async def execute_astrbot_command(
        self,
        event: AstrMessageEvent,
        command: str = "",
        actor: str = "user",
        result_mode: str = "auto",
        wait_seconds: float | None = None,
        target_user: str = "",
    ) -> str:
        """执行已搜索到的 AstrBot 命令，并按需监听本次命令的结果。

        ``auto`` 默认监听配置的短窗口；``background`` 在调度启动后立即返回；
        ``custom`` 需要正的 ``wait_seconds``，且不得超过配置上限。命令输出始终
        继续发送到当前聊天，tool 仅返回本次 synthetic event 可归因的摘要。

        Args:
            command(string): 要执行的完整命令文本。即使目标命令本身无参数，也必须传入其触发式，例如 ``帮助``。
            actor(string): 执行身份；``user`` 使用当前用户，``self`` 使用机器人自身（需开启对应配置）。
            result_mode(string): 结果监听方式；可为 ``auto``、``background`` 或 ``custom``。
            wait_seconds(number): ``custom`` 模式的监听秒数；其他模式不传。
            target_user(string): 可选目标用户昵称、UID、@、reply_target 或 target_ref；与 actor=self 互斥。
        """
        service = get_help_service()
        return await service.execute_command(
            event, command, actor, result_mode, wait_seconds, target_user
        )

    @filter.llm_tool(name="resolve_astrbot_user")
    async def resolve_astrbot_user(
        self, event: AstrMessageEvent, reference: str
    ) -> str:
        """解析当前会话中的用户，不唯一时返回候选且不执行命令。

        Args:
            reference(string): 用户昵称、UID、@、reply_target 或 target_ref。
        """
        return await get_help_service().resolve_user(event, reference)

    @filter.llm_tool(name="set_astrbot_user_alias")
    async def set_astrbot_user_alias(
        self, event: AstrMessageEvent, alias: str, target_user: str
    ) -> str:
        """为当前请求者保存会话内个人别名。

        Args:
            alias(string): 要保存的个人别名。
            target_user(string): 已可唯一解析的目标用户。
        """
        return await get_help_service().set_user_alias(event, alias, target_user)

    @filter.llm_tool(name="list_astrbot_user_aliases")
    async def list_astrbot_user_aliases(self, event: AstrMessageEvent) -> str:
        """列出当前请求者在当前会话的个人用户别名。"""
        return get_help_service().list_user_aliases(event)

    @filter.llm_tool(name="delete_astrbot_user_alias")
    async def delete_astrbot_user_alias(
        self, event: AstrMessageEvent, alias: str
    ) -> str:
        """删除当前请求者的一个个人用户别名。

        Args:
            alias(string): 要删除的精确别名。
        """
        return get_help_service().delete_user_alias(event, alias)

    async def list_custom_groups(self, event: AstrMessageEvent) -> str:
        """读取自定义命令目录；普通用户不会看到隐藏或管理员目录项。"""
        return self._tool_json(
            await get_custom_group_service().list_groups(is_admin=event.is_admin())
        )

    async def create_custom_group(
        self,
        event: AstrMessageEvent,
        group_name: str,
        description: str = "",
        priority: int = 0,
        hidden: bool = False,
    ) -> str:
        """管理员创建空目录组；目录条目只描述已有命令，不会创建 handler。

        Args:
            group_name(string): 新目录组的唯一名称。
            description(string): 面向帮助菜单的分组说明；可留空。
            priority(number): 帮助目录中的排序优先级；数值越大越靠前。
            hidden(boolean): 是否在普通用户的目录中隐藏该分组。
        """
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().create_group(
                group_name, description, priority, hidden
            )
        )

    async def update_custom_group(
        self,
        event: AstrMessageEvent,
        group_name: str,
        new_group_name: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        hidden: bool | None = None,
    ) -> str:
        """管理员按分组名称修改目录组的元数据，不会创建命令 handler。

        Args:
            group_name(string): 要修改的现有分组名称。
            new_group_name(string): 新分组名称；不改名时不传。
            description(string): 新分组说明；不修改时不传。
            priority(number): 新排序优先级；不修改时不传。
            hidden(boolean): 新的隐藏状态；不修改时不传。
        """
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

    async def preview_delete_custom_group(
        self, event: AstrMessageEvent, group_name: str
    ) -> str:
        """管理员预览整组删除并获取一次性 token；必须再调用确认工具。

        Args:
            group_name(string): 要删除的目录组名称。
        """
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().preview_delete_group(group_name)
        )

    async def confirm_delete_custom_group(
        self, event: AstrMessageEvent, group_name: str, delete_token: str
    ) -> str:
        """管理员使用 preview 返回的一次性 token 确认删除整组目录。

        Args:
            group_name(string): preview 时使用的目录组名称。
            delete_token(string): preview 接口返回的一次性确认 token。
        """
        denied = self._write_permission(event)
        if denied is not None:
            return denied
        return self._tool_json(
            await get_custom_group_service().confirm_delete_group(
                group_name, delete_token
            )
        )

    async def add_custom_group_command(
        self,
        event: AstrMessageEvent,
        group_name: str,
        command_type: str,
        command: str | None = None,
        pattern: str | None = None,
        description: str = "",
        is_admin: bool | None = None,
        permission_level: str | None = None,
        delegation_policy: str | None = None,
        history_mode: str = "command",
        hidden: bool = False,
        aliases: list[str] | None = None,
        examples: list[str] | None = None,
        sub_commands: list[str] | None = None,
        linked_plugin: str | None = None,
        availability: str = "available",
    ) -> str:
        """管理员新增目录条目；未验证真实命令只会返回 warning，不创建 handler。

        Args:
            group_name(string): 要写入的现有目录组名称。
            command_type(string): 条目类型；``command`` 使用 ``command`` 触发式，``regex`` 使用 ``pattern``。
            command(string): 普通命令的完整触发式；``command`` 类型必填。
            pattern(string): 正则命令的模式；``regex`` 类型必填。
            description(string): 命令说明；可留空。
            is_admin(boolean): 是否仅允许管理员在帮助目录中看到该条目。
            permission_level(string): 权限等级 normal 或 admin；必须与 is_admin 一致。
            delegation_policy(string): 委托策略 normal、sensitive 或 forbidden。
            history_mode(string): 历史模式 none、command 或 full。
            hidden(boolean): 是否在普通用户帮助目录中隐藏该条目。
            aliases(list[string]): 命令别名列表；没有别名时不传。
            examples(list[string]): 可直接执行的示例文本列表；没有示例时不传。
            sub_commands(list[string]): 子命令说明列表；没有子命令时不传。
            linked_plugin(string): 可选关联插件名称。
            availability(string): available 或 missing_plugin。
        """
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
                permission_level=permission_level,
                delegation_policy=delegation_policy,
                history_mode=history_mode,
                hidden=hidden,
                aliases=aliases,
                examples=examples,
                sub_commands=sub_commands,
                linked_plugin=linked_plugin,
                availability=availability,
            )
        )

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
        permission_level: str | None = None,
        delegation_policy: str | None = None,
        history_mode: str | None = None,
        hidden: bool | None = None,
        aliases: list[str] | None = None,
        examples: list[str] | None = None,
        sub_commands: list[str] | None = None,
        linked_plugin: str | None = None,
        clear_linked_plugin: bool = False,
        availability: str | None = None,
    ) -> str:
        """管理员按命令触发式更新目录条目；不会创建或修改 handler。

        Args:
            group_name(string): 条目所在的现有目录组名称。
            command_type(string): 条目类型；``command`` 或 ``regex``。
            current_trigger(string): 当前精确触发式；普通命令传命令文本，正则传模式。
            command(string): 更新后的普通命令触发式；不修改时不传。
            pattern(string): 更新后的正则模式；不修改时不传。
            description(string): 更新后的命令说明；不修改时不传。
            is_admin(boolean): 更新后的管理员可见性；不修改时不传。
            permission_level(string): 更新后的 normal/admin 权限；不修改时不传。
            delegation_policy(string): 更新后的 normal/sensitive/forbidden 策略。
            history_mode(string): 更新后的 none/command/full 历史模式。
            hidden(boolean): 更新后的隐藏状态；不修改时不传。
            aliases(list[string]): 更新后的完整别名列表；不修改时不传。
            examples(list[string]): 更新后的完整示例列表；不修改时不传。
            sub_commands(list[string]): 更新后的完整子命令列表；不修改时不传。
            linked_plugin(string): 更新后的关联插件；不修改时不传。
            clear_linked_plugin(boolean): 显式清除插件关联；与 linked_plugin 互斥。
            availability(string): 更新后的 available/missing_plugin 状态。
        """
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
                permission_level=permission_level,
                delegation_policy=delegation_policy,
                history_mode=history_mode,
                hidden=hidden,
                aliases=aliases,
                examples=examples,
                sub_commands=sub_commands,
                linked_plugin=linked_plugin,
                clear_linked_plugin=clear_linked_plugin,
                availability=availability,
            )
        )

    async def delete_custom_group_command(
        self,
        event: AstrMessageEvent,
        group_name: str,
        command_type: str,
        trigger: str,
    ) -> str:
        """管理员按精确触发式删除单条目录命令；空分组仍保留。

        Args:
            group_name(string): 条目所在的目录组名称。
            command_type(string): 条目类型；``command`` 或 ``regex``。
            trigger(string): 要删除的精确触发式；普通命令传命令文本，正则传模式。
        """
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
        """优先按稳定自然键更新；index 仅兼容旧版同名编辑。"""
        data = await self._request_json_object()
        if isinstance(data, tuple):
            return data
        group_data = data.get("group")
        if not isinstance(group_data, dict):
            return jsonify({"success": False, "error": "Group is required"}), 400
        current_group_name = data.get("current_group_name")
        if not isinstance(current_group_name, str) or not current_group_name.strip():
            # 旧 UI 只传 index。为避免列表漂移覆盖其他组，仅允许提交名称仍
            # 唯一存在的同名更新；rename 必须提供打开面板时保存的稳定键。
            submitted_name = group_data.get("group_name")
            listed = await get_custom_group_service().list_groups(is_admin=True)
            if not listed["success"]:
                return self._web_response(listed)
            matches = [
                group
                for group in listed["groups"]
                if isinstance(submitted_name, str)
                and group["group_name"] == submitted_name.strip()
            ]
            if len(matches) != 1:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "rename 或位置漂移更新必须提供 current_group_name",
                        }
                    ),
                    400,
                )
            current_group_name = matches[0]["group_name"]
        response = await get_custom_group_service().replace_group(
            current_group_name.strip(),
            group_name=group_data.get("group_name", ""),
            description=group_data.get("description", ""),
            priority=group_data.get("priority", 0),
            hidden=group_data.get("hidden", False),
            commands=group_data["commands"] if "commands" in group_data else [],
        )
        return self._web_response(response)

    async def api_preview_delete_custom_group(self):
        """Web 删除也必须先预览并获得绑定当前内容的 token。"""
        data = await self._request_json_object()
        if isinstance(data, tuple):
            return data
        response = await get_custom_group_service().preview_delete_group(
            data.get("group_name", "")
        )
        return self._web_response(response)

    async def api_delete_custom_group(self):
        """使用 preview 返回的 confirmation_token 确认整组删除。"""
        data = await self._request_json_object()
        if isinstance(data, tuple):
            return data
        response = await get_custom_group_service().confirm_delete_group(
            data.get("group_name", ""), data.get("confirmation_token", "")
        )
        return self._web_response(response)

    async def api_get_commands(self):
        """分页读取 runtime/custom 统一命令目录。"""
        try:
            page = int(request.args.get("page", "1"))
            page_size = int(request.args.get("page_size", "20"))
            filters = {
                key: request.args[key]
                for key in (
                    "source_type",
                    "plugin",
                    "permission_level",
                    "delegation_policy",
                )
                if key in request.args
            }
            query = request.args.get("query")
            result = get_help_service().command_runtime.catalog_service.list_commands(
                page=page, page_size=page_size, filter=query or filters or None
            )
        except (TypeError, ValueError) as error:
            return jsonify({"success": False, "error": str(error)}), 400
        return jsonify({"success": True, **result})

    async def api_update_command_policy(self):
        """严格更新目录策略；数据库约束错误不得包装成成功。"""
        data = await self._request_json_object()
        if isinstance(data, tuple):
            return data
        command_id = data.get("command_id")
        if type(command_id) is not int:
            return jsonify({"success": False, "error": "command_id 必须是整数"}), 400
        service = get_help_service()
        try:
            command = service.command_runtime.catalog_service.update_command_policy(
                command_id,
                permission_level=data.get("permission_level"),
                delegation_policy=data.get("delegation_policy"),
                history_mode=data.get("history_mode"),
            )
        except KeyError:
            return jsonify({"success": False, "error": "Command not found"}), 404
        except ValueError as error:
            return jsonify({"success": False, "error": str(error)}), 400
        service.config.custom_groups = [
            CustomGroupConfig.model_validate(group)
            for group in service.command_runtime.catalog.list_custom_groups()
        ]
        service.command_index.update_config()
        # update_config 只替换配置快照；必须同时清除已经 warm 的内存与持久化
        # 索引，确保下一次普通搜索立刻按新权限重建。
        invalidate_command_cache()
        return jsonify({"success": True, "command": command})

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
                "permission_level": command["permission_level"],
                "delegation_policy": command["delegation_policy"],
                "history_mode": command["history_mode"],
                "hidden": command["hidden"],
                "linked_plugin": command.get("linked_plugin"),
                "availability": command.get("availability", "available"),
                "aliases": command.get("aliases", []),
                "examples": command.get("examples", []),
                "sub_commands": command.get("sub_commands", []),
            }
            if command["type"] == "regex":
                item.update(pattern=command["pattern"])
            else:
                item.update(command=command["command"])
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
            # 普通写入保留旧最小成功形状；preview 的 token 与摘要是两步删除
            # 必需字段，必须完整透传。
            payload: dict[str, Any] = {"success": True}
            if response.get("warnings"):
                payload["warnings"] = response["warnings"]
            if "delete_token" in response:
                payload["delete_token"] = response["delete_token"]
                payload["group"] = response.get("group")
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
