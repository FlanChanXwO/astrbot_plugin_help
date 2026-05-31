"""命令执行器 - 单例模式"""

from __future__ import annotations

import asyncio
import copy
import inspect
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

    async def _dispatch_command_task(
        self,
        event: AstrMessageEvent,
        command_event: AstrMessageEvent,
        pipeline_context: PipelineContext,
        final_command: str,
    ) -> None:
        """后台执行命令并把后续结果投递回当前会话。"""
        try:
            scheduler = self._create_pipeline_scheduler(pipeline_context)
            await scheduler.initialize()
            stage_index = self._find_stage_index(scheduler, "ProcessStage")
            await scheduler._process_stages(command_event, from_stage=stage_index)
        except Exception as exc:
            logger.error(f"后台执行指令 {final_command} 失败: {exc}", exc_info=True)
            if self.cfg.enable_ai_command_result:
                try:
                    await self._plain_result(
                        event, f"执行命令 {final_command} 后台失败，原因：{exc}"
                    )
                except Exception as result_exc:
                    logger.warning(f"发送后台执行失败通知失败: {result_exc}")
        finally:
            cleanup = getattr(command_event, "cleanup_temporary_local_files", None)
            if cleanup:
                cleanup()

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
    ) -> None:
        """提交后台命令任务，并持有引用直到任务结束。"""
        task = asyncio.create_task(
            self._dispatch_command_task(
                event,
                command_event,
                pipeline_context,
                final_command,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

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
    ) -> dict:
        """
        执行 AstrBot 命令

        Args:
            event: 消息事件
            command: 要执行的命令
            allowed_plugins: 允许的插件集合
            search_suggestions_func: 搜索建议的回调函数
            actor: 执行角色，"user"（默认）或 "self"。

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
            }

        final_command = self.normalize_command_text(command_text)
        stripped_command = self.strip_command_prefix(final_command)
        if not stripped_command:
            return {
                "command": final_command,
                "success": False,
                "message": "命令不能为空",
                "matched_handlers": [],
                "messages": [],
                "result_type": "none",
                "error": "empty_command",
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

            is_custom_group_cmd = looks_like_custom_group_command(stripped_command)
            # 正则命令可能无法被 looks_like_custom_group_command 识别，
            # 需要通过 command_index 二次确认
            is_custom_regex_cmd = False
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
                }

            if is_forwarding_command:
                message = f"命令 '{stripped_command}' 是转发命令，已提交后台处理"
            else:
                message = f"命令已提交后台执行，命中 {len(matched_handlers)} 个处理器"

            self._schedule_command_task(
                event,
                command_event,
                execution["pipeline_context"],
                final_command,
            )

            if self.cfg.enable_ai_command_result:
                try:
                    if is_forwarding_command:
                        await self._plain_result(
                            event, f"执行转发命令 {final_command}，已提交后台处理"
                        )
                    else:
                        await self._plain_result(
                            event, f"执行命令 {final_command}，已提交后台执行"
                        )
                except Exception as result_exc:
                    logger.warning(f"发送执行结果通知失败: {result_exc}")

            return {
                "command": final_command,
                "success": True,
                "message": message,
                "matched_handlers": matched_handlers,
                "messages": [],
                "result_type": "forwarding" if is_forwarding_command else "dispatched",
                "error": None,
                "suggestions": suggestions,
                "is_forwarding_command": is_forwarding_command,
                "actor": actor,
                "dispatched": True,
            }
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
