"""命令执行器 - 单例模式"""

from __future__ import annotations

import asyncio
import copy
import inspect
import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.pipeline.context import PipelineContext
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage

from ...infrastructure.config import get_config
from ..context_holder import get_context
from ..utils.logger import get_logger
from .command_index import get_command_index

if TYPE_CHECKING:
    pass

logger = get_logger()


@dataclass
class _CommandTaskState:
    """仅保存本次 synthetic event 的后台执行状态与可归因输出。"""

    started: asyncio.Event = field(default_factory=asyncio.Event)
    messages: list[str] = field(default_factory=list)
    error: Exception | None = None
    scheduler_initialized: bool = False
    streaming_output_ids: set[int] = field(default_factory=set)


class CommandExecutor:
    """命令执行器，用于 AI 代理执行 AstrBot 命令"""

    def __init__(self):
        self.context = get_context()
        self.cfg = get_config()
        self.command_index = get_command_index()
        self.prefixes: list[str] = ["/"]
        self._background_tasks: set[asyncio.Task] = set()

    def update_prefixes(self, prefixes: list[str]) -> None:
        """更新命令前缀"""
        self.prefixes = prefixes

    def _build_command_event(
        self, event: AstrMessageEvent, command_text: str, actor: str = "user"
    ) -> AstrMessageEvent:
        """构建命令执行事件"""
        command_event = copy.copy(event)
        command_event.message_obj = copy.copy(event.message_obj)
        command_event.message_str = command_text
        command_event.message_obj.message_str = command_text
        command_event.message_obj.message = [Plain(command_text)]

        sender = copy.copy(getattr(event.message_obj, "sender", None))
        if sender is None:
            sender = SimpleNamespace(
                user_id=event.get_sender_id(),
                nickname=(
                    event.get_sender_name()
                    if hasattr(event, "get_sender_name")
                    else None
                ),
            )
        if actor == "self":
            sender.user_id = event.get_self_id()
            if not getattr(sender, "nickname", None):
                sender.nickname = "AstrBot"
            # self 身份只影响权限/过滤判断；实际消息仍投递到当前会话。
            command_event.send = event.send
            if hasattr(event, "send_streaming"):
                command_event.send_streaming = event.send_streaming
            if hasattr(event, "send_typing"):
                command_event.send_typing = event.send_typing
            if hasattr(event, "stop_typing"):
                command_event.stop_typing = event.stop_typing
        command_event.message_obj.sender = sender

        command_event.is_wake = False
        command_event.is_at_or_wake_command = False
        command_event.role = "member"
        # 后台只委托执行命令处理器，不在命令结束后继续触发默认 LLM 对话。
        command_event.call_llm = True
        command_event.plugins_name = None
        command_event._force_stopped = False
        command_event._has_send_oper = False
        command_event.clear_result()
        if hasattr(command_event, "_extras"):
            command_event._extras = {}
        else:
            command_event.clear_extra()
        self._bind_command_event_sender(command_event, event)
        return command_event

    def _bind_command_event_sender(
        self, command_event: AstrMessageEvent, source_event: AstrMessageEvent
    ) -> None:
        """为委托事件绑定可用发送器，避免 tool 事件缺少 send 时打断调度。"""
        source_send = getattr(source_event, "send", None)
        if callable(source_send):
            command_event.send = source_send
            return

        async def send_via_context(result) -> bool:
            """回退到 AstrBot 主动发送接口，保持命令输出仍回到原会话。"""
            chain = getattr(result, "chain", None)
            if chain is None:
                chain = result
            return await self.context.send_message(
                source_event.unified_msg_origin,
                chain,
            )

        command_event.send = send_via_context

    def _make_pipeline_context(
        self, event: AstrMessageEvent, allow_bot_self_message: bool = False
    ) -> PipelineContext:
        """创建管道上下文"""
        astrbot_config = self.context.get_config(umo=event.unified_msg_origin)
        if allow_bot_self_message:
            astrbot_config = self._copy_config_for_self_actor(astrbot_config)
        return PipelineContext(
            astrbot_config=astrbot_config,
            plugin_manager=getattr(self.context, "_star_manager", None),
            astrbot_config_id=event.unified_msg_origin,
        )

    def _copy_config_for_self_actor(self, astrbot_config: dict) -> dict:
        """复制 self actor 需要改写的配置层级，避开 AstrBotConfig deepcopy 限制。"""
        config_copy = dict(astrbot_config)
        platform_settings = config_copy.get("platform_settings", {})
        if isinstance(platform_settings, dict):
            platform_settings = dict(platform_settings)
            # self actor 需要经过正常权限链路，但不能被“忽略机器人自身消息”挡掉。
            platform_settings["ignore_bot_self_message"] = False
            config_copy["platform_settings"] = platform_settings
        return config_copy

    async def _run_waking_check(
        self, command_event: AstrMessageEvent, actor: str = "user"
    ) -> dict:
        """运行唤醒/过滤阶段，只做同步调度前校验。"""
        pipeline_context = self._make_pipeline_context(
            command_event,
            allow_bot_self_message=actor == "self",
        )
        waking_stage = WakingCheckStage()
        await waking_stage.initialize(pipeline_context)
        await waking_stage.process(command_event)

        activated_handlers = command_event.get_extra("activated_handlers", []) or []
        return {
            "matched_handlers": activated_handlers,
            "pipeline_context": pipeline_context,
            "result_type": "stopped" if command_event.is_stopped() else "matched",
            "stopped": command_event.is_stopped(),
        }

    async def _execute_command_via_pipeline(
        self, command_event: AstrMessageEvent
    ) -> dict:
        """通过管道执行命令。

        保留给测试和兼容调用；LLM tool 正常路径会改用异步后台调度。
        """
        execution = await self._run_waking_check(command_event)
        activated_handlers = execution["matched_handlers"]
        if not activated_handlers or command_event.is_stopped():
            return {
                "matched_handlers": activated_handlers,
                "messages": [],
                "raw_results": [],
                "result_type": "stopped" if command_event.is_stopped() else "none",
                "stopped": command_event.is_stopped(),
                "had_result": False,
            }

        process_stage = ProcessStage()
        await process_stage.initialize(execution["pipeline_context"])

        results: list[MessageEventResult] = []
        async for _ in process_stage.star_request_sub_stage.process(command_event):
            if result := command_event.get_result():
                results.append(copy.deepcopy(result))

        messages = [
            self._summarize_result(result)
            for result in results
            if self._summarize_result(result)
        ]
        result_type = "none"
        if results:
            if any(result.is_stopped() for result in results):
                result_type = "stopped"
            elif any(result.chain for result in results):
                result_type = "chain"
            else:
                result_type = "message"

        return {
            "matched_handlers": activated_handlers,
            "messages": messages,
            "raw_results": results,
            "result_type": result_type,
            "stopped": any(result.is_stopped() for result in results),
            "had_result": bool(results),
        }

    def _is_sendable_result(self, result) -> bool:
        """判断 handler 产物是否可以直接投递。"""
        return bool(result and getattr(result, "chain", None))

    def _summarize_output(self, output) -> str:
        """将一次 synthetic event 输出转换为不会泄露本地资源的摘要。"""
        if output is None:
            return ""
        if isinstance(output, str):
            return output.strip()

        chain = getattr(output, "chain", None)
        if chain is None:
            return f"[{type(output).__name__}]"

        parts: list[str] = []
        for component in chain:
            if isinstance(component, str):
                text = component.strip()
                if text:
                    parts.append(text)
                continue
            if type(component).__name__ == "Plain":
                text = getattr(component, "text", "")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                    continue
            # 图片、文件等只返回组件类型，避免将本地路径或二进制泄露给 tool。
            parts.append(f"[{type(component).__name__}]")
        return "".join(parts)

    def _attach_output_capture(
        self, command_event: AstrMessageEvent, state: _CommandTaskState
    ) -> None:
        """包装 synthetic event 发送接口，同时保持原会话消息投递。"""
        original_send = getattr(command_event, "send", None)
        if callable(original_send):

            async def capture_send(result, *args, **kwargs):
                # send_streaming 的底层实现通常会复用 event.send。仅跳过当前
                # 流片段的重复投递，不能用全局布尔值，否则并行普通 send 会漏记。
                if id(result) not in state.streaming_output_ids:
                    summary = self._summarize_output(result)
                    if summary:
                        state.messages.append(summary)
                sent = original_send(result, *args, **kwargs)
                if inspect.isawaitable(sent):
                    return await sent
                return sent

            command_event.send = capture_send

        original_send_streaming = getattr(command_event, "send_streaming", None)
        if callable(original_send_streaming):

            async def capture_send_streaming(generator, *args, **kwargs):
                async def captured_generator():
                    async for result in generator:
                        result_id = id(result)
                        state.streaming_output_ids.add(result_id)
                        try:
                            summary = self._summarize_output(result)
                            if summary:
                                state.messages.append(summary)
                            yield result
                        finally:
                            state.streaming_output_ids.discard(result_id)

                sent = original_send_streaming(captured_generator(), *args, **kwargs)
                if inspect.isawaitable(sent):
                    return await sent
                return sent

            command_event.send_streaming = capture_send_streaming

    async def _dispatch_command_task(
        self,
        event: AstrMessageEvent,
        command_event: AstrMessageEvent,
        pipeline_context: PipelineContext,
        final_command: str,
        state: _CommandTaskState,
    ) -> None:
        """后台执行命令并把后续结果投递回当前会话。"""
        try:
            scheduler = self._create_pipeline_scheduler(pipeline_context)
            await scheduler.initialize()
            # initialize 成功代表调度器已真正接手该命令，可安全回应 background。
            state.scheduler_initialized = True
            state.started.set()
            stage_index = self._find_stage_index(scheduler, "ProcessStage")
            await scheduler._process_stages(command_event, from_stage=stage_index)
            if command_event.is_stopped():
                state.error = RuntimeError(
                    self._describe_stopped_pipeline(command_event)
                )
        except Exception as exc:
            state.error = exc
        finally:
            state.started.set()
            cleanup = getattr(command_event, "cleanup_temporary_local_files", None)
            if cleanup:
                cleanup()

        if state.error is not None:
            logger.error(f"后台执行指令 {final_command} 失败: {state.error}")
            if self.cfg.enable_ai_command_result:
                try:
                    await self._plain_result(
                        event, f"执行命令 {final_command} 后台失败，原因：{state.error}"
                    )
                except Exception as result_exc:
                    logger.warning(f"发送后台执行失败通知失败: {result_exc}")

    def _describe_stopped_pipeline(self, command_event: AstrMessageEvent) -> str:
        """提取已停止管道的可观察错误，避免把宿主吞掉的异常伪装成成功。"""
        observable_keys = (
            "handler_error",
            "pipeline_error",
            "plugin_error",
            "error",
            "exception",
        )
        for key in observable_keys:
            value = command_event.get_extra(key, None)
            if isinstance(value, BaseException):
                detail = str(value)
            elif isinstance(value, str):
                detail = value.strip()
            elif isinstance(value, dict):
                detail = str(
                    value.get("message")
                    or value.get("error")
                    or value.get("detail")
                    or ""
                ).strip()
            else:
                detail = ""
            if detail:
                return f"AstrBot 管道终止: {detail}"

        result = command_event.get_result()
        if result is not None:
            detail = self._summarize_output(result)
            if detail:
                return f"AstrBot 管道终止: {detail}"
        return "AstrBot 管道终止，宿主未提供可观察的失败详情"

    def _find_stage_index(self, scheduler, stage_name: str) -> int:
        """查找后台调度应从哪个 AstrBot stage 开始恢复。"""
        for index, stage in enumerate(scheduler.stages):
            if stage.__class__.__name__ == stage_name:
                return index
        raise RuntimeError(f"未找到 AstrBot pipeline stage: {stage_name}")

    def _create_pipeline_scheduler(self, pipeline_context: PipelineContext):
        """延迟创建 AstrBot pipeline scheduler，避免插件导入期绑定完整核心模块。"""
        from astrbot.core.pipeline.scheduler import PipelineScheduler

        return PipelineScheduler(pipeline_context)

    def _schedule_command_task(
        self,
        event: AstrMessageEvent,
        command_event: AstrMessageEvent,
        pipeline_context: PipelineContext,
        final_command: str,
        state: _CommandTaskState,
    ) -> asyncio.Task:
        """提交后台命令任务，并持有引用直到任务结束。"""
        task = asyncio.create_task(
            self._dispatch_command_task(
                event,
                command_event,
                pipeline_context,
                final_command,
                state,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _wait_for_scheduler_start(
        self, state: _CommandTaskState, task: asyncio.Task, timeout: float
    ) -> bool:
        """有限等待初始化握手，超时只结束监听，不取消后台命令。"""
        start_waiter = asyncio.create_task(state.started.wait())
        try:
            await asyncio.wait({task, start_waiter}, timeout=timeout)
            return state.scheduler_initialized
        finally:
            if not start_waiter.done():
                start_waiter.cancel()
                await asyncio.gather(start_waiter, return_exceptions=True)

    def _resolve_result_wait(
        self, result_mode: str, wait_seconds: float | None
    ) -> tuple[float | None, dict | None]:
        """校验监听参数；错误时保证命令尚未被调度。"""
        if result_mode == "background":
            return None, None
        if result_mode == "auto":
            return float(self.cfg.ai_command_auto_wait_seconds), None
        if result_mode != "custom":
            return None, {
                "message": "result_mode 仅支持 auto、background 或 custom",
                "error": "invalid_result_mode",
            }
        if (
            type(wait_seconds) not in {int, float}
            or not math.isfinite(wait_seconds)
            or wait_seconds <= 0
        ):
            return None, {
                "message": "custom 模式要求 wait_seconds 为正的有限数值",
                "error": "invalid_wait_seconds",
            }
        if wait_seconds > self.cfg.ai_command_max_wait_seconds:
            return None, {
                "message": "wait_seconds 超过 ai_command_max_wait_seconds 配置上限",
                "error": "wait_seconds_exceeds_max",
            }
        return float(wait_seconds), None

    def _execution_result(
        self,
        *,
        command: str,
        matched_handlers: list,
        suggestions: list[str],
        is_forwarding_command: bool,
        is_external_routed_command: bool,
        actor: str,
        task: asyncio.Task,
        state: _CommandTaskState,
    ) -> dict:
        """把后台任务状态映射为稳定的 LLM tool 响应。"""
        if state.error is not None:
            return {
                "command": command,
                "success": False,
                "message": f"执行失败: {state.error}",
                "matched_handlers": matched_handlers,
                "messages": list(state.messages),
                "result_type": "failed",
                "error": str(state.error),
                "suggestions": suggestions,
                "is_forwarding_command": is_forwarding_command,
                "external_response_pending": False,
                "actor": actor,
                "dispatched": True,
                "execution_state": "failed",
                "output_complete": True,
            }

        if not state.scheduler_initialized:
            return {
                "command": command,
                "success": False,
                "message": "命令调度器未在等待窗口内完成初始化，后台任务仍在继续初始化",
                "matched_handlers": matched_handlers,
                "messages": list(state.messages),
                "result_type": "not_started",
                "error": "scheduler_start_timeout",
                "suggestions": suggestions,
                "is_forwarding_command": is_forwarding_command,
                "external_response_pending": False,
                "actor": actor,
                "dispatched": False,
                "execution_state": "not_started",
                "output_complete": False,
            }

        # 自定义目录命令只命中通用处理器时，常由桥接插件转交到外部 Bot
        # 框架。该框架的回复不属于本次 synthetic event，不能把空捕获误报为失败。
        if is_external_routed_command and not state.messages:
            return {
                "command": command,
                "success": True,
                "message": (
                    "命令已由自定义命令路由器受理；回复会由外部 Bot 框架异步发送到"
                    "当前聊天。本次调用未捕获本地输出不代表失败，请等待后续消息，"
                    "不要重复调用。"
                ),
                "matched_handlers": matched_handlers,
                "messages": [],
                "result_type": "external_dispatched",
                "error": None,
                "suggestions": suggestions,
                "is_forwarding_command": is_forwarding_command,
                "external_response_pending": True,
                "actor": actor,
                "dispatched": True,
                "execution_state": "accepted",
                "output_complete": False,
            }

        if task.done():
            return {
                "command": command,
                "success": True,
                "message": "命令执行完成",
                "matched_handlers": matched_handlers,
                "messages": list(state.messages),
                "result_type": "completed",
                "error": None,
                "suggestions": suggestions,
                "is_forwarding_command": is_forwarding_command,
                "external_response_pending": False,
                "actor": actor,
                "dispatched": True,
                "execution_state": "completed",
                "output_complete": True,
            }

        return {
            "command": command,
            "success": True,
            "message": "命令已启动并继续后台执行",
            "matched_handlers": matched_handlers,
            "messages": list(state.messages),
            "result_type": "forwarding" if is_forwarding_command else "dispatched",
            "error": None,
            "suggestions": suggestions,
            "is_forwarding_command": is_forwarding_command,
            "external_response_pending": False,
            "actor": actor,
            "dispatched": True,
            "execution_state": "running",
            "output_complete": False,
        }

    def _get_regex_examples(self, regex_command: str) -> list[str]:
        """获取正则命令的示例文本列表

        Args:
            regex_command: 以 regex: 开头的命令名

        Returns:
            示例文本列表
        """
        try:
            self.command_index.update_config()
            cache = self.command_index.get_all_commands()
            if cache:
                cmd_info = cache.get(regex_command)
                if cmd_info:
                    examples = cmd_info.get("examples", [])
                    if examples:
                        return list(examples)
        except Exception as exc:
            logger.warning(f"获取正则命令示例失败: {exc}")
        return []

    def _summarize_result(self, result: MessageEventResult) -> str:
        """汇总结果"""
        if not result.chain:
            return ""
        return result.get_plain_text(with_other_comps_mark=True).strip()

    def normalize_command_text(self, command: str) -> str:
        """规范化命令文本"""
        command = command.strip()
        if not command:
            return command

        # 正则触发命令不以 / 开头，直接返回原文
        if command.startswith("regex:"):
            return command

        # 如果命令已经有前缀，直接返回
        for prefix in self.prefixes:
            if command.startswith(prefix):
                return command

        # 如果没有前缀，添加默认前缀
        return self.prefixes[0] + command

    def strip_command_prefix(self, command: str) -> str:
        """去除命令前缀"""
        # 正则触发命令不以 / 开头，直接返回原文
        if command.startswith("regex:"):
            return command
        for prefix in self.prefixes:
            if command.startswith(prefix):
                return command[len(prefix) :].strip()
        return command.strip()

    async def _plain_result(self, event: AstrMessageEvent, text: str) -> None:
        """兼容同步和异步的 plain_result，实现测试与运行时一致投递。"""
        plain_result = getattr(event, "plain_result", None)
        if not callable(plain_result):
            logger.warning("当前事件不支持 plain_result，跳过命令执行提示")
            return

        result = plain_result(text)
        if inspect.isawaitable(result):
            await result
            return
        if self._is_sendable_result(result):
            send = getattr(event, "send", None)
            if callable(send):
                await send(result)
            else:
                await self.context.send_message(event.unified_msg_origin, result.chain)

    async def execute(
        self,
        event: AstrMessageEvent,
        command: str,
        allowed_plugins: set[str] | None = None,
        search_suggestions_func=None,
        actor: str = "user",
        result_mode: str = "auto",
        wait_seconds: float | None = None,
    ) -> dict:
        """
        执行 AstrBot 命令

        Args:
            event: 消息事件
            command: 要执行的命令
            allowed_plugins: 允许的插件集合
            search_suggestions_func: 搜索建议的回调函数
            actor: 执行角色，"user"（默认）或 "self"。
            result_mode: 结果监听模式，auto、background 或 custom。
            wait_seconds: custom 模式的监听窗口（秒）。

        Returns:
            执行结果字典
        """
        command_text = command.strip()

        if not command_text:
            return {
                "command": "",
                "success": False,
                "message": "缺少必需参数: command",
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": "missing_command",
                "execution_state": "rejected",
                "output_complete": False,
                "dispatched": False,
            }

        if command_text in {"execute_astrbot_command", "/execute_astrbot_command"}:
            return {
                "command": command_text,
                "success": False,
                "message": "禁止递归调用 execute_astrbot_command",
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": "recursive_call_blocked",
                "execution_state": "rejected",
                "output_complete": False,
                "dispatched": False,
            }

        from ..utils.text import matches_custom_group_regex

        # AI 可能直接使用自定义正则的实际触发文本（如 nte帮助）。
        # 这类文本不能补普通命令前缀，否则 RegexFilter 与转发处理器无法匹配。
        is_direct_custom_regex = not command_text.startswith(
            "regex:"
        ) and matches_custom_group_regex(command_text)
        final_command = (
            command_text
            if is_direct_custom_regex
            else self.normalize_command_text(command_text)
        )
        stripped_command = (
            final_command
            if is_direct_custom_regex
            else self.strip_command_prefix(final_command)
        )
        if not stripped_command:
            return {
                "command": final_command,
                "success": False,
                "message": "命令不能为空",
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": "empty_command",
                "execution_state": "rejected",
                "output_complete": False,
                "dispatched": False,
            }

        if actor not in {"user", "self"}:
            return {
                "command": final_command,
                "success": False,
                "message": "actor 仅支持 user 或 self",
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": "invalid_actor",
                "execution_state": "rejected",
                "output_complete": False,
                "dispatched": False,
            }

        if actor == "self" and not self.cfg.enable_ai_self_command:
            return {
                "command": final_command,
                "success": False,
                "message": "actor=self 默认禁用，请先开启 enable_ai_self_command 配置项",
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": "self_actor_disabled",
                "execution_state": "rejected",
                "output_complete": False,
                "dispatched": False,
            }

        if actor == "self" and not event.get_self_id():
            return {
                "command": final_command,
                "success": False,
                "message": "当前事件缺少 bot self_id，无法使用 actor=self 执行命令",
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": "missing_self_id",
                "execution_state": "rejected",
                "output_complete": False,
                "dispatched": False,
            }

        wait_window, wait_error = self._resolve_result_wait(result_mode, wait_seconds)
        if wait_error is not None:
            return {
                "command": final_command,
                "success": False,
                "message": wait_error["message"],
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": wait_error["error"],
                "execution_state": "rejected",
                "output_complete": False,
                "dispatched": False,
            }

        try:
            # 获取建议
            suggestions = []
            if search_suggestions_func:
                suggestions = search_suggestions_func(stripped_command, allowed_plugins)

            # 执行前通知
            if self.cfg.enable_ai_command_notify:
                try:
                    await self._plain_result(event, f"正在执行命令: {final_command}")
                except Exception as notify_exc:
                    logger.warning(f"发送执行前通知失败: {notify_exc}")

            # 尝试执行命令
            # 对于正则命令，需要用实际匹配文本作为 message_str，而不是 regex:pattern 本身
            execution_text = final_command
            if final_command.startswith("regex:"):
                pattern = final_command[6:]  # strip "regex:"
                examples = self._get_regex_examples(final_command)
                if examples:
                    execution_text = examples[0]
                    logger.info(
                        f"正则命令 '{final_command}' 使用示例文本执行: '{execution_text}'"
                    )
                else:
                    execution_text = pattern.lstrip("^").rstrip("$")
                    logger.info(
                        f"正则命令 '{final_command}' 无示例，使用派生文本: '{execution_text}'"
                    )

            command_event = self._build_command_event(event, execution_text, actor)
            state = _CommandTaskState()
            self._attach_output_capture(command_event, state)
            execution = await self._run_waking_check(command_event, actor)
            matched_handlers = execution["matched_handlers"]
            result_type = execution["result_type"]

            # 检查是否只匹配到了通用消息处理器
            def is_generic_handler(handler) -> bool:
                """检查是否是通用消息处理器"""
                handler_name = getattr(handler, "handler_name", "")
                generic_handlers = {
                    "on_message",
                    "on_all_message",
                    "handle_empty_mention",
                    "handle_session_control_agent",
                    "on_file_message",
                }
                return handler_name in generic_handlers

            def is_forwarding_plugin(handler) -> bool:
                """检查是否是转发插件"""
                module_path = getattr(handler, "handler_module_path", "")
                forwarding_keywords = ["adapter", "forward", "proxy", "bridge"]
                return any(
                    keyword in module_path.lower() for keyword in forwarding_keywords
                )

            all_generic = (
                all(is_generic_handler(h) for h in matched_handlers)
                if matched_handlers
                else True
            )
            has_forwarding_plugin = (
                any(is_forwarding_plugin(h) for h in matched_handlers)
                if matched_handlers
                else False
            )

            # 检查是否是自定义命令组的命令（包括正则命令）
            from ..utils.text import looks_like_custom_group_command

            is_custom_group_cmd = looks_like_custom_group_command(
                stripped_command, prefixes=self.prefixes
            )
            # 正则命令可能无法被 looks_like_custom_group_command 识别，
            # 需要通过 command_index 二次确认
            is_custom_regex_cmd = is_direct_custom_regex
            if final_command.startswith("regex:"):
                try:
                    cache = self.command_index.get_all_commands()
                    if cache:
                        cmd_info = cache.get(final_command)
                        if cmd_info and cmd_info.get("custom_groups"):
                            is_custom_regex_cmd = True
                except Exception:
                    pass

            # 自定义命令组命令：普通命令或正则命令
            is_any_custom_cmd = is_custom_group_cmd or is_custom_regex_cmd
            # 目录命令由通用处理器接收时，实际回复可能由桥接到的外部 Bot
            # 框架发送；AstrBot 本次事件无法可靠捕获那条回复。
            is_external_routed_command = is_any_custom_cmd and all_generic
            is_forwarding_command = (
                is_any_custom_cmd and all_generic and has_forwarding_plugin
            )
            # 自定义正则命令：即使没有转发插件也不应该被黑名单拦截
            if is_custom_regex_cmd and not is_forwarding_command:
                is_forwarding_command = True

            # 如果只匹配到通用处理器，需要进一步判断
            if all_generic and matched_handlers:
                if is_any_custom_cmd:
                    # 自定义命令组的命令，可能是转发命令
                    if has_forwarding_plugin:
                        # 有转发插件，说明这确实是一个转发命令
                        logger.info(
                            f"命令 '{stripped_command}' 属于自定义命令组，将通过转发插件处理"
                        )
                    else:
                        # 没有转发插件，可能是配置错误
                        logger.warning(
                            f"命令 '{stripped_command}' 属于自定义命令组但没有匹配到转发插件"
                        )
                else:
                    # 非自定义命令组的命令，只匹配到通用处理器说明命令不存在
                    error_msg = f"未找到指令 '{stripped_command}' 的具体处理器。该命令可能不存在或名称不正确。"
                    logger.info(
                        f"命令 '{stripped_command}' 只匹配到通用消息处理器，可能不存在该命令"
                    )
                    if self.cfg.enable_ai_command_result:
                        try:
                            await self._plain_result(
                                event,
                                f"执行命令 {final_command} 失败，原因：{error_msg}",
                            )
                        except Exception as result_exc:
                            logger.warning(f"发送执行失败通知失败: {result_exc}")
                    return {
                        "command": final_command,
                        "success": False,
                        "message": error_msg,
                        "matched_handlers": matched_handlers,
                        "messages": [],
                        "result_type": "generic_only",
                        "error": "command_not_found",
                        "suggestions": suggestions,
                        "execution_state": "rejected",
                        "output_complete": False,
                        "dispatched": False,
                    }

            # 检查黑名单（但跳过自定义命令组的转发命令）
            if not is_forwarding_command:
                blacklisted_handlers = []
                for handler in matched_handlers:
                    if is_generic_handler(handler):
                        continue
                    plugin_name = getattr(handler, "handler_module_path", "")
                    if plugin_name and any(
                        plugin_name.startswith(bl) or plugin_name == bl
                        for bl in self.cfg.ai_command_blacklist
                    ):
                        blacklisted_handlers.append(plugin_name)

                if blacklisted_handlers:
                    error_msg = (
                        f"指令 '{stripped_command}' 所属插件在AI调用黑名单中，禁止执行"
                    )
                    logger.warning(f"AI尝试调用黑名单插件命令: {blacklisted_handlers}")
                    if self.cfg.enable_ai_command_result:
                        try:
                            await self._plain_result(
                                event,
                                f"执行命令 {final_command} 失败，原因：{error_msg}",
                            )
                        except Exception as result_exc:
                            logger.warning(f"发送执行失败通知失败: {result_exc}")
                    return {
                        "command": final_command,
                        "success": False,
                        "message": error_msg,
                        "matched_handlers": matched_handlers,
                        "messages": [],
                        "result_type": "blocked",
                        "error": "blacklisted_plugin",
                        "suggestions": [],
                        "execution_state": "rejected",
                        "output_complete": False,
                        "dispatched": False,
                    }

            if not matched_handlers:
                error_msg = f"未找到或无法执行指令 '{stripped_command}'"
                if self.cfg.enable_ai_command_result:
                    try:
                        await self._plain_result(
                            event, f"执行命令 {final_command} 失败，原因：{error_msg}"
                        )
                    except Exception as result_exc:
                        logger.warning(f"发送执行失败通知失败: {result_exc}")
                return {
                    "command": final_command,
                    "success": False,
                    "message": error_msg,
                    "matched_handlers": [],
                    "messages": [],
                    "result_type": result_type,
                    "error": "command_not_found"
                    if not suggestions
                    else "no_handler_matched",
                    "suggestions": suggestions,
                    "execution_state": "rejected",
                    "output_complete": False,
                    "dispatched": False,
                }

            if execution.get("stopped"):
                error_msg = f"指令 '{stripped_command}' 在调度前被 AstrBot 管道终止"
                if self.cfg.enable_ai_command_result:
                    try:
                        await self._plain_result(
                            event, f"执行命令 {final_command} 失败，原因：{error_msg}"
                        )
                    except Exception as result_exc:
                        logger.warning(f"发送执行失败通知失败: {result_exc}")
                return {
                    "command": final_command,
                    "success": False,
                    "message": error_msg,
                    "matched_handlers": matched_handlers,
                    "messages": [],
                    "result_type": "stopped",
                    "error": "command_stopped",
                    "suggestions": suggestions,
                    "is_forwarding_command": is_forwarding_command,
                    "execution_state": "rejected",
                    "output_complete": False,
                    "dispatched": False,
                }

            task = self._schedule_command_task(
                event,
                command_event,
                execution["pipeline_context"],
                final_command,
                state,
            )

            # background 没有结果监听窗口，使用既有 auto 窗口作为初始化握手上限；
            # auto/custom 则共享各自的完整等待预算，且不会取消后台任务。
            scheduler_start_timeout = (
                wait_window
                if wait_window is not None
                else float(self.cfg.ai_command_auto_wait_seconds)
            )
            loop = asyncio.get_running_loop()
            wait_started_at = loop.time()
            await self._wait_for_scheduler_start(state, task, scheduler_start_timeout)

            if (
                self.cfg.enable_ai_command_result
                and state.error is None
                and state.scheduler_initialized
            ):
                try:
                    if is_external_routed_command:
                        await self._plain_result(
                            event,
                            f"执行命令 {final_command}，已由自定义命令路由器受理；"
                            "外部回复将异步发送到当前聊天",
                        )
                    elif is_forwarding_command:
                        await self._plain_result(
                            event, f"执行转发命令 {final_command}，已提交后台处理"
                        )
                    else:
                        await self._plain_result(
                            event, f"执行命令 {final_command}，已提交后台执行"
                        )
                except Exception as result_exc:
                    logger.warning(f"发送执行结果通知失败: {result_exc}")

            if (
                wait_window is not None
                and not task.done()
                and state.error is None
                and state.scheduler_initialized
            ):
                # asyncio.wait 不会取消未完成任务；监听窗口仅影响 tool 返回时机。
                elapsed = loop.time() - wait_started_at
                await asyncio.wait({task}, timeout=max(0.0, wait_window - elapsed))

            return self._execution_result(
                command=final_command,
                matched_handlers=matched_handlers,
                suggestions=suggestions,
                is_forwarding_command=is_forwarding_command,
                is_external_routed_command=is_external_routed_command,
                actor=actor,
                task=task,
                state=state,
            )
        except Exception as exc:
            logger.error(f"执行指令失败: {exc}", exc_info=True)
            return {
                "command": final_command,
                "success": False,
                "message": f"执行失败: {exc}",
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": str(exc),
                "execution_state": "failed",
                "output_complete": True,
                "dispatched": False,
            }


# 单例实例
_executor_instance: CommandExecutor | None = None


def get_command_executor() -> CommandExecutor:
    """获取命令执行器单例。

    Returns:
        CommandExecutor 实例
    """
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = CommandExecutor()
    return _executor_instance


def reset_command_executor() -> None:
    """重置命令执行器（用于测试）。"""
    global _executor_instance
    _executor_instance = None
