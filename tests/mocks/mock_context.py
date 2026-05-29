"""Mock Context and related classes for testing."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


class MockStar:
    """模拟 Star (插件) 对象."""

    def __init__(
        self,
        name: str = "test_plugin",
        display_name: str | None = None,
        version: str = "v1.0.0",
        desc: str = "Test plugin description",
        module_path: str = "test_plugin.main",
        activated: bool = True,
    ):
        self.name = name
        self.display_name = display_name or name
        self.version = version
        self.desc = desc
        self.module_path = module_path
        self.activated = activated
        self.root_dir_name = name


class MockFilter:
    """基础过滤器模拟."""

    def __init__(self, filter_type: str):
        self.filter_type = filter_type


class MockCommandFilter(MockFilter):
    """模拟 CommandFilter."""

    def __init__(
        self,
        command_name: str,
        alias: set[str] | None = None,
        parent_command_names: list[str] | None = None,
    ):
        super().__init__("command")
        self.command_name = command_name
        self.alias = alias or set()
        self.parent_command_names = parent_command_names


class MockCommandGroupFilter(MockCommandFilter):
    """模拟 CommandGroupFilter."""

    def __init__(self, group_name: str, alias: set[str] | None = None):
        super().__init__(group_name, alias)
        self.group_name = group_name


class MockRegexFilter(MockFilter):
    """模拟 RegexFilter."""

    def __init__(self, regex: str):
        super().__init__("regex")
        self.regex_str = regex


class MockPermissionFilter(MockFilter):
    """模拟 PermissionTypeFilter."""

    def __init__(self, permission_type: str = "admin"):
        super().__init__("permission")
        self.permission_type = permission_type


class MockHandler:
    """模拟 StarHandlerMetadata."""

    def __init__(
        self,
        handler_name: str,
        event_filters: list | None = None,
        desc: str = "",
        handler_module_path: str = "",
        event_type: Any | None = None,
        extras_configs: dict | None = None,
    ):
        self.handler_name = handler_name
        self.event_filters = event_filters or []
        self.desc = desc
        self.handler_module_path = handler_module_path
        self.event_type = event_type
        self.extras_configs = extras_configs or {}
        self.handler_full_name = f"{handler_module_path}.{handler_name}"
        self.handler = None


class MockContext:
    """模拟 AstrBot Context."""

    def __init__(self):
        self._stars: list[MockStar] = []
        self._handlers: list[MockHandler] = []
        self._config = MagicMock()
        self.sent_messages: list[dict] = []

    def add_star(self, star: MockStar) -> None:
        """添加插件."""
        self._stars.append(star)

    def add_handler(self, handler: MockHandler) -> None:
        """添加处理器."""
        self._handlers.append(handler)

    def get_all_stars(self) -> list[MockStar]:
        """获取所有插件."""
        return self._stars

    def get_config(self, umo: str | None = None) -> dict:
        """获取配置."""
        return {
            "admins_id": [],
            "plugin_set": ["*"],
            "wake_prefix": ["/"],
            "platform_settings": {
                "ignore_bot_self_message": True,
                "no_permission_reply": True,
            },
            "provider_settings": {
                "enable": False,
                "identifier": "",
                "prompt_prefix": "",
            },
        }

    def get_llm_tool_manager(self) -> Any | None:
        """获取 LLM 工具管理器."""
        return None

    async def send_message(self, session: str, message_chain) -> bool:
        """模拟 AstrBot 主动发送消息。"""
        self.sent_messages.append({"session": session, "message_chain": message_chain})
        return True
