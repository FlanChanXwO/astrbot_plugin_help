"""execute_astrbot_command 的 actor 与异步调度测试。"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import src.infrastructure.analysis.executor as executor_module
from src.infrastructure.analysis.command_index import reset_command_index
from src.infrastructure.analysis.executor import (
    CommandExecutor,
    reset_command_executor,
)
from src.infrastructure.config.config_manager import clear_config, init_config
from src.infrastructure.config.datamodels import CustomGroupCommand, CustomGroupConfig
from src.infrastructure.context_holder import clear_context, set_context
from src.infrastructure.utils.paths import init_plugin_paths

from tests.mocks import (
    MockAstrMessageEvent,
    MockCommandFilter,
    MockContext,
    MockHandler,
    MockMessageEventResult,
)


class _Plain:
    """测试用 Plain，避免依赖真实 AstrBot 消息段。"""

    def __init__(self, text: str):
        self.text = text


class _PipelineContext:
    """测试用 PipelineContext。"""

    def __init__(self, astrbot_config, plugin_manager, astrbot_config_id):
        self.astrbot_config = astrbot_config
        self.plugin_manager = plugin_manager
        self.astrbot_config_id = astrbot_config_id


class _DeepcopyHostileConfig(dict):
    """模拟 AstrBotConfig：deepcopy 会因缺失 __setstate__ 属性返回 None 而失败。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "config_path", "cmd_config.json")

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            return None


class _WakingCheckStage:
    """按测试事件上的 extra 模拟 AstrBot 唤醒阶段。"""

    handlers = [
        MockHandler(
            "help",
            event_filters=[MockCommandFilter("help")],
            handler_module_path="help_plugin",
        )
    ]
    send_during_process = False

    async def initialize(self, ctx) -> None:
        self.ctx = ctx

    async def process(self, event) -> None:
        if self.send_during_process:
            await event.send(MockMessageEventResult(chain=["过滤器错误提示"]))
        event.set_extra("activated_handlers", self.handlers)


class _Scheduler:
    """后台调度器 mock，阻塞到测试显式放行。"""

    started = asyncio.Event()
    release = asyncio.Event()
    command_events: list[MockAstrMessageEvent] = []

    def __init__(self, ctx):
        self.ctx = ctx
        self.stages = [type("ProcessStage", (), {})()]

    async def initialize(self) -> None:
        return None

    async def _process_stages(self, event, from_stage=0) -> None:
        self.command_events.append(event)
        self.started.set()
        await self.release.wait()
        await event.send(f"后台结果:{event.get_message_str()}")


@pytest.fixture(autouse=True)
def _setup_singletons():
    """Set up required singletons before each test, tear down after."""
    mock_ctx = MockContext()
    set_context(mock_ctx)

    tmpdir = tempfile.TemporaryDirectory()
    plugin_dir = Path(tmpdir.name) / "astrbot_plugin_helpinfo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    init_plugin_paths(plugin_dir)

    data_dir = Path(tmpdir.name) / "data" / "plugin_data" / "astrbot_plugin_helpinfo"
    data_dir.mkdir(parents=True, exist_ok=True)
    patcher = patch(
        "astrbot.api.star.StarTools.get_data_dir",
        return_value=data_dir,
    )
    patcher.start()

    cfg = init_config(None)
    cfg.custom_groups = [
        CustomGroupConfig(
            group_name="测试分组",
            commands=[CustomGroupCommand(command="help", description="测试命令")],
        )
    ]

    _Scheduler.started = asyncio.Event()
    _Scheduler.release = asyncio.Event()
    _Scheduler.command_events = []
    _WakingCheckStage.handlers = [
        MockHandler(
            "help",
            event_filters=[MockCommandFilter("help")],
            handler_module_path="help_plugin",
        )
    ]
    _WakingCheckStage.send_during_process = False
    with (
        patch.object(executor_module, "Plain", _Plain),
        patch.object(executor_module, "PipelineContext", _PipelineContext),
        patch.object(executor_module, "WakingCheckStage", _WakingCheckStage),
        patch.object(
            executor_module.CommandExecutor,
            "_create_pipeline_scheduler",
            lambda self, pipeline_context: _Scheduler(pipeline_context),
        ),
    ):
        yield

    patcher.stop()
    clear_context()
    clear_config()
    reset_command_executor()
    reset_command_index()
    tmpdir.cleanup()


