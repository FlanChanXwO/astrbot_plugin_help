"""execute_astrbot_command 的 actor 与异步调度测试。"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
from pathlib import Path
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

from tests.mocks import MockAstrMessageEvent, MockContext, MockHandler


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


class _WakingCheckStage:
    """按测试事件上的 extra 模拟 AstrBot 唤醒阶段。"""

    handlers = [MockHandler("help", handler_module_path="help_plugin")]

    async def initialize(self, ctx) -> None:
        self.ctx = ctx

    async def process(self, event) -> None:
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
        MockHandler("help", handler_module_path="help_plugin")
    ]
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
    async def test_user_actor_returns_after_dispatch(self, mock_event):
        """user actor 调度后立即返回，后台稍后发送结果。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help", actor="user")
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

        result = await executor.execute(event=mock_event, command="/help", actor="user")

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

        await executor.execute(event=mock_event, command="/help", actor="user")

        assert any(
            "正在执行命令" in str(m.get("content", ""))
            for m in mock_event.sent_messages
        )
        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)


class TestActorSelf:
    """actor=self 的安全开关与身份改写。"""

    @pytest.mark.asyncio
    async def test_self_actor_disabled_by_default(self, mock_event):
        """self actor 默认禁用。"""
        executor = CommandExecutor()
        result = await executor.execute(event=mock_event, command="/help", actor="self")
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

        result = await executor.execute(event=mock_event, command="/help", actor="self")
        await asyncio.sleep(0)

        assert result["success"] is True, result
        command_event = _Scheduler.command_events[0]
        assert command_event.get_sender_id() == mock_event.get_self_id()
        assert mock_event.get_sender_id() == "123456"

        _Scheduler.release.set()
        await asyncio.gather(*executor._background_tasks)
        assert any(m.get("type") == "result" for m in mock_event.sent_messages)

    @pytest.mark.asyncio
    async def test_user_actor_keeps_sender(self, mock_event):
        """user actor 不改写发送者。"""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = False
        executor.cfg.enable_ai_command_result = False

        result = await executor.execute(event=mock_event, command="/help", actor="user")
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
    async def test_blacklisted_command_not_dispatched(self, mock_event):
        """黑名单命令不调度后台执行。"""
        _WakingCheckStage.handlers = [
            MockHandler("admin", handler_module_path="admin_plugin.main")
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
    async def test_invalid_actor_not_dispatched(self, mock_event):
        """非法 actor 不调度后台执行。"""
        executor = CommandExecutor()
        result = await executor.execute(event=mock_event, command="/help", actor="bot")
        assert result["success"] is False
        assert result["error"] == "invalid_actor"
        assert not _Scheduler.started.is_set()
