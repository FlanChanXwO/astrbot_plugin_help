"""跨用户命令委托的隐私、策略、回执和历史编排。"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from typing import Any


class DelegatedCommandService:
    """将一整条委托决策链收敛在单一应用服务。"""

    def __init__(
        self,
        *,
        runtime: Any,
        command_executor: Any,
        command_index: Any,
        config_getter: Callable[[], Any],
        prefixes_getter: Callable[[], list[str]],
        resolve_target: Callable[
            [Any, str], Awaitable[tuple[dict[str, object], str | None]]
        ],
        resolve_allowed_plugins: Callable[[Any], Awaitable[set[str] | None]],
        is_command_invokable: Callable[[str], bool],
    ) -> None:
        self.runtime = runtime
        self.command_executor = command_executor
        self.command_index = command_index
        self._config_getter = config_getter
        self._prefixes_getter = prefixes_getter
        self._resolve_target = resolve_target
        self._resolve_allowed_plugins = resolve_allowed_plugins
        self._is_command_invokable = is_command_invokable

    @staticmethod
    def _rejected(target: dict[str, object] | None, error: str) -> str:
        return json.dumps(
            {
                "success": False,
                "execution_state": "rejected",
                "receipt_id": None,
                "target": target,
                "dispatched": False,
                "output_complete": True,
                "retryable": False,
                "messages": [],
                "error": error,
            },
            ensure_ascii=False,
        )

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        """保留结构并把宿主对象转换为稳定 JSON，避免已调度回执滞留。"""
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._json_safe(item) for item in value]
        if hasattr(value, "__dict__"):
            fields = {
                name: getattr(value, name)
                for name in (
                    "handler_module_path",
                    "handler_name",
                    "handler_type",
                    "priority",
                    "description",
                    "command",
                )
                if hasattr(value, name)
            }
            return cls._json_safe(fields or {"value": str(value)})
        return str(value)

    def _dispatch_failure(
        self, receipt_id: str, error: BaseException, *, dispatched: bool = False
    ) -> str:
        try:
            failed = self.runtime.receipt_service.fail_or_release(
                receipt_id, error, dispatched=dispatched
            )
            return json.dumps(failed, ensure_ascii=False)
        except Exception as receipt_error:
            return json.dumps(
                {
                    "success": False,
                    "execution_state": "failed",
                    "receipt_id": receipt_id,
                    "target": {},
                    "dispatched": dispatched,
                    "output_complete": True,
                    "retryable": not dispatched,
                    "messages": [],
                    "error": f"{error}; receipt_fail_safe_error: {receipt_error}",
                },
                ensure_ascii=False,
            )

    async def execute(
        self,
        event: Any,
        command: str,
        actor: str = "user",
        result_mode: str = "auto",
        wait_seconds: float | None = None,
        target_user: str = "",
    ) -> str:
        """执行完整委托链；任何调度结论都先落回执，再处理历史。"""
        requester_id = str(event.get_sender_id())
        if actor != "self" and not target_user.strip():
            target_user = self.runtime.identity_service.infer_explicit_at_target(
                event, requester_id=requester_id
            )
        if actor == "self" and target_user.strip():
            return self._rejected(None, "actor=self 与 target_user 互斥")
        is_admin = bool(event.is_admin())
        config = self._config_getter()
        if target_user.strip() and is_admin and config.allow_admin_target_override:
            (
                target,
                target_id,
            ) = await self.runtime.identity_service.resolve_for_management(
                event, target_user, requester_id=requester_id
            )
        else:
            target, target_id = await self._resolve_target(event, target_user)
        if target_id is None:
            return self._rejected(target, "目标用户未唯一解析或不可操作")

        platform_id = str(event.get_platform_id())
        cross_user = target_id != requester_id
        command_record = self.runtime.find_command(command)
        if command_record is None:
            if cross_user:
                return self._rejected(target, "command_policy_unknown")
            policy = "normal"
        else:
            plugin = str(command_record.get("source_plugin") or "")
            # runtime 目录的 source_plugin 来自真实 handler，可在调度前拒绝；custom
            # 的插件关联仅是可编辑元数据，必须交给 executor 按实际非通用 handler
            # 检查，否则只由外部通用路由接管的命令会被误拦截。
            if (
                command_record.get("source_kind") == "runtime"
                and plugin
                and not self._is_command_invokable(plugin)
            ):
                return self._rejected(target, "命令所属插件在 AI 调用黑名单中")
            permission = str(command_record["permission_level"])
            policy = str(command_record["delegation_policy"])
            if permission == "admin" and not is_admin:
                return self._rejected(target, "原请求者没有管理员权限")
            if cross_user and policy == "forbidden":
                return self._rejected(target, "该命令禁止跨用户委托")
            if cross_user and policy == "sensitive":
                if not is_admin:
                    return self._rejected(target, "敏感委托要求原请求者为管理员")
                if not config.enable_sensitive_delegation:
                    return self._rejected(target, "敏感跨用户委托全局未启用")

        settings = self.runtime.identity_service.get_user_settings(
            platform_id, target_id
        )
        may_override = is_admin and config.allow_admin_target_override
        if not settings["allow_llm_operation"] and not may_override:
            return self._rejected(target, "目标用户已关闭全部 AI 命令代操作")
        if (
            cross_user
            and policy == "sensitive"
            and not settings["allow_sensitive_delegation"]
            and not may_override
        ):
            return self._rejected(target, "目标用户已关闭敏感命令代操作")

        reservation = self.runtime.receipt_service.reserve(
            platform_id=platform_id,
            session_id=str(event.unified_msg_origin),
            requester_user_id=requester_id,
            target_user_id=target_id,
            command=command,
            target=target,
        )
        if not reservation["reserved"]:
            return json.dumps(reservation, ensure_ascii=False)
        receipt_id = str(reservation["receipt_id"])
        try:
            allowed_plugins = await self._resolve_allowed_plugins(event)
            display_prefix = (
                self._prefixes_getter()[0] if self._prefixes_getter() else "/"
            )

            def suggestions(stripped: str, allowed: set[str] | None) -> list[str]:
                return [
                    item["command"]
                    if str(item["command"]).startswith(display_prefix)
                    else display_prefix + str(item["command"]).lstrip("/")
                    for item in self.command_index.search_commands(
                        stripped, limit=3, allowed_plugins=allowed
                    )
                ]

            raw_result = await self.command_executor.execute(
                event=event,
                command=command,
                allowed_plugins=allowed_plugins,
                search_suggestions_func=suggestions,
                actor=actor,
                result_mode=result_mode,
                wait_seconds=wait_seconds,
                target_user_id=target_id if cross_user else None,
                target_user_name=str(target.get("display_name") or target_id),
            )
        except Exception as error:
            return self._dispatch_failure(receipt_id, error)

        result = self._json_safe(raw_result)
        state = str(result.get("execution_state") or "failed")
        if state not in {
            "completed",
            "accepted",
            "external_dispatched",
            "rejected",
            "failed",
        }:
            state = "failed"
            result["error"] = result.get("error") or "命令调度器未确认启动"
        messages = [
            item if isinstance(item, dict) else {"text": str(item)}
            for item in result.get("messages", [])
        ]
        dispatched = bool(
            result.get(
                "dispatched",
                state in {"completed", "accepted", "external_dispatched"},
            )
        )
        # failed 是终态，但是否已经产生副作用只能以 executor 的调度事实为准。
        # 已派发失败不得重试；仅调度前失败释放下一次尝试机会。
        retryable = state == "failed" and not dispatched
        try:
            final = self.runtime.receipt_service.save_result(
                receipt_id,
                execution_state=state,
                dispatched=dispatched,
                output_complete=state in {"completed", "rejected", "failed"},
                retryable=retryable,
                messages=messages,
                error=(
                    str(result.get("error") or "命令执行失败")
                    if state in {"rejected", "failed"}
                    else None
                ),
                result=result,
            )
        except Exception as error:
            return self._dispatch_failure(receipt_id, error, dispatched=dispatched)

        if command_record and state in {"completed", "accepted", "external_dispatched"}:
            try:
                self.runtime.history_service.record_execution(
                    platform_id=platform_id,
                    target_user_id=target_id,
                    command_id=int(command_record["id"]),
                    command_key=str(command_record["command_key"]),
                    command_text=command,
                    execution_state=state,
                )
            except Exception as history_error:
                final.setdefault("warnings", []).append(
                    f"命令已确认，但历史记录失败: {history_error}"
                )
        return json.dumps(final, ensure_ascii=False, indent=2)