@pytest.fixture
def mock_event():
    """Provide a fresh MockAstrMessageEvent."""
    return MockAstrMessageEvent(message="/help")


class TestActorSignatureAndDefault:
    """Verify the actor param signature and defaults."""

    def test_signature_accepts_actor(self):
        """execute() accepts actor kwarg with default 'user'."""
        sig = inspect.signature(CommandExecutor.execute)
        assert "actor" in sig.parameters
        assert sig.parameters["actor"].default == "user"

    def test_signature_accepts_result_listening_options(self):
        """执行器暴露同步监听模式与自定义等待参数。"""
        sig = inspect.signature(CommandExecutor.execute)
        assert sig.parameters["result_mode"].default == "auto"
        assert sig.parameters["wait_seconds"].default is None

    def test_help_service_signature_accepts_actor(self):
        """HelpService.execute_command() accepts actor kwarg with default 'user'."""
        from src.application.services.help_service import HelpService

        sig = inspect.signature(HelpService.execute_command)
        assert "actor" in sig.parameters
        assert sig.parameters["actor"].default == "user"


class TestEarlyReturnsBothActors:
    """Early-return paths are identical for both actors."""

    @pytest.mark.asyncio
    async def test_missing_command_user(self, mock_event):
        """Empty command returns error for user actor."""
        executor = CommandExecutor()
        result = await executor.execute(event=mock_event, command="", actor="user")
        assert result["success"] is False
        assert result["error"] == "missing_command"

    @pytest.mark.asyncio
    async def test_missing_command_self(self, mock_event):
        """Empty command returns error before self actor config validation."""
        executor = CommandExecutor()
        result = await executor.execute(event=mock_event, command="", actor="self")
        assert result["success"] is False
        assert result["error"] == "missing_command"

    @pytest.mark.asyncio
    async def test_recursive_call_blocked_user(self, mock_event):
        """Recursive call blocked for user actor."""
        executor = CommandExecutor()
        result = await executor.execute(
            event=mock_event, command="execute_astrbot_command", actor="user"
        )
        assert result["success"] is False
        assert result["error"] == "recursive_call_blocked"

    @pytest.mark.asyncio
    async def test_recursive_call_blocked_self(self, mock_event):
        """Recursive call blocked before self actor config validation."""
        executor = CommandExecutor()
        result = await executor.execute(
            event=mock_event, command="execute_astrbot_command", actor="self"
        )
        assert result["success"] is False
        assert result["error"] == "recursive_call_blocked"


