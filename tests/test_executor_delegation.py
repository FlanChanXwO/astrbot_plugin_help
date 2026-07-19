"""synthetic 委托事件及通用 handler 分类回归。"""

from __future__ import annotations

from types import SimpleNamespace
import asyncio

import pytest

from src.infrastructure.analysis.executor import CommandExecutor
from tests.mocks import MockCommandFilter, MockHandler


def test_synthetic_target_changes_sender_but_preserves_requester_role(
    mock_event,
) -> None:
    """目标只承载真实 sender，不能替换原请求者权限。"""
    mock_event.role = "admin"
    executor = CommandExecutor.__new__(CommandExecutor)

    delegated = executor._build_command_event(
        mock_event,
        "/打卡",
        target_user_id="target-2",
        target_user_name="橡皮糖",
    )

    assert delegated.get_sender_id() == "target-2"
    assert delegated.get_sender_name() == "橡皮糖"
    assert delegated.role == "admin"
    assert delegated.get_extra("_helpinfo_synthetic_command") is True
    assert delegated.get_extra("_helpinfo_original_requester_id") == "123456"
    assert delegated.unified_msg_origin == mock_event.unified_msg_origin
    # AstrBot 4.26.6 中 True 表示禁止 ProcessStage 末尾的默认 LLM 请求。
    assert delegated.call_llm is True


def test_generic_handler_is_determined_by_filter_types_not_handler_name() -> None:
    """任意名字的消息监听器都属通用；有命令过滤器则属具体处理器。"""
    generic = MockHandler(
        "wakepro_on_group_msg",
        event_filters=[SimpleNamespace(filter_type="event_message_type")],
    )
    specific = MockHandler(
        "on_message",
        event_filters=[MockCommandFilter("打卡")],
    )

    assert CommandExecutor.is_generic_handler(generic) is True
    assert CommandExecutor.is_generic_handler(specific) is False


@pytest.mark.asyncio
async def test_shutdown_cancels_and_gathers_pending_background_tasks() -> None:
    """插件终止必须回收仍绑定旧 context 的 synthetic 后台任务。"""
    executor = CommandExecutor.__new__(CommandExecutor)
    executor._background_tasks = set()
    blocker = asyncio.Event()

    async def pending() -> None:
        await blocker.wait()

    task = asyncio.create_task(pending())
    executor._background_tasks.add(task)

    await executor.shutdown()

    assert task.cancelled()
    assert executor._background_tasks == set()
