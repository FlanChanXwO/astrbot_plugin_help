"""Tests for the `actor` parameter of execute_astrbot_command / CommandExecutor.execute()."""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
    MockContext,
    MockHandler,
    MockMessageEventResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _setup_singletons():
    """Set up required singletons before each test, tear down after."""
    # 初始化运行上下文，保证执行器能读取配置和 AstrBot mock。
    mock_ctx = MockContext()
    set_context(mock_ctx)

    # 使用真实临时目录，避免持久化路径相关测试误写插件目录。
    tmpdir = tempfile.TemporaryDirectory()
    plugin_dir = Path(tmpdir.name) / "astrbot_plugin_helpinfo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    init_plugin_paths(plugin_dir)

    # 将 AstrBot 数据目录固定到临时目录，测试结束后统一清理。
    data_dir = Path(tmpdir.name) / "data" / "plugin_data" / "astrbot_plugin_helpinfo"
    data_dir.mkdir(parents=True, exist_ok=True)
    patcher = patch(
        "astrbot.api.star.StarTools.get_data_dir",
        return_value=data_dir,
    )
    patcher.start()

    # init_config(None) 会创建默认配置。
    cfg = init_config(None)
    cfg.custom_groups = [
        CustomGroupConfig(
            group_name="测试分组",
            commands=[CustomGroupCommand(command="help", description="测试命令")],
        )
    ]
    executor_module.MessageEventResult = MockMessageEventResult

    yield

    # 清理单例，避免跨测试污染。
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


@pytest.fixture
def executor():
    """Provide a fresh CommandExecutor singleton."""
    reset_command_executor()
    return CommandExecutor()


# ---------------------------------------------------------------------------
# Tests: Signature & default
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests: Early returns (before any chat I/O) — both actors
# ---------------------------------------------------------------------------


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
        """Empty command returns error for self actor."""
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
        """Recursive call blocked for self actor."""
        executor = CommandExecutor()
        result = await executor.execute(
            event=mock_event, command="execute_astrbot_command", actor="self"
        )
        assert result["success"] is False
        assert result["error"] == "recursive_call_blocked"


# ---------------------------------------------------------------------------
# Tests: actor="user" — chat output + full data
# ---------------------------------------------------------------------------


class TestActorUserChatOutput:
    """actor='user' sends notifications and results to chat."""

    @pytest.mark.asyncio
    async def test_pre_notification_sent(self, mock_event):
        """Pre-exec notification IS sent for user actor."""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = True

        with patch.object(
            executor, "_execute_command_via_pipeline", new_callable=AsyncMock
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "matched_handlers": [
                    MockHandler("on_message", handler_module_path="builtin_commands"),
                    MockHandler(
                        "forward_help", handler_module_path="help_forward_adapter"
                    ),
                ],
                "messages": [],
                "raw_results": [],
                "result_type": "none",
                "stopped": False,
                "had_result": False,
            }
            await executor.execute(event=mock_event, command="/help", actor="user")

        pre_msgs = [
            m
            for m in mock_event.sent_messages
            if "正在执行命令" in str(m.get("content", ""))
        ]
        assert len(pre_msgs) == 1

    @pytest.mark.asyncio
    async def test_result_notification_sent(self, mock_event):
        """Post-exec notification IS sent for user actor."""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_result = True

        with patch.object(
            executor, "_execute_command_via_pipeline", new_callable=AsyncMock
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "matched_handlers": [
                    MockHandler("on_message", handler_module_path="builtin_commands"),
                    MockHandler(
                        "forward_help", handler_module_path="help_forward_adapter"
                    ),
                ],
                "messages": ["output"],
                "raw_results": [],
                "result_type": "message",
                "stopped": False,
                "had_result": True,
            }
            result = await executor.execute(
                event=mock_event, command="/help", actor="user"
            )

        assert result["success"] is True, result
        result_msgs = [
            m
            for m in mock_event.sent_messages
            if str(m.get("content", "")).startswith("执行命令")
        ]
        assert len(result_msgs) == 1

    @pytest.mark.asyncio
    async def test_raw_result_forwarding(self, mock_event):
        """Raw results ARE sent to chat for user actor."""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_result = False
        executor.cfg.enable_ai_command_notify = False

        mock_result = MockMessageEventResult()
        mock_result.chain = True

        with patch.object(
            executor, "_execute_command_via_pipeline", new_callable=AsyncMock
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "matched_handlers": [
                    MockHandler("on_message", handler_module_path="builtin_commands"),
                    MockHandler(
                        "forward_help", handler_module_path="help_forward_adapter"
                    ),
                ],
                "messages": ["result"],
                "raw_results": [mock_result],
                "result_type": "chain",
                "stopped": False,
                "had_result": True,
            }
            result = await executor.execute(
                event=mock_event, command="/help", actor="user"
            )

        assert result["success"] is True, result
        assert any(m.get("type") == "result" for m in mock_event.sent_messages)

    @pytest.mark.asyncio
    async def test_returns_full_data(self, mock_event):
        """User actor returns complete result dict."""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_result = False
        executor.cfg.enable_ai_command_notify = False

        with patch.object(
            executor, "_execute_command_via_pipeline", new_callable=AsyncMock
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "matched_handlers": [
                    MockHandler("on_message", handler_module_path="builtin_commands"),
                    MockHandler(
                        "forward_help", handler_module_path="help_forward_adapter"
                    ),
                ],
                "messages": ["a", "b"],
                "raw_results": [],
                "result_type": "message",
                "stopped": False,
                "had_result": True,
            }
            result = await executor.execute(
                event=mock_event, command="/help", actor="user"
            )

        assert result["command"] == "/help"
        assert result["success"] is True, result
        assert len(result["matched_handlers"]) == 2
        assert len(result["messages"]) == 2
        assert result["result_type"] == "message"


