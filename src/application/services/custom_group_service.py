"""自定义命令组的应用服务。

此模块只管理帮助目录数据，不创建或注册 AstrBot 命令处理器。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import re
import secrets
from collections.abc import Callable, Sequence
from typing import Any

from ...infrastructure.config.datamodels import CustomGroupCommand, CustomGroupConfig
from ...infrastructure.storage import CommandCatalog

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]
GroupsGetter = Callable[[], Sequence[CustomGroupConfig]]
GroupsSetter = Callable[[list[CustomGroupConfig]], None]
GroupsSaver = Callable[[list[CustomGroupConfig]], bool]
CommandFinder = Callable[[str], bool]
Invalidator = Callable[[], Any]

_custom_group_service_instance: CustomGroupService | None = None
_runtime_catalog: CommandCatalog | None = None
_MISSING = object()


class CustomGroupService:
    """为 AI tool 和 Web API 提供同一套自定义命令组管理用例。"""

    def __init__(
        self,
        *,
        get_groups: GroupsGetter | None = None,
        set_groups: GroupsSetter | None = None,
        save_groups: GroupsSaver | None = None,
        find_real_command: CommandFinder | None = None,
        invalidate_command_index: Invalidator | None = None,
        clear_runtime_cache: Invalidator | None = None,
        command_prefixes: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        """注入边界，使业务规则无需依赖 AstrBot 运行时即可测试。"""
        if get_groups is None or set_groups is None:
            from ...infrastructure.config import (
                get_config,
                update_custom_groups_in_config,
            )

            get_groups = get_groups or (lambda: get_config().custom_groups)
            set_groups = set_groups or update_custom_groups_in_config
        if save_groups is None:

            def save_to_catalog(groups: list[CustomGroupConfig]) -> bool:
                catalog = _runtime_catalog
                if catalog is None:
                    from ...infrastructure.utils.paths import get_data_dir

                    catalog = CommandCatalog(get_data_dir() / "command_catalog.db")
                    catalog.initialize()
                catalog.replace_all_custom_groups(
                    [group.model_dump(mode="json") for group in groups]
                )
                return True

            save_groups = save_to_catalog

        self._get_groups = get_groups
        self._set_groups = set_groups
        self._save_groups = save_groups
        self._find_real_command = find_real_command or self._find_real_command_default
        self._invalidate_command_index = (
            invalidate_command_index or self._invalidate_command_index_default
        )
        self._clear_runtime_cache = clear_runtime_cache or (lambda: None)
        self._command_prefixes = command_prefixes or self._command_prefixes_default
        self._lock = asyncio.Lock()
        # token 只保存在本服务实例中，因此配置重载的新实例和插件重启均会失效。
        self._delete_tokens: dict[str, tuple[str, str]] = {}
        self._latest_delete_tokens: dict[str, str] = {}

    async def list_groups(self, is_admin: bool) -> JsonDict:
        """返回按调用者权限裁剪后的自定义命令组视图。"""
        groups: list[JsonDict] = []
        for group in self._get_groups():
            if not is_admin and group.hidden:
                continue
            item = group.model_dump(mode="json")
            if not is_admin:
                item["commands"] = [
                    command.model_dump(mode="json")
                    for command in group.commands
                    if not command.hidden and not command.is_admin
                ]
            groups.append(item)
        return self._success(
            "已获取自定义命令组",
            groups=groups,
            filtered=not is_admin,
        )

    async def create_group(
        self,
        group_name: str,
        description: str = "",
        priority: int = 0,
        hidden: bool = False,
    ) -> JsonDict:
        """创建允许为空的命令组。"""
        normalized_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_name, dict):
            return normalized_name
        invalid_description = self._validate_text(description, "description")
        if invalid_description is not None:
            return invalid_description
        invalid_priority = self._validate_priority(priority)
        if invalid_priority is not None:
            return invalid_priority
        invalid_hidden = self._validate_bool(hidden, "hidden")
        if invalid_hidden is not None:
            return invalid_hidden
        async with self._lock:
            groups = self._copy_groups()
            if self._find_group_index(groups, normalized_name) is not None:
                return self._error(
                    "group_already_exists", f"分组 '{normalized_name}' 已存在"
                )
            group = CustomGroupConfig(
                group_name=normalized_name,
                description=description,
                priority=priority,
                hidden=hidden,
            )
            groups.append(group)
            return await self._commit(groups, "已创建自定义命令组", group)

    async def create_group_with_commands(
        self,
        group_name: str,
        *,
        description: str = "",
        priority: int = 0,
        hidden: bool = False,
        commands: Any = _MISSING,
    ) -> JsonDict:
        """原子创建整组目录，供兼容 Web API 提交完整表单。"""
        normalized_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_name, dict):
            return normalized_name
        group_error = self._validate_group_fields(description, priority, hidden)
        if group_error is not None:
            return group_error
        # 仅省略字段时才兼容旧 WebUI 的空命令组；显式 null/false/""
        # 都是客户端参数错误，不能被 ``or []`` 静默转换成空列表。
        prepared_commands = self._prepare_group_commands(
            [] if commands is _MISSING else commands
        )
        if isinstance(prepared_commands, dict):
            return prepared_commands
        async with self._lock:
            groups = self._copy_groups()
            if self._find_group_index(groups, normalized_name) is not None:
                return self._error(
                    "group_already_exists", f"分组 '{normalized_name}' 已存在"
                )
            group = CustomGroupConfig(
                group_name=normalized_name,
                description=description,
                priority=priority,
                hidden=hidden,
                commands=prepared_commands,
            )
            groups.append(group)
            return await self._commit(groups, "已创建自定义命令组", group)

    async def replace_group(
        self,
        current_group_name: str,
        *,
        group_name: str,
        description: str = "",
        priority: int = 0,
        hidden: bool = False,
        commands: Any = _MISSING,
    ) -> JsonDict:
        """按自然键原子替换整组内容，避免 Web 编辑产生半写入状态。"""
        normalized_current_name = self._required_text(
            current_group_name, "current_group_name"
        )
        if isinstance(normalized_current_name, dict):
            return normalized_current_name
        normalized_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_name, dict):
            return normalized_name
        group_error = self._validate_group_fields(description, priority, hidden)
        if group_error is not None:
            return group_error
        # 见 create_group_with_commands：只有字段缺失允许按空列表处理。
        prepared_commands = self._prepare_group_commands(
            [] if commands is _MISSING else commands
        )
        if isinstance(prepared_commands, dict):
            return prepared_commands
        async with self._lock:
            groups = self._copy_groups()
            index = self._find_group_index(groups, normalized_current_name)
            if index is None:
                return self._error(
                    "group_not_found", f"未找到分组 '{normalized_current_name}'"
                )
            duplicate_index = self._find_group_index(groups, normalized_name)
            if duplicate_index is not None and duplicate_index != index:
                return self._error(
                    "group_already_exists", f"分组 '{normalized_name}' 已存在"
                )
            previous_group_name = groups[index].group_name
            group = CustomGroupConfig(
                group_name=normalized_name,
                description=description,
                priority=priority,
                hidden=hidden,
                commands=prepared_commands,
            )
            groups[index] = group
            return await self._commit(
                groups,
                "已更新自定义命令组",
                group,
                clear_delete_tokens={previous_group_name, group.group_name},
            )

    async def update_group(
        self,
        group_name: str,
        *,
        new_group_name: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        hidden: bool | None = None,
    ) -> JsonDict:
        """更新分组的显式字段；``None`` 表示保持原值。"""
        normalized_group_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_group_name, dict):
            return normalized_group_name
        if new_group_name is not None:
            normalized_new_group_name = self._required_text(
                new_group_name, "new_group_name"
            )
            if isinstance(normalized_new_group_name, dict):
                return normalized_new_group_name
        else:
            normalized_new_group_name = None
        if description is not None:
            invalid_description = self._validate_text(description, "description")
            if invalid_description is not None:
                return invalid_description
        if priority is not None:
            invalid_priority = self._validate_priority(priority)
            if invalid_priority is not None:
                return invalid_priority
        if hidden is not None:
            invalid_hidden = self._validate_bool(hidden, "hidden")
            if invalid_hidden is not None:
                return invalid_hidden
        async with self._lock:
            groups = self._copy_groups()
            index = self._find_group_index(groups, normalized_group_name)
            if index is None:
                return self._error(
                    "group_not_found", f"未找到分组 '{normalized_group_name}'"
                )
            group = groups[index]
            previous_group_name = group.group_name
            if normalized_new_group_name is not None:
                existing_index = self._find_group_index(
                    groups, normalized_new_group_name
                )
                if existing_index is not None and existing_index != index:
                    return self._error(
                        "group_already_exists",
                        f"分组 '{normalized_new_group_name}' 已存在",
                    )
                group.group_name = normalized_new_group_name
            if description is not None:
                group.description = description
            if priority is not None:
                group.priority = priority
            if hidden is not None:
                group.hidden = hidden
            return await self._commit(
                groups,
                "已更新自定义命令组",
                group,
                clear_delete_tokens={previous_group_name, group.group_name},
            )

    async def add_command(
        self,
        group_name: str,
        command_type: str,
        *,
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
    ) -> JsonDict:
        """向分组添加目录条目，不注册任何动态命令处理器。"""
        normalized_group_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_group_name, dict):
            return normalized_group_name
        async with self._lock:
            groups = self._copy_groups()
            group_index = self._find_group_index(groups, normalized_group_name)
            if group_index is None:
                return self._error(
                    "group_not_found", f"未找到分组 '{normalized_group_name}'"
                )
            prepared = self._prepare_command(
                command_type=command_type,
                command=command,
                pattern=pattern,
                description=description,
                is_admin=is_admin,
                permission_level=permission_level,
                delegation_policy=delegation_policy,
                history_mode=history_mode,
                hidden=hidden,
                aliases=[] if aliases is None else aliases,
                examples=[] if examples is None else examples,
                sub_commands=[] if sub_commands is None else sub_commands,
                linked_plugin=linked_plugin,
                availability=availability,
                allow_legacy_primary_empty=False,
            )
            if isinstance(prepared, dict):
                return prepared
            conflict = self._find_trigger_conflict(groups[group_index], prepared)
            if conflict is not None:
                return self._error(
                    "trigger_conflict", f"触发式 '{conflict}' 在该分组已存在"
                )
            groups[group_index].commands.append(prepared)
            verified, warning = self._verify_command(prepared)
            response = await self._commit(
                groups,
                "已添加自定义命令目录",
                groups[group_index],
                clear_delete_tokens={groups[group_index].group_name},
            )
            response["verified"] = verified
            if warning:
                response["warnings"].append(warning)
            return response

    async def update_command(
        self,
        group_name: str,
        command_type: str,
        current_trigger: str,
        *,
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
    ) -> JsonDict:
        """按自然键更新条目；插件关联只能由专用字段显式清除。"""
        normalized_group_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_group_name, dict):
            return normalized_group_name
        invalid_clear_linked_plugin = self._validate_bool(
            clear_linked_plugin, "clear_linked_plugin"
        )
        if invalid_clear_linked_plugin is not None:
            return invalid_clear_linked_plugin
        if clear_linked_plugin and linked_plugin is not None:
            return self._error(
                "linked_plugin_clear_conflict",
                "clear_linked_plugin=true 与 linked_plugin 互斥",
            )
        if clear_linked_plugin and availability == "missing_plugin":
            return self._error(
                "linked_plugin_clear_conflict",
                "清除插件关联时 availability 不能为 missing_plugin",
            )
        async with self._lock:
            groups = self._copy_groups()
            group_index = self._find_group_index(groups, normalized_group_name)
            if group_index is None:
                return self._error(
                    "group_not_found", f"未找到分组 '{normalized_group_name}'"
                )
            group = groups[group_index]
            command_index, locate_error = self._find_command_index(
                group, command_type, current_trigger
            )
            if locate_error:
                return locate_error
            assert command_index is not None
            existing = group.commands[command_index]
            final_permission = permission_level
            if final_permission is None:
                final_permission = (
                    "admin"
                    if is_admin is True
                    else "normal"
                    if is_admin is False
                    else existing.permission_level
                )
            final_is_admin = (
                is_admin if is_admin is not None else final_permission == "admin"
            )
            final_delegation = delegation_policy
            if final_delegation is None:
                final_delegation = existing.delegation_policy
                if final_permission == "admin" and final_delegation == "normal":
                    final_delegation = "sensitive"
            prepared = self._prepare_command(
                command_type=command_type,
                command=existing.command if command is None else command,
                pattern=existing.pattern if pattern is None else pattern,
                description=existing.description
                if description is None
                else description,
                is_admin=final_is_admin,
                permission_level=final_permission,
                delegation_policy=final_delegation,
                history_mode=(
                    existing.history_mode if history_mode is None else history_mode
                ),
                hidden=existing.hidden if hidden is None else hidden,
                aliases=list(existing.aliases) if aliases is None else aliases,
                examples=list(existing.examples) if examples is None else examples,
                sub_commands=(
                    list(existing.sub_commands)
                    if sub_commands is None
                    else sub_commands
                ),
                linked_plugin=(
                    None
                    if clear_linked_plugin
                    else existing.linked_plugin
                    if linked_plugin is None
                    else linked_plugin
                ),
                availability=(
                    "available"
                    if clear_linked_plugin
                    else existing.availability
                    if availability is None
                    else availability
                ),
                allow_legacy_primary_empty=True,
            )
            if isinstance(prepared, dict):
                return prepared
            remaining = CustomGroupConfig(
                group_name=group.group_name,
                description=group.description,
                priority=group.priority,
                hidden=group.hidden,
                commands=[
                    item
                    for index, item in enumerate(group.commands)
                    if index != command_index
                ],
            )
            conflict = self._find_trigger_conflict(remaining, prepared)
            if conflict is not None:
                return self._error(
                    "trigger_conflict", f"触发式 '{conflict}' 在该分组已存在"
                )
            group.commands[command_index] = prepared
            verified, warning = self._verify_command(prepared)
            response = await self._commit(
                groups,
                "已更新自定义命令目录",
                group,
                clear_delete_tokens={group.group_name},
            )
            response["verified"] = verified
            if warning:
                response["warnings"].append(warning)
            return response

    async def delete_command(
        self, group_name: str, command_type: str, trigger: str
    ) -> JsonDict:
        """删除一条目录条目，空分组仍然保留。"""
        normalized_group_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_group_name, dict):
            return normalized_group_name
        async with self._lock:
            groups = self._copy_groups()
            group_index = self._find_group_index(groups, normalized_group_name)
            if group_index is None:
                return self._error(
                    "group_not_found", f"未找到分组 '{normalized_group_name}'"
                )
            group = groups[group_index]
            command_index, locate_error = self._find_command_index(
                group, command_type, trigger
            )
            if locate_error:
                return locate_error
            assert command_index is not None
            del group.commands[command_index]
            return await self._commit(
                groups,
                "已删除自定义命令目录",
                group,
                clear_delete_tokens={group.group_name},
            )

    async def preview_delete_group(self, group_name: str) -> JsonDict:
        """生成绑定当前分组完整内容的一次性删除确认 token。"""
        normalized_group_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_group_name, dict):
            return normalized_group_name
        async with self._lock:
            groups = self._copy_groups()
            group_index = self._find_group_index(groups, normalized_group_name)
            if group_index is None:
                return self._error(
                    "group_not_found", f"未找到分组 '{normalized_group_name}'"
                )
            group = groups[group_index]
            self._clear_group_delete_tokens({group.group_name})
            token = secrets.token_urlsafe(24)
            self._delete_tokens[token] = (
                group.group_name,
                self._group_signature(group),
            )
            self._latest_delete_tokens[group.group_name] = token
            return self._success(
                "删除预览已创建，请使用确认 token 删除整个分组",
                group=group.model_dump(mode="json"),
                delete_token=token,
            )

    async def confirm_delete_group(self, group_name: str, token: str) -> JsonDict:
        """确认并删除整个分组；token 只能成功使用一次。"""
        normalized_group_name = self._required_text(group_name, "group_name")
        if isinstance(normalized_group_name, dict):
            return normalized_group_name
        normalized_token = self._required_text(token, "delete_token")
        if isinstance(normalized_token, dict):
            return normalized_token
        async with self._lock:
            token_binding = self._delete_tokens.pop(normalized_token, None)
            if token_binding is None:
                return self._error(
                    "invalid_delete_token", "删除确认 token 无效或已使用"
                )
            expected_name, expected_signature = token_binding
            self._clear_group_delete_tokens({expected_name, normalized_group_name})
            groups = self._copy_groups()
            group_index = self._find_group_index(groups, normalized_group_name)
            if group_index is None:
                return self._error(
                    "group_not_found", f"未找到分组 '{normalized_group_name}'"
                )
            group = groups[group_index]
            if (
                group.group_name != expected_name
                or self._group_signature(group) != expected_signature
            ):
                return self._error(
                    "stale_delete_token", "分组内容已变化，请重新预览删除"
                )
            deleted_group = group.model_dump(mode="json")
            del groups[group_index]
            return await self._commit(
                groups,
                "已删除自定义命令组",
                deleted_group,
                clear_delete_tokens={group.group_name},
            )

    def _copy_groups(self) -> list[CustomGroupConfig]:
        """创建完整候选副本，持久化成功前绝不改变当前内存对象。"""
        return [group.model_copy(deep=True) for group in self._get_groups()]

    async def _commit(
        self,
        candidate_groups: list[CustomGroupConfig],
        message: str,
        group: CustomGroupConfig | JsonDict,
        clear_delete_tokens: set[str] | None = None,
    ) -> JsonDict:
        """先原子保存，再更新内存并尽力失效运行态缓存。"""
        try:
            saved = self._save_groups(candidate_groups)
            if inspect.isawaitable(saved):
                saved = await saved
        except Exception as exc:
            logger.exception("保存自定义命令组时发生异常")
            return self._error("persistence_failed", f"保存自定义命令组失败: {exc}")
        if not saved:
            return self._error("persistence_failed", "保存自定义命令组失败")

        if clear_delete_tokens:
            self._clear_group_delete_tokens(clear_delete_tokens)
        response = self._success(message, group=self._as_group_dict(group))
        try:
            result = self._set_groups(candidate_groups)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.exception("自定义命令组已保存，但更新内存配置失败")
            response["warnings"].append(f"持久化已成功，但更新内存配置失败: {exc}")
            return response

        for label, invalidator in (
            ("命令索引", self._invalidate_command_index),
            ("运行态缓存", self._clear_runtime_cache),
        ):
            try:
                result = invalidator()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.exception("自定义命令组已保存，但%s失效失败", label)
                response["warnings"].append(f"已保存，但{label}失效失败: {exc}")
        return response

    def _validate_group_fields(
        self, description: Any, priority: Any, hidden: Any
    ) -> JsonDict | None:
        """统一校验整组写入字段，避免 Web 与 AI 接口规则分叉。"""
        invalid_description = self._validate_text(description, "description")
        if invalid_description is not None:
            return invalid_description
        invalid_priority = self._validate_priority(priority)
        if invalid_priority is not None:
            return invalid_priority
        return self._validate_bool(hidden, "hidden")

    def _prepare_group_commands(
        self, commands: Any
    ) -> list[CustomGroupCommand] | JsonDict:
        """将 Web 的扁平命令对象完整校验后转换为候选数据。"""
        if not isinstance(commands, list):
            return self._error("invalid_commands", "commands 必须是对象列表")

        prepared_commands: list[CustomGroupCommand] = []
        candidate_group = CustomGroupConfig(group_name="候选", commands=[])
        for raw_command in commands:
            if not isinstance(raw_command, dict):
                return self._error("invalid_command", "commands 中的每项必须是对象")
            command_type = raw_command.get("type", "command")
            prepared = self._prepare_command(
                command_type=command_type,
                command=raw_command.get("command"),
                pattern=raw_command.get("pattern"),
                description=raw_command.get("description", ""),
                is_admin=raw_command.get("is_admin"),
                permission_level=raw_command.get("permission_level"),
                delegation_policy=raw_command.get("delegation_policy"),
                history_mode=raw_command.get("history_mode", "command"),
                hidden=raw_command.get("hidden", False),
                aliases=raw_command.get("aliases", []),
                examples=raw_command.get("examples", []),
                sub_commands=raw_command.get("sub_commands", []),
                linked_plugin=raw_command.get("linked_plugin"),
                availability=raw_command.get("availability", "available"),
                # 兼容旧 Web API：普通目录条目可仅使用 aliases。
                allow_legacy_primary_empty=True,
            )
            if isinstance(prepared, dict):
                return prepared
            conflict = self._find_trigger_conflict(candidate_group, prepared)
            if conflict is not None:
                return self._error(
                    "trigger_conflict", f"触发式 '{conflict}' 在该分组已存在"
                )
            candidate_group.commands.append(prepared)
            prepared_commands.append(prepared)
        return prepared_commands

    def _prepare_command(
        self,
        *,
        command_type: str,
        command: str | None,
        pattern: str | None,
        description: str,
        is_admin: bool | None,
        permission_level: str | None,
        delegation_policy: str | None,
        history_mode: str,
        hidden: bool,
        aliases: list[str],
        examples: list[str],
        sub_commands: list[str],
        linked_plugin: str | None,
        availability: str,
        allow_legacy_primary_empty: bool,
    ) -> CustomGroupCommand | JsonDict:
        if not isinstance(command_type, str) or command_type not in {
            "command",
            "regex",
        }:
            return self._error(
                "invalid_command_type", "command_type 必须为 command 或 regex"
            )
        invalid_description = self._validate_text(description, "description")
        if invalid_description is not None:
            return invalid_description
        if is_admin is not None:
            invalid_is_admin = self._validate_bool(is_admin, "is_admin")
            if invalid_is_admin is not None:
                return invalid_is_admin
        if permission_level is None:
            permission_level = "admin" if is_admin is True else "normal"
        elif permission_level not in {"normal", "admin"}:
            return self._error(
                "invalid_permission_level",
                "permission_level 必须为 normal 或 admin",
            )
        if is_admin is not None and is_admin != (permission_level == "admin"):
            return self._error(
                "inconsistent_permission",
                "is_admin 与 permission_level 不一致",
            )
        is_admin = permission_level == "admin"
        if delegation_policy is None:
            delegation_policy = "sensitive" if permission_level == "admin" else "normal"
        if delegation_policy not in {"normal", "sensitive", "forbidden"}:
            return self._error(
                "invalid_delegation_policy",
                "delegation_policy 必须为 normal、sensitive 或 forbidden",
            )
        if permission_level == "admin" and delegation_policy == "normal":
            return self._error(
                "unsafe_delegation_policy",
                "管理员命令 delegation_policy 至少为 sensitive",
            )
        if history_mode not in {"none", "command", "full"}:
            return self._error(
                "invalid_history_mode", "history_mode 必须为 none、command 或 full"
            )
        if delegation_policy in {"sensitive", "forbidden"} and history_mode == "full":
            return self._error(
                "unsafe_history_mode", "敏感或禁止委托命令不能记录完整参数"
            )
        if linked_plugin is not None and (
            not isinstance(linked_plugin, str) or not linked_plugin.strip()
        ):
            return self._error(
                "invalid_linked_plugin", "linked_plugin 必须是非空字符串或 null"
            )
        if availability not in {"available", "missing_plugin"}:
            return self._error(
                "invalid_availability",
                "availability 必须为 available 或 missing_plugin",
            )
        invalid_hidden = self._validate_bool(hidden, "hidden")
        if invalid_hidden is not None:
            return invalid_hidden
        invalid_examples = self._validate_string_list(examples, "examples")
        if invalid_examples is not None:
            return invalid_examples
        invalid_sub_commands = self._validate_string_list(sub_commands, "sub_commands")
        if invalid_sub_commands is not None:
            return invalid_sub_commands
        normalized_aliases = self._normalize_aliases(aliases)
        if normalized_aliases is None:
            return self._error("invalid_alias", "aliases 必须是非空字符串列表")
        if len(self._trigger_keys(normalized_aliases, command_type)) != len(
            normalized_aliases
        ):
            return self._error("duplicate_alias", "aliases 不能包含重复触发式")

        normalized_command = ""
        normalized_pattern = ""
        if command_type == "command":
            if command is not None and not isinstance(command, str):
                return self._error("invalid_command", "command 必须是字符串")
            normalized_command = (command or "").strip()
            if not normalized_command and not (
                allow_legacy_primary_empty and normalized_aliases
            ):
                return self._error("invalid_command", "普通命令必须提供非空 command")
        else:
            if pattern is not None and not isinstance(pattern, str):
                return self._error("invalid_pattern", "pattern 必须是字符串")
            # 正则的空白可能具有语义，写入与自然键均保留调用方原文。
            normalized_pattern = pattern or ""
            if normalized_pattern == "":
                return self._error("invalid_pattern", "正则命令必须提供非空 pattern")
            try:
                compiled_pattern = re.compile(normalized_pattern, re.IGNORECASE)
            except re.error as exc:
                return self._error("invalid_pattern", f"正则 pattern 无法编译: {exc}")
            for example in examples:
                runtime_example = example.lower().strip()
                if not compiled_pattern.search(runtime_example):
                    return self._error(
                        "regex_example_mismatch",
                        f"示例 '{example}' 不匹配正则 pattern",
                    )

        primary_trigger = (
            normalized_command if command_type == "command" else normalized_pattern
        )
        triggers = ([primary_trigger] if primary_trigger else []) + normalized_aliases
        if len(self._trigger_keys(triggers, command_type)) != len(triggers):
            return self._error("trigger_conflict", "command 与 aliases 不能重复")
        return CustomGroupCommand(
            command=normalized_command,
            type=command_type,
            description=description,
            is_admin=is_admin,
            permission_level=permission_level,
            delegation_policy=delegation_policy,
            history_mode=history_mode,
            hidden=hidden,
            aliases=normalized_aliases,
            pattern=normalized_pattern,
            examples=list(examples),
            sub_commands=list(sub_commands),
            linked_plugin=linked_plugin.strip() if linked_plugin else None,
            availability=availability,
        )

    def _find_command_index(
        self, group: CustomGroupConfig, command_type: str, trigger: str
    ) -> tuple[int | None, JsonDict | None]:
        if not isinstance(command_type, str) or command_type not in {
            "command",
            "regex",
        }:
            return None, self._error(
                "invalid_command_type", "command_type 必须为 command 或 regex"
            )
        if command_type == "regex":
            if not isinstance(trigger, str) or trigger == "":
                return None, self._error("invalid_trigger", "trigger 必须是非空字符串")
            normalized_trigger = trigger
        else:
            normalized_trigger = self._required_text(trigger, "trigger")
            if isinstance(normalized_trigger, dict):
                return None, normalized_trigger
        trigger_key = self._trigger_key(normalized_trigger, command_type)
        matches = []
        for index, item in enumerate(group.commands):
            if item.type != command_type:
                continue
            primary = item.command if command_type == "command" else item.pattern
            item_keys = self._trigger_keys([primary, *item.aliases], command_type)
            if trigger_key in item_keys:
                matches.append(index)
        if not matches:
            return None, self._error(
                "command_not_found", f"未找到触发式 '{normalized_trigger}'"
            )
        if len(matches) > 1:
            return None, self._error(
                "ambiguous_trigger", f"触发式 '{normalized_trigger}' 匹配多个旧目录条目"
            )
        return matches[0], None

    def _find_trigger_conflict(
        self, group: CustomGroupConfig, command: CustomGroupCommand
    ) -> str | None:
        new_triggers = self._trigger_keys(self._command_triggers(command), command.type)
        for existing in group.commands:
            existing_triggers = self._command_triggers(existing)
            existing_keys = self._trigger_keys(existing_triggers, existing.type)
            overlap = new_triggers.intersection(existing_keys)
            if overlap:
                matching_key = sorted(overlap)[0]
                return next(
                    trigger
                    for trigger in existing_triggers
                    if self._trigger_key(trigger, existing.type) == matching_key
                )
        return None

    @staticmethod
    def _command_triggers(command: CustomGroupCommand) -> list[str]:
        primary = command.command if command.type == "command" else command.pattern
        triggers = [primary, *command.aliases]
        if command.type == "regex":
            return [trigger for trigger in triggers if trigger != ""]
        return [trigger for trigger in triggers if trigger.strip()]

    def _trigger_keys(self, triggers: Sequence[str], command_type: str) -> set[str]:
        return {self._trigger_key(trigger, command_type) for trigger in triggers}

    def _trigger_key(self, trigger: str, command_type: str) -> str:
        """返回与运行时查询一致的自然键；正则保留原始语义。"""
        if command_type != "command":
            return trigger

        normalized = trigger.strip()

        prefixes = {"/"}
        prefixes.update(
            prefix
            for prefix in self._command_prefixes()
            if isinstance(prefix, str) and prefix
        )
        ordered_prefixes = sorted(prefixes, key=len, reverse=True)
        while True:
            prefix = next(
                (item for item in ordered_prefixes if normalized.startswith(item)),
                None,
            )
            if prefix is None:
                return normalized.casefold()
            normalized = normalized[len(prefix) :]

    def _verify_command(self, command: CustomGroupCommand) -> tuple[bool, str | None]:
        if command.type != "command" or not command.command:
            return True, None
        try:
            if self._find_real_command(command.command):
                return True, None
        except Exception as exc:
            logger.exception("验证真实命令索引失败")
            return False, f"未能验证真实命令是否存在: {exc}"
        return False, f"未在真实命令索引中找到 '{command.command}'；目录项仍已保存"

    @classmethod
    def _required_text(cls, value: Any, field: str) -> str | JsonDict:
        """校验必填文本，避免 Pydantic 将错误输入悄然转换。"""
        normalized = value.strip() if isinstance(value, str) else ""
        if normalized:
            return normalized
        return cls._error(f"invalid_{field}", f"{field} 必须是非空字符串")

    @classmethod
    def _validate_text(cls, value: Any, field: str) -> JsonDict | None:
        """校验可为空的文本字段。"""
        if isinstance(value, str):
            return None
        return cls._error(f"invalid_{field}", f"{field} 必须是字符串")

    @classmethod
    def _validate_priority(cls, value: Any) -> JsonDict | None:
        """优先级必须是实际的 int，不能接受 bool 或字符串转换。"""
        if type(value) is int:
            return None
        return cls._error("invalid_priority", "priority 必须是整数")

    @classmethod
    def _validate_bool(cls, value: Any, field: str) -> JsonDict | None:
        """布尔字段拒绝 Python/Pydantic 的隐式真值转换。"""
        if type(value) is bool:
            return None
        return cls._error(f"invalid_{field}", f"{field} 必须是布尔值")

    @classmethod
    def _validate_string_list(cls, value: Any, field: str) -> JsonDict | None:
        """校验列表容器和元素，避免模型层抛出非 JSON 异常。"""
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return None
        return cls._error(f"invalid_{field}", f"{field} 必须是字符串列表")

    def _clear_group_delete_tokens(self, group_names: set[str]) -> None:
        """清除指定分组的全部确认状态，不以时间窗口淘汰 token。"""
        normalized_names = {name.strip() for name in group_names}
        tokens = [
            token
            for token, (group_name, _) in self._delete_tokens.items()
            if group_name.strip() in normalized_names
        ]
        for token in tokens:
            del self._delete_tokens[token]
        for group_name in normalized_names:
            self._latest_delete_tokens.pop(group_name, None)

    @staticmethod
    def _normalize_aliases(aliases: list[str]) -> list[str] | None:
        if not isinstance(aliases, list):
            return None
        result = []
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                return None
            result.append(alias.strip())
        return result

    @staticmethod
    def _find_group_index(
        groups: Sequence[CustomGroupConfig], group_name: str
    ) -> int | None:
        normalized_name = group_name.strip() if isinstance(group_name, str) else ""
        for index, group in enumerate(groups):
            if group.group_name.strip() == normalized_name:
                return index
        return None

    @staticmethod
    def _group_signature(group: CustomGroupConfig) -> str:
        material = json.dumps(
            group.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _as_group_dict(group: CustomGroupConfig | JsonDict) -> JsonDict:
        return (
            group.model_dump(mode="json")
            if isinstance(group, CustomGroupConfig)
            else copy.deepcopy(group)
        )

    @staticmethod
    def _success(message: str, **fields: Any) -> JsonDict:
        return {
            "success": True,
            "error": None,
            "message": message,
            "warnings": [],
            **fields,
        }

    @staticmethod
    def _error(error: str, message: str) -> JsonDict:
        return {
            "success": False,
            "error": error,
            "message": message,
            "warnings": [],
        }

    def _find_real_command_default(self, command: str) -> bool:
        from ...infrastructure.analysis import get_command_index

        entries = get_command_index().get_all_commands()
        candidates = {command.strip()}
        for prefix in self._command_prefixes():
            candidates.add(f"{prefix}{command.strip()}")
        return any(
            key in candidates and not value.get("custom_groups")
            for key, value in entries.items()
        )

    @staticmethod
    def _command_prefixes_default() -> Sequence[str]:
        from ...infrastructure.analysis import get_command_index

        return get_command_index().prefixes

    @staticmethod
    def _invalidate_command_index_default() -> None:
        from ...infrastructure.analysis import (
            get_command_index,
            invalidate_command_cache,
        )

        # 先同步新持久化的分组快照，再清除索引与持久化缓存。
        get_command_index().update_config()
        invalidate_command_cache()


def get_custom_group_service() -> CustomGroupService:
    """获取共享服务实例，使 AI 删除 token 可由 Web/AI 后续调用继续使用。"""
    global _custom_group_service_instance
    if _custom_group_service_instance is None:
        _custom_group_service_instance = CustomGroupService()
    return _custom_group_service_instance


def reset_custom_group_service() -> None:
    """重置共享服务实例，供测试与插件运行时重建使用。"""
    global _custom_group_service_instance
    _custom_group_service_instance = None


def bind_custom_group_catalog(catalog: CommandCatalog) -> None:
    """绑定当前插件运行时目录，并让后续 AI/Web CRUD 共享同一权威源。"""
    global _runtime_catalog
    _runtime_catalog = catalog
    reset_custom_group_service()
