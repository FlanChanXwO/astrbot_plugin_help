"""响应 DTO 定义"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class SearchCommandResponse:
    """搜索命令响应"""

    success: bool
    message: str
    command_count: int = 0
    plugin_count: int = 0
    command_prefix: list[str] = field(default_factory=list)
    plugins: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)

    @classmethod
    def success_response(
        cls,
        message: str,
        command_count: int,
        plugin_count: int,
        command_prefix: list[str],
        plugins: list[dict],
    ) -> SearchCommandResponse:
        """创建成功响应"""
        return cls(
            success=True,
            message=message,
            command_count=command_count,
            plugin_count=plugin_count,
            command_prefix=command_prefix,
            plugins=plugins,
        )

    @classmethod
    def error_response(cls, message: str) -> SearchCommandResponse:
        """创建错误响应"""
        return cls(
            success=False,
            message=message,
            error=message,
        )


@dataclass
class CommandDetailResponse:
    """命令详情响应"""

    success: bool
    message: str
    command: dict = field(default_factory=dict)
    similar_commands: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)

    @classmethod
    def from_command_entry(
        cls, entry: dict, similar: list[str]
    ) -> CommandDetailResponse:
        """从命令条目创建响应"""
        return cls(
            success=True,
            message=f"找到命令 '{entry.get('command', '')}' 的详细信息",
            command=entry,
            similar_commands=similar,
        )

    @classmethod
    def error_response(
        cls, message: str, suggestions: list[str] | None = None
    ) -> CommandDetailResponse:
        """创建错误响应"""
        return cls(
            success=False,
            message=message,
            error=message,
            similar_commands=suggestions or [],
        )


@dataclass
class ExecuteCommandResponse:
    """执行命令响应"""

    success: bool
    command: str
    message: str
    matched_handlers: list[dict] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    result_type: str = "none"
    error: str | None = None
    suggestions: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)

    @classmethod
    def success_response(
        cls,
        command: str,
        message: str,
        matched_handlers: list,
        messages: list[str],
        result_type: str,
    ) -> ExecuteCommandResponse:
        """创建成功响应"""
        return cls(
            success=True,
            command=command,
            message=message,
            matched_handlers=[
                h.to_dict() if hasattr(h, "to_dict") else {"name": str(h)}
                for h in matched_handlers
            ],
            messages=messages,
            result_type=result_type,
        )

    @classmethod
    def error_response(
        cls,
        command: str,
        message: str,
        error: str,
        matched_handlers: list = None,
        messages: list[str] = None,
        result_type: str = "none",
        suggestions: list[str] = None,
    ) -> ExecuteCommandResponse:
        """创建错误响应"""
        return cls(
            success=False,
            command=command,
            message=message,
            error=error,
            matched_handlers=[
                h.to_dict() if hasattr(h, "to_dict") else {"name": str(h)}
                for h in (matched_handlers or [])
            ],
            messages=messages or [],
            result_type=result_type,
            suggestions=suggestions or [],
        )


@dataclass
class ListCustomGroupsResponse:
    """列出自定义分组响应"""

    success: bool
    group_count: int = 0
    groups: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)