# ---------------------------------------------------------------------------
# Tests: actor="self" — chat output + full data (same as "user")
# ---------------------------------------------------------------------------


class TestActorSelfChatOutput:
    """actor='self' also sends notifications and results to chat, same as 'user'."""

    @pytest.mark.asyncio
    async def test_pre_notification_sent(self, mock_event):
        """Pre-exec notification IS sent for self actor."""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_notify = True

        with patch.object(
            executor, "_execute_command_via_pipeline", new_callable=AsyncMock
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "matched_handlers": [
                    MockHandler("on_message", handler_module_path="builtin_commands"),
                    MockHandler(
                        "forward_help", handler_module_path="help_forward_adapter"
                    ),
                ],
                "messages": [],
                "raw_results": [],
                "result_type": "none",
                "stopped": False,
                "had_result": False,
            }
            await executor.execute(event=mock_event, command="/help", actor="self")

        pre_msgs = [
            m
            for m in mock_event.sent_messages
            if "正在执行命令" in str(m.get("content", ""))
        ]
        assert len(pre_msgs) == 1

    @pytest.mark.asyncio
    async def test_result_notification_sent(self, mock_event):
        """Post-exec notification IS sent for self actor."""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_result = True

        with patch.object(
            executor, "_execute_command_via_pipeline", new_callable=AsyncMock
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "matched_handlers": [
                    MockHandler("on_message", handler_module_path="builtin_commands"),
                    MockHandler(
                        "forward_help", handler_module_path="help_forward_adapter"
                    ),
                ],
                "messages": ["output"],
                "raw_results": [],
                "result_type": "message",
                "stopped": False,
                "had_result": True,
            }
            result = await executor.execute(
                event=mock_event, command="/help", actor="self"
            )

        assert result["success"] is True, result
        result_msgs = [
            m
            for m in mock_event.sent_messages
            if str(m.get("content", "")).startswith("执行命令")
        ]
        assert len(result_msgs) == 1

    @pytest.mark.asyncio
    async def test_raw_result_forwarding(self, mock_event):
        """Raw results ARE sent to chat for self actor."""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_result = False
        executor.cfg.enable_ai_command_notify = False

        mock_result = MockMessageEventResult()
        mock_result.chain = True

        with patch.object(
            executor, "_execute_command_via_pipeline", new_callable=AsyncMock
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "matched_handlers": [
                    MockHandler("on_message", handler_module_path="builtin_commands"),
                    MockHandler(
                        "forward_help", handler_module_path="help_forward_adapter"
                    ),
                ],
                "messages": ["result"],
                "raw_results": [mock_result],
                "result_type": "chain",
                "stopped": False,
                "had_result": True,
            }
            result = await executor.execute(
                event=mock_event, command="/help", actor="self"
            )

        assert result["success"] is True, result
        assert any(m.get("type") == "result" for m in mock_event.sent_messages)

    @pytest.mark.asyncio
    async def test_returns_full_data(self, mock_event):
        """Self actor returns complete result dict."""
        executor = CommandExecutor()
        executor.cfg.enable_ai_command_result = False
        executor.cfg.enable_ai_command_notify = False

        with patch.object(
            executor, "_execute_command_via_pipeline", new_callable=AsyncMock
        ) as mock_pipeline:
            mock_pipeline.return_value = {
                "matched_handlers": [
                    MockHandler("on_message", handler_module_path="builtin_commands"),
                    MockHandler(
                        "forward_help", handler_module_path="help_forward_adapter"
                    ),
                ],
                "messages": ["a", "b"],
                "raw_results": [],
                "result_type": "message",
                "stopped": False,
                "had_result": True,
            }
            result = await executor.execute(
                event=mock_event, command="/help", actor="self"
            )

        assert result["command"] == "/help"
        assert result["success"] is True, result
        assert len(result["matched_handlers"]) == 2
        assert len(result["messages"]) == 2
        assert result["result_type"] == "message"