class TestAsyncDispatch:
    """命令执行只等待调度成功，不等待后台结果。"""

    @pytest.mark.asyncio
    async def test_synthetic_dispatch_excludes_builtin_active_reply_handler(
        self, mock_event
    ):
        """代执行事件不能被内置主动回复再次解释成目标用户的新请求。"""
        builtin_active_reply = MockHandler(
            "on_message",
            event_filters=[SimpleNamespace(filter_type="platform_adapter_type")],
            handler_module_path="astrbot.builtin_stars.astrbot.main",
        )
        command_handler = MockHandler(
            "help",
            event_filters=[MockCommandFilter("help")],
            handler_module_path="help_plugin",
        )
        forwarding_handler = MockHandler(
            "on_all_message",
            event_filters=[SimpleNamespace(filter_type="event_message_type")],
            handler_module_path="data.plugins.astrbot_plugin_gscore_adapter.main",
        )
        _WakingCheckStage.handlers = [
            builtin_active_reply,
            forwarding_handler,
            command_handler,
        ]
        executor = CommandExecutor()

        execution = await executor._run_waking_check(mock_event)

        assert execution["matched_handlers"] == [forwarding_handler, command_handler]
        assert mock_event.get_extra("activated_handlers") == [
            forwarding_handler,
            command_handler,
        ]

    @pytest.mark.asyncio
    async def test_user_actor_returns_after_dispatch(self, mock_event):
        """user actor 调度后立即返回，后台稍后发送结果。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(
            event=mock_event,
            command="/help",
            actor="user",
            result_mode="background",
        )
        await asyncio.sleep(0)

        assert result["success"] is True, result
        assert result["dispatched"] is True
        assert result["result_type"] == "dispatched"
        assert result["messages"] == []
        assert _Scheduler.started.is_set()
        assert not any(m.get("type") == "result" for m in mock_event.sent_messages)

        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)
        assert any(m.get("type") == "result" for m in mock_event.sent_messages)

    @pytest.mark.asyncio
    async def test_result_notification_means_dispatched(self, mock_event):
        """结果通知语义改为已提交后台执行。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = True

        result = await executor.execute(
            event=mock_event,
            command="/help",
            actor="user",
            result_mode="background",
        )

        assert result["success"] is True, result
        assert any(
            "已提交后台执行" in str(m.get("content", ""))
            for m in mock_event.sent_messages
        )
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    async def test_pre_notification_still_sent(self, mock_event):
        """执行前通知仍按配置发送。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = True
        executor.cfg.enable_ai_command_result = False

        await executor.execute(
            event=mock_event,
            command="/help",
            actor="user",
            result_mode="background",
        )

        assert any(
            "正在执行命令" in str(m.get("content", ""))
            for m in mock_event.sent_messages
        )
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    async def test_sync_plain_result_with_send_none_does_not_block_dispatch(
        self, mock_event
    ):
        """真实 AstrBot plain_result 返回结果对象；send 缺失不应阻塞调度。"""

        def plain_result(text: str):
            return MockMessageEventResult(chain=[text])

        mock_event.plain_result = plain_result
        mock_event.send = None
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = True
        executor.cfg.enable_ai_command_result = True

        result = await executor.execute(
            event=mock_event,
            command="/help",
            actor="user",
            result_mode="background",
        )
        await asyncio.sleep(0)

        assert result["success"] is True, result
        assert result["dispatched"] is True
        assert _Scheduler.started.is_set()
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    async def test_waking_check_send_none_does_not_mask_dispatch(self, mock_event):
        """唤醒阶段内部发送提示时，也要能通过原始会话投递。"""
        _WakingCheckStage.send_during_process = True
        mock_event.send = None
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(
            event=mock_event,
            command="/help",
            actor="user",
            result_mode="background",
        )
        await asyncio.sleep(0)

        assert result["success"] is True, result
        assert result["dispatched"] is True
        assert _Scheduler.started.is_set()
        assert executor.context.sent_messages
        assert executor.context.sent_messages[0]["session"] == (
            mock_event.unified_msg_origin
        )
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)


class TestActorSelf:
    """actor=self 的安全开关与身份改写。"""

    @pytest.mark.asyncio
    async def test_self_actor_disabled_by_default(self, mock_event):
        """self actor 默认禁用。"""
        executor = CommandExecutor()
        result = await executor.execute(
            event=mock_event,
            command="/help",
            actor="self",
            result_mode="background",
        )
        assert result["success"] is False
        assert result["error"] == "self_actor_disabled"
        assert not _Scheduler.started.is_set()

    @pytest.mark.asyncio
    async def test_self_actor_requires_self_id(self):
        """启用 self actor 后仍要求事件携带 self_id。"""
        event = MockAstrMessageEvent(message="/help", self_id="")
        executor = CommandExecutor()
        executor.cfg.enable_ai_self_command = True

        result = await executor.execute(event=event, command="/help", actor="self")

        assert result["success"] is False
        assert result["error"] == "missing_self_id"
        assert not _Scheduler.started.is_set()

    @pytest.mark.asyncio
    async def test_self_actor_rewrites_sender_and_uses_original_chat(self, mock_event):
        """self actor 使用 bot self_id 作为发送者，结果仍发回当前聊天。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_self_command = True
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(
            event=mock_event,
            command="/help",
            actor="self",
            result_mode="background",
        )
        await asyncio.sleep(0)

        assert result["success"] is True, result
        command_event = _Scheduler.command_events[0]
        assert command_event.get_sender_id() == mock_event.get_self_id()
        assert mock_event.get_sender_id() == "123456"

        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)
        assert any(m.get("type") == "result" for m in mock_event.sent_messages)

    @pytest.mark.asyncio
    async def test_self_actor_does_not_deepcopy_astrbot_config(self, mock_event):
        """self actor 只复制必要配置层级，避开 AstrBotConfig deepcopy 问题。"""
        hostile_config = _DeepcopyHostileConfig(
            {
                "admins_id": [],
                "plugin_set": ["*"],
                "wake_prefix": ["/"],
                "platform_settings": {
                    "ignore_bot_self_message": True,
                    "no_permission_reply": True,
                },
                "provider_settings": {"enable": False},
            }
        )
        executor = CommandExecutor()
        executor.context.get_config = lambda umo=None: hostile_config
        executor.cfg.enable_ai_self_command = True
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help", actor="self")
        await asyncio.sleep(0)

        assert result["success"] is True, result
        assert _Scheduler.started.is_set()
        assert hostile_config["platform_settings"]["ignore_bot_self_message"] is True
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    async def test_user_actor_keeps_sender(self, mock_event):
        """user actor 不改写发送者。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(
            event=mock_event,
            command="/help",
            actor="user",
            result_mode="background",
        )
        await asyncio.sleep(0)

        assert result["success"] is True, result
        command_event = _Scheduler.command_events[0]
        assert command_event.get_sender_id() == "123456"

        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)


class TestNoDispatchOnRejectedCommand:
    """拒绝路径不会创建后台任务。"""

    @pytest.mark.asyncio
    async def test_generic_only_not_dispatched(self, mock_event):
        """只命中通用处理器时不调度后台执行。"""
        _WakingCheckStage.handlers = [
            MockHandler("on_message", handler_module_path="builtin_commands")
        ]
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/unknown")

        assert result["success"] is False
        assert result["error"] == "command_not_found"
        assert not _Scheduler.started.is_set()

    @pytest.mark.asyncio
    async def test_prefixed_case_variant_custom_command_dispatches_with_generic_handler(
        self, mock_event
    ):
        """图片目录保留前缀/大小写时，仍应识别为自定义命令并交给通用处理器。"""
        from src.infrastructure.config import get_config

        get_config().custom_groups = [
            CustomGroupConfig(
                group_name="图片命令",
                commands=[CustomGroupCommand(command="/HELP")],
            )
        ]
        _WakingCheckStage.handlers = [
            MockHandler("on_message", handler_module_path="builtin_commands")
        ]
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(
            event=mock_event, command="/help", result_mode="background"
        )

        assert result["success"] is True, result
        assert result["dispatched"] is True
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    async def test_custom_alias_with_configured_prefix_dispatches_with_generic_handler(
        self, mock_event
    ):
        """自定义别名使用非默认前缀时，也不能被误判为不存在的通用消息。"""
        from src.infrastructure.config import get_config

        get_config().custom_groups = [
            CustomGroupConfig(
                group_name="图片命令",
                commands=[CustomGroupCommand(command="帮助", aliases=["!HELP"])],
            )
        ]
        _WakingCheckStage.handlers = [
            MockHandler("on_message", handler_module_path="builtin_commands")
        ]
        executor = CommandExecutor()
        executor.update_prefixes(["!"])
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False
        _Scheduler.release.set()

        result = await executor.execute(event=mock_event, command="!help")

        assert result["success"] is True, result
        assert result["dispatched"] is True
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    async def test_custom_regex_trigger_text_dispatches_with_generic_handler(
        self, mock_event
    ):
        """线上目录保存正则时，AI 直接使用触发文本也应识别为自定义命令。"""
        from src.infrastructure.config import get_config

        get_config().custom_groups = [
            CustomGroupConfig(
                group_name="异环",
                commands=[
                    CustomGroupCommand(
                        type="regex",
                        pattern="^nte帮助$",
                        examples=["nte帮助"],
                    )
                ],
            )
        ]
        _WakingCheckStage.handlers = [
            MockHandler("on_message", handler_module_path="builtin_commands")
        ]
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(
            event=mock_event, command="nte帮助", result_mode="background"
        )

        assert result["success"] is True, result
        assert result["dispatched"] is True
        assert _Scheduler.command_events[0].get_message_str() == "nte帮助"
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    async def test_blacklisted_command_not_dispatched(self, mock_event):
        """黑名单命令不调度后台执行。"""
        _WakingCheckStage.handlers = [
            MockHandler(
                "admin",
                event_filters=[MockCommandFilter("admin")],
                handler_module_path="admin_plugin.main",
            )
        ]
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False
        executor.cfg.ai_command_blacklist = {"admin_plugin"}

        result = await executor.execute(event=mock_event, command="/admin")

        assert result["success"] is False
        assert result["error"] == "blacklisted_plugin"
        assert not _Scheduler.started.is_set()

    @pytest.mark.asyncio
    async def test_custom_command_with_specific_blacklisted_handler_is_rejected(
        self, mock_event
    ):
        """custom 只豁免通用外部路由，真实具体黑名单 handler 仍必须拒绝。"""
        _WakingCheckStage.handlers = [
            MockHandler(
                "help",
                event_filters=[MockCommandFilter("help")],
                handler_module_path="blocked_plugin.main",
            )
        ]
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False
        executor.cfg.ai_command_blacklist = {"blocked_plugin"}

        result = await executor.execute(event=mock_event, command="/help")

        assert result["success"] is False
        assert result["error"] == "blacklisted_plugin"
        assert not _Scheduler.started.is_set()

    @pytest.mark.asyncio
    async def test_custom_regex_with_specific_blacklisted_handler_is_rejected(
        self, mock_event
    ):
        """正则目录条目不能绕过实际匹配到的具体黑名单 handler。"""
        executor = CommandExecutor()
        executor.cfg.custom_groups = [
            CustomGroupConfig(
                group_name="正则",
                commands=[
                    CustomGroupCommand(
                        type="regex",
                        pattern=r"^给\S+打卡$",
                        examples=["给橡皮糖打卡"],
                    )
                ],
            )
        ]
        _WakingCheckStage.handlers = [
            MockHandler(
                "checkin",
                event_filters=[SimpleNamespace(filter_type="regex")],
                handler_module_path="blocked_plugin.main",
            )
        ]
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False
        executor.cfg.ai_command_blacklist = {"blocked_plugin"}

        result = await executor.execute(event=mock_event, command="给橡皮糖打卡")

        assert result["execution_state"] == "rejected"
        assert result["error"] == "blacklisted_plugin"
        assert not _Scheduler.started.is_set()

    @pytest.mark.asyncio
    async def test_custom_regex_with_generic_handler_is_external_dispatched(
        self, mock_event, monkeypatch
    ):
        """正则目录条目仅命中通用 handler 时仍按外部路由受理。"""

        async def process_without_local_output(scheduler, event, from_stage=0) -> None:
            scheduler.command_events.append(event)
            scheduler.started.set()

        monkeypatch.setattr(_Scheduler, "_process_stages", process_without_local_output)
        executor = CommandExecutor()
        executor.cfg.custom_groups = [
            CustomGroupConfig(
                group_name="正则",
                commands=[
                    CustomGroupCommand(
                        type="regex",
                        pattern=r"^给\S+打卡$",
                        examples=["给橡皮糖打卡"],
                    )
                ],
            )
        ]
        _WakingCheckStage.handlers = [
            MockHandler(
                "on_message",
                event_filters=[SimpleNamespace(filter_type="event_message_type")],
                handler_module_path="external_router",
            )
        ]
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="给橡皮糖打卡")

        assert result["execution_state"] == "external_dispatched"
        assert result["dispatched"] is True
        assert len(_Scheduler.command_events) == 1

    @pytest.mark.asyncio
    async def test_invalid_actor_not_dispatched(self, mock_event):
        """非法 actor 不调度后台执行。"""
        executor = CommandExecutor()
        result = await executor.execute(event=mock_event, command="/help", actor="bot")
        assert result["success"] is False
        assert result["error"] == "invalid_actor"
        assert not _Scheduler.started.is_set()


class TestCommandResultListening:
    """AI tool 只监听本次 synthetic event 的命令输出。"""

    @pytest.mark.asyncio
    async def test_custom_generic_command_without_local_output_is_external_dispatch(
        self, mock_event, monkeypatch
    ):
        """外部路由命令无本地输出时，也必须明确告知 AI 已成功受理。"""

        async def process_without_local_output(scheduler, event, from_stage=0) -> None:
            scheduler.command_events.append(event)
            scheduler.started.set()
            await scheduler.release.wait()

        monkeypatch.setattr(_Scheduler, "_process_stages", process_without_local_output)
        _WakingCheckStage.handlers = [
            MockHandler("on_message", handler_module_path="external_router")
        ]
        _Scheduler.release.set()
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help")

        assert result["success"] is True
        assert result["dispatched"] is True
        assert result["result_type"] == "external_dispatched"
        assert result["execution_state"] == "external_dispatched"
        assert result["external_response_pending"] is True
        assert result["output_complete"] is False
        assert result["messages"] == []
        assert "不要重复调用" in result["message"]

    @pytest.mark.asyncio
    async def test_auto_returns_completed_messages_and_keeps_chat_delivery(
        self, mock_event
    ):
        """快速命令完成时，tool 与原聊天都能获得同一批结果。"""
        _Scheduler.release.set()
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help")

        assert result["success"] is True
        assert result["execution_state"] == "completed"
        assert result["output_complete"] is True
        assert result["messages"] == ["后台结果:/help"]
        assert any(m.get("type") == "result" for m in mock_event.sent_messages)

    @pytest.mark.asyncio
    async def test_auto_timeout_keeps_task_running_without_cancellation(
        self, mock_event
    ):
        """监听窗口结束只返回运行中，不会取消后台命令。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False
        executor.cfg.ai_command_auto_wait_seconds = 0.01

        result = await executor.execute(event=mock_event, command="/help")

        assert result["success"] is True
        assert result["execution_state"] == "accepted"
        assert result["output_complete"] is False
        assert _Scheduler.started.is_set()
        assert any(not task.cancelled() for task in executor._background_tasks)
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)
        assert any(m.get("type") == "result" for m in mock_event.sent_messages)

    @pytest.mark.asyncio
    async def test_background_waits_for_pipeline_start_handshake(self, mock_event):
        """background 不会在调度器尚未启动时虚报已运行。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(
            event=mock_event, command="/help", result_mode="background"
        )

        assert result["success"] is True
        assert result["execution_state"] == "accepted"
        assert _Scheduler.started.is_set()
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("result_mode", "wait_seconds"),
        [
            ("background", None),
            ("auto", None),
            ("custom", 0.01),
        ],
    )
    async def test_stalled_scheduler_initialization_is_accepted_without_retry(
        self, mock_event, monkeypatch, result_mode, wait_seconds
    ):
        """初始化卡住时，任意监听模式都不得永久等待或虚报已启动。"""
        initialization_release = asyncio.Event()

        async def blocked_initialize(scheduler):
            await initialization_release.wait()

        monkeypatch.setattr(_Scheduler, "initialize", blocked_initialize)
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False
        executor.cfg.ai_command_auto_wait_seconds = 0.01

        result = await executor.execute(
            event=mock_event,
            command="/help",
            result_mode=result_mode,
            wait_seconds=wait_seconds,
        )

        assert result["success"] is True
        assert result["execution_state"] == "accepted"
        assert result["result_type"] == "startup_pending"
        assert result["error"] is None
        assert result["dispatched"] is True
        assert result["output_complete"] is False
        assert result["retryable"] is False

        initialization_release.set()
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("wait_seconds", [None, 0, -1, float("inf"), "bad"])
    async def test_custom_rejects_invalid_wait_without_scheduling(
        self, mock_event, wait_seconds
    ):
        """非法 custom 等待参数在命令启动前明确拒绝。"""
        executor = CommandExecutor()

        result = await executor.execute(
            event=mock_event,
            command="/help",
            result_mode="custom",
            wait_seconds=wait_seconds,
        )

        assert result["success"] is False
        assert result["execution_state"] == "rejected"
        assert result["error"] == "invalid_wait_seconds"
        assert not _Scheduler.started.is_set()

    @pytest.mark.asyncio
    async def test_custom_rejects_value_above_configured_max_without_scheduling(
        self, mock_event
    ):
        """custom 不得以过大等待值实际启动命令。"""
        executor = CommandExecutor()
        executor.cfg.ai_command_max_wait_seconds = 0.1

        result = await executor.execute(
            event=mock_event,
            command="/help",
            result_mode="custom",
            wait_seconds=0.2,
        )

        assert result["success"] is False
        assert result["execution_state"] == "rejected"
        assert result["error"] == "wait_seconds_exceeds_max"
        assert not _Scheduler.started.is_set()

    @pytest.mark.asyncio
    async def test_custom_returns_completed_result(self, mock_event):
        """custom 在窗口内完成时返回完整捕获结果。"""
        _Scheduler.release.set()
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(
            event=mock_event,
            command="/help",
            result_mode="custom",
            wait_seconds=0.1,
        )

        assert result["execution_state"] == "completed"
        assert result["output_complete"] is True
        assert result["messages"] == ["后台结果:/help"]

    @pytest.mark.asyncio
    async def test_auto_captures_multiple_and_streaming_outputs_only_from_command_event(
        self, mock_event, monkeypatch
    ):
        """捕获多条及流式结果，但不会把全局主动发送误归因给命令。"""

        async def process_stages(scheduler, command_event, from_stage=0):
            scheduler.command_events.append(command_event)
            scheduler.started.set()
            await command_event.send("第一条")

            async def chunks():
                yield "流式一"
                yield "流式二"

            await command_event.send_streaming(chunks())
            await mock_event.send("不属于 synthetic event 的全局消息")

        monkeypatch.setattr(_Scheduler, "_process_stages", process_stages)
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help")

        assert result["execution_state"] == "completed"
        assert result["messages"] == ["第一条", "流式一", "流式二"]
        assert any(
            m.get("content") == "不属于 synthetic event 的全局消息"
            for m in mock_event.sent_messages
        )

    @pytest.mark.asyncio
    async def test_auto_marks_non_text_components_without_exposing_paths(
        self, mock_event, monkeypatch
    ):
        """图片等非文本输出只提供类型标记，不能泄露组件内的本地路径。"""

        class _Image:
            def __init__(self):
                self.path = "/private/tmp/secret.png"

        async def process_stages(scheduler, command_event, from_stage=0):
            scheduler.command_events.append(command_event)
            scheduler.started.set()
            await command_event.send(MockMessageEventResult(chain=[_Image()]))

        monkeypatch.setattr(_Scheduler, "_process_stages", process_stages)
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help")

        assert result["messages"] == ["[_Image]"]
        assert "/private/tmp/secret.png" not in str(result["messages"])

    @pytest.mark.asyncio
    async def test_auto_reports_background_exception_observed_in_window(
        self, mock_event, monkeypatch
    ):
        """等待窗口内的后台异常必须向 tool 暴露真实失败原因。"""

        async def process_stages(scheduler, command_event, from_stage=0):
            scheduler.command_events.append(command_event)
            scheduler.started.set()
            raise RuntimeError("模拟处理器失败")

        monkeypatch.setattr(_Scheduler, "_process_stages", process_stages)
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help")

        assert result["success"] is False
        assert result["execution_state"] == "failed"
        assert "模拟处理器失败" in result["error"]

    @pytest.mark.asyncio
    async def test_auto_reports_stopped_pipeline_error_without_false_success(
        self, mock_event, monkeypatch
    ):
        """AstrBot 吞掉 handler 异常并停止事件时，tool 必须如实返回失败。"""

        async def process_stages(scheduler, command_event, from_stage=0):
            scheduler.command_events.append(command_event)
            scheduler.started.set()
            command_event.set_extra("handler_error", "模拟 handler 执行失败")
            command_event.stop_event()

        monkeypatch.setattr(_Scheduler, "_process_stages", process_stages)
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help")

        assert result["success"] is False
        assert result["execution_state"] == "failed"
        assert result["result_type"] == "failed"
        assert "模拟 handler 执行失败" in result["error"]

    @pytest.mark.asyncio
    async def test_streaming_capture_keeps_concurrent_normal_send(
        self, mock_event, monkeypatch
    ):
        """流式发送期间，并行普通 send 仍属于同一事件且必须被捕获。"""

        async def process_stages(scheduler, command_event, from_stage=0):
            scheduler.command_events.append(command_event)
            scheduler.started.set()
            send_normal = asyncio.Event()

            async def concurrent_normal_send():
                await send_normal.wait()
                await command_event.send("并行普通输出")

            normal_task = asyncio.create_task(concurrent_normal_send())

            async def chunks():
                yield "流式一"
                send_normal.set()
                await normal_task
                yield "流式二"

            await command_event.send_streaming(chunks())

        monkeypatch.setattr(_Scheduler, "_process_stages", process_stages)
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help")

        assert result["execution_state"] == "completed"
        assert result["messages"] == ["流式一", "并行普通输出", "流式二"]
