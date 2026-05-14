"""Pytest configuration and shared fixtures for astrbot_plugin_help tests."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# 模拟 AstrBot 相关导入
sys.modules["astrbot"] = MagicMock()
sys.modules["astrbot.api"] = MagicMock()
sys.modules["astrbot.api.event"] = MagicMock()
sys.modules["astrbot.api.star"] = MagicMock()
sys.modules["astrbot.api.message_components"] = MagicMock()
sys.modules["astrbot.core"] = MagicMock()
sys.modules["astrbot.core.message"] = MagicMock()
sys.modules["astrbot.core.message.components"] = MagicMock()
sys.modules["astrbot.core.message.message_event_result"] = MagicMock()
sys.modules["astrbot.core.agent"] = MagicMock()
sys.modules["astrbot.core.agent.mcp_client"] = MagicMock()
sys.modules["astrbot.core.star"] = MagicMock()
sys.modules["astrbot.core.star.filter"] = MagicMock()
sys.modules["astrbot.core.star.filter.command"] = MagicMock()
sys.modules["astrbot.core.star.filter.command_group"] = MagicMock()
sys.modules["astrbot.core.star.filter.event_message_type"] = MagicMock()
sys.modules["astrbot.core.star.filter.regex"] = MagicMock()
sys.modules["astrbot.core.star.filter.permission"] = MagicMock()
sys.modules["astrbot.core.star.filter.platform_adapter_type"] = MagicMock()
sys.modules["astrbot.core.star.star_handler"] = MagicMock()
sys.modules["astrbot.core.pipeline"] = MagicMock()
sys.modules["astrbot.core.pipeline.context"] = MagicMock()
sys.modules["astrbot.core.pipeline.waking_check"] = MagicMock()
sys.modules["astrbot.core.pipeline.waking_check.stage"] = MagicMock()
sys.modules["astrbot.core.pipeline.process_stage"] = MagicMock()
sys.modules["astrbot.core.pipeline.process_stage.stage"] = MagicMock()

from tests.mocks import (
    MockAstrMessageEvent,
    MockCommandFilter,
    MockContext,
    MockDataFactory,
    MockHandler,
    MockHtmlRenderer,
    MockRegexFilter,
    MockStar,
)


@pytest.fixture
def event_loop():
    """创建事件循环."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_event():
    """提供默认的 MockAstrMessageEvent 实例."""
    return MockAstrMessageEvent()


@pytest.fixture
def mock_admin_event():
    """提供管理员 MockAstrMessageEvent 实例."""
    return MockAstrMessageEvent(is_admin_flag=True)


@pytest.fixture
def mock_context():
    """提供 MockContext 实例."""
    return MockContext()


@pytest.fixture
def mock_renderer():
    """提供 MockHtmlRenderer 实例."""
    return MockHtmlRenderer()


@pytest.fixture
def mock_data_factory():
    """提供 MockDataFactory 实例."""
    return MockDataFactory()


@pytest.fixture
def temp_data_dir():
    """提供临时数据目录."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_plugin_config():
    """提供示例插件配置."""
    return MockDataFactory.create_config()


@pytest.fixture
def test_patterns():
    """提供测试用的正则模式."""
    return MockDataFactory.get_test_patterns()


@pytest.fixture
def sample_commands():
    """提供示例命令列表."""
    return [
        MockDataFactory.create_command_entry("/help", "显示帮助"),
        MockDataFactory.create_command_entry("/status", "查看状态"),
        MockDataFactory.create_regex_entry(
            r"^来.*色图$",
            "获取色图",
            examples=["来份色图", "来张涩图"],
        ),
        MockDataFactory.create_command_entry(
            "/admin",
            "管理员命令",
            tag="admin",
        ),
    ]


@pytest.fixture
def mock_plugins_with_handlers():
    """提供带处理器的模拟插件."""
    context = MockContext()

    # 插件 1: 基础命令
    star1 = MockStar(
        name="test_plugin",
        display_name="测试插件",
        version="v1.0.0",
        desc="测试用插件",
        module_path="test_plugin.main",
    )
    context.add_star(star1)

    # 普通命令
    handler1 = MockHandler(
        handler_name="help_cmd",
        event_filters=[MockCommandFilter("help")],
        desc="显示帮助信息",
        handler_module_path="test_plugin.main",
    )
    context.add_handler(handler1)

    # 带别名的命令
    handler2 = MockHandler(
        handler_name="status_cmd",
        event_filters=[MockCommandFilter("status", alias={"st"})],
        desc="查看状态",
        handler_module_path="test_plugin.main",
    )
    context.add_handler(handler2)

    # 正则命令
    handler3 = MockHandler(
        handler_name="setu_cmd",
        event_filters=[MockRegexFilter(r"^来.*色图$")],
        desc="获取色图",
        handler_module_path="test_plugin.main",
    )
    context.add_handler(handler3)

    # 插件 2: 管理员命令
    star2 = MockStar(
        name="admin_plugin",
        display_name="管理插件",
        version="v1.0.0",
        module_path="admin_plugin.main",
    )
    context.add_star(star2)

    handler4 = MockHandler(
        handler_name="admin_cmd",
        event_filters=[
            MockCommandFilter("admin"),
        ],
        desc="管理员命令",
        handler_module_path="admin_plugin.main",
    )
    context.add_handler(handler4)

    return context


class AsyncContextManagerMock:
    """异步上下文管理器 mock."""

    def __init__(self, return_value: Any = None):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def async_context_mock():
    """提供 AsyncContextManagerMock 工厂."""
    return AsyncContextManagerMock
