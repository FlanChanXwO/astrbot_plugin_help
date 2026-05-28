"""Mock AstrMessageEvent for testing."""

from __future__ import annotations

from unittest.mock import MagicMock


class MockMessageEventResult:
    """模拟 AstrBot MessageEventResult，用于测试结果投递。"""

    def __init__(self, chain=None):
        self.chain = chain


class MockAstrMessageEvent:
    """模拟 AstrMessageEvent 用于测试."""

    def __init__(
        self,
        message: str = "",
        user_id: str = "123456",
        group_id: str | None = None,
        is_admin_flag: bool = False,
        platform_name: str = "aiocqhttp",
    ):
        self.message = message
        self.user_id = user_id
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

        # 记录发送的消息
        self.sent_messages: list[dict] = []

    def get_message_str(self) -> str:
        """获取消息字符串."""
        return self.message

    def is_admin(self) -> bool:
        """检查是否为管理员."""
        return self._is_admin

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
        return f"msg_id_{len(self.sent_messages)}"

    async def remove_message_event(self, msg_id: str) -> bool:
        """撤回消息."""
        return True

    def clear_result(self) -> None:
        """清理事件结果，模拟 AstrBot 事件复用前置操作。"""

    def clear_extra(self) -> None:
        """清理事件扩展字段，模拟 AstrBot 事件复用前置操作。"""

    def get_sender_id(self) -> str:
        """获取发送者 ID."""
        return self.user_id

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
