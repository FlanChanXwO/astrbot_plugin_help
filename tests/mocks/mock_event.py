"""Mock AstrMessageEvent for testing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


class MockMessageEventResult:
    """模拟 AstrBot MessageEventResult，用于测试结果投递。"""

    def __init__(self, chain=None):
        self.chain = chain or []
        self.result_content_type = None
        self.async_stream = None
        self.use_t2i_ = None
        self.use_markdown_ = None

    def is_stopped(self) -> bool:
        """模拟结果未终止事件。"""
        return False

    def get_plain_text(self, with_other_comps_mark: bool = False) -> str:
        """提取纯文本内容。"""
        return " ".join(str(item) for item in self.chain)

    def derive(self, chain=None):
        """创建派生消息结果。"""
        return MockMessageEventResult(chain=chain or [])

    def is_model_result(self) -> bool:
        """测试中默认不是模型结果。"""
        return False


class MockAstrMessageEvent:
    """模拟 AstrMessageEvent 用于测试."""

    def __init__(
        self,
        message: str = "",
        user_id: str = "123456",
        self_id: str = "bot_self",
        group_id: str | None = None,
        is_admin_flag: bool = False,
        platform_name: str = "aiocqhttp",
    ):
        self.message = message
        self.user_id = user_id
        self.self_id = self_id
        self.group_id = group_id
        self._is_admin = is_admin_flag
        self.platform_name = platform_name
        self.unified_msg_origin = (
            f"{platform_name}:{group_id}:{user_id}"
            if group_id
            else f"{platform_name}:{user_id}"
        )
        self.session_id = self.unified_msg_origin

        # Mock 消息组件
        self.message_obj = MagicMock()
        self.message_obj.get_message_str.return_value = message
        self.message_obj.message_str = message
        self.message_obj.message = [message]
        self.message_obj.self_id = self_id
        self.message_obj.sender = SimpleNamespace(user_id=user_id, nickname="tester")
        self.message_obj.type = "friend"
        self.message_obj.group_id = group_id or ""
        self.message_obj.message_id = "mock-message-id"
        self.message_str = message
        self.role = "admin" if is_admin_flag else "member"
        self.is_wake = False
        self.is_at_or_wake_command = False
        self.call_llm = False
        self.plugins_name = None
        self._extras: dict = {}
        self._result = None
        self._force_stopped = False
        self._has_send_oper = False

        # 记录发送的消息
        self.sent_messages: list[dict] = []

    def get_message_str(self) -> str:
        """获取消息字符串."""
        return self.message_str

    def is_admin(self) -> bool:
        """检查是否为管理员."""
        return self.role == "admin" or self._is_admin

    def get_platform_name(self) -> str:
        """获取平台名称."""
        return self.platform_name

    def get_platform_id(self) -> str:
        """获取平台 ID."""
        return self.platform_name

    def get_messages(self) -> list:
        """获取消息链."""
        return self.message_obj.message

    def get_message_type(self):
        """获取消息类型."""
        return self.message_obj.type

    def is_private_chat(self) -> bool:
        """测试默认按私聊处理."""
        return not self.group_id

    def get_sender_name(self) -> str:
        """获取发送者昵称."""
        return getattr(self.message_obj.sender, "nickname", "") or ""

    async def plain_result(self, text: str) -> str:
        """发送纯文本结果."""
        self.sent_messages.append({"type": "plain", "content": text})
        return f"msg_id_{len(self.sent_messages)}"

    async def chain_result(self, components: list) -> str:
        """发送消息链结果."""
        self.sent_messages.append({"type": "chain", "components": components})
        return f"msg_id_{len(self.sent_messages)}"

    async def send(self, result) -> str:
        """发送 AstrBot 执行结果."""
        self.sent_messages.append({"type": "result", "content": result})
        self._has_send_oper = True
        return f"msg_id_{len(self.sent_messages)}"

    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        """模拟流式发送."""
        async for result in generator:
            await self.send(result)

    def cleanup_temporary_local_files(self) -> None:
        """测试中不需要清理临时文件。"""

    async def remove_message_event(self, msg_id: str) -> bool:
        """撤回消息."""
        return True

    def clear_result(self) -> None:
        """清理事件结果，模拟 AstrBot 事件复用前置操作。"""
        self._result = None

    def clear_extra(self) -> None:
        """清理事件扩展字段，模拟 AstrBot 事件复用前置操作。"""
        self._extras.clear()

    def set_extra(self, key, value) -> None:
        """设置事件扩展字段."""
        self._extras[key] = value

    def get_extra(self, key: str | None = None, default=None):
        """获取事件扩展字段."""
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def set_result(self, result) -> None:
        """设置事件结果."""
        self._result = result

    def get_result(self):
        """获取事件结果."""
        return self._result

    def stop_event(self) -> None:
        """终止事件."""
        self._force_stopped = True

    def is_stopped(self) -> bool:
        """检查事件是否终止."""
        return self._force_stopped

    def get_sender_id(self) -> str:
        """获取发送者 ID."""
        return getattr(self.message_obj.sender, "user_id", self.user_id)

    def get_self_id(self) -> str:
        """获取机器人自身 ID."""
        return self.message_obj.self_id

    def get_group_id(self) -> str | None:
        """获取群组 ID."""
        return self.group_id


class MockImage:
    """模拟 Image 消息组件."""

    def __init__(self, path: str):
        self.path = path

    @classmethod
    def fromFileSystem(cls, path: str) -> MockImage:
        """从文件系统创建图片."""
        return cls(path)
