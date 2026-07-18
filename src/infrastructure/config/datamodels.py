"""Configuration data models.

Contains Pydantic models for plugin configuration.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ...shared.constants import DefaultCFG


class RegexConfig(BaseModel):
    """正则触发器配置"""

    max_examples: int = Field(
        default=DefaultCFG.REGEX_MAX_EXAMPLES, description="每条正则最多示例数"
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RegexConfig:
        """从字典创建配置"""
        if not data:
            return cls()
        return cls.model_validate({**cls().model_dump(), **(data or {})})


class CustomGroupCommand(BaseModel):
    """自定义分组中的单个命令配置"""

    command: str = Field(default="", description="命令名称")
    type: str = Field(default="command", description="类型：command 或 regex")
    description: str = Field(default="", description="命令描述")
    is_admin: bool = Field(default=False, description="是否需要管理员权限")
    permission_level: str = Field(default="normal", description="权限等级")
    delegation_policy: str = Field(default="normal", description="跨用户委托策略")
    history_mode: str = Field(default="command", description="历史记录模式")
    hidden: bool = Field(default=False, description="是否隐藏")
    aliases: list[str] = Field(
        default_factory=list, description="命令别名列表（供AI识别）"
    )
    pattern: str = Field(default="", description="正则模式")
    examples: list[str] = Field(default_factory=list, description="示例列表")
    sub_commands: list[str] = Field(default_factory=list, description="子命令列表")
    linked_plugin: str | None = Field(default=None, description="显式关联的插件")
    availability: str = Field(default="available", description="关联插件可用状态")

    @model_validator(mode="before")
    @classmethod
    def normalize_permission_compatibility(cls, data: Any) -> Any:
        """兼容旧 is_admin，同时让新 permission_level 成为权威字段。"""
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "permission_level" not in normalized:
            normalized["permission_level"] = (
                "admin" if normalized.get("is_admin") is True else "normal"
            )
        if "is_admin" not in normalized:
            normalized["is_admin"] = normalized["permission_level"] == "admin"
        if "delegation_policy" not in normalized:
            normalized["delegation_policy"] = (
                "sensitive" if normalized["permission_level"] == "admin" else "normal"
            )
        return normalized

    @model_validator(mode="after")
    def validate_command_policy(self) -> CustomGroupCommand:
        if self.permission_level not in {"normal", "admin"}:
            raise ValueError("permission_level 必须为 normal 或 admin")
        if self.is_admin != (self.permission_level == "admin"):
            raise ValueError("is_admin 与 permission_level 不一致")
        if self.delegation_policy not in {"normal", "sensitive", "forbidden"}:
            raise ValueError("delegation_policy 值无效")
        if self.permission_level == "admin" and self.delegation_policy == "normal":
            raise ValueError("管理员命令 delegation_policy 至少为 sensitive")
        if self.history_mode not in {"none", "command", "full"}:
            raise ValueError("history_mode 值无效")
        if (
            self.delegation_policy in {"sensitive", "forbidden"}
            and self.history_mode == "full"
        ):
            raise ValueError("敏感或禁止委托命令不能记录完整参数")
        if self.linked_plugin is not None and not self.linked_plugin.strip():
            raise ValueError("linked_plugin 不能为空字符串")
        if self.availability not in {"available", "missing_plugin"}:
            raise ValueError("availability 值无效")
        return self


class CustomGroupConfig(BaseModel):
    """自定义分组配置"""

    group_name: str = Field(description="分组名称")
    description: str = Field(default="", description="分组描述")
    commands: list[CustomGroupCommand] = Field(
        default_factory=list, description="命令列表"
    )
    priority: int = Field(default=0, description="优先级")
    hidden: bool = Field(default=False, description="是否隐藏")


class HelpPluginConfig(BaseModel):
    """Help插件统一配置类"""

    enable_ai_command_notify: bool = Field(default=True, description="AI执行命令前通知")
    enable_ai_command_result: bool = Field(
        default=True, description="AI执行命令结果通知"
    )
    enable_ai_self_command: bool = Field(
        default=False, description="允许AI以机器人自身身份执行命令"
    )
    ai_command_auto_wait_seconds: float = Field(
        default=3, description="AI 命令 tool 自动同步监听窗口（秒）"
    )
    ai_command_max_wait_seconds: float = Field(
        default=60, description="AI 命令 tool 自定义等待上限（秒）"
    )
    enable_sensitive_delegation: bool = Field(
        default=False, description="允许管理员跨用户委托敏感命令"
    )
    allow_admin_target_override: bool = Field(
        default=False, description="允许管理员绕过目标用户的委托隐私设置"
    )
    ai_command_dedupe_window_seconds: float = Field(
        default=60, description="AI 命令重复调度抑制窗口（秒）"
    )
    command_history_retention_days: int = Field(
        default=90, description="命令历史和观察身份的明细保留天数"
    )
    ai_command_blacklist: set[str] = Field(
        default_factory=lambda: DefaultCFG.AI_COMMAND_BLACKLIST.copy(),
        description="AI命令调用黑名单",
    )
    regex: RegexConfig = Field(default_factory=RegexConfig)
    custom_groups: list[CustomGroupConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ai_command_wait_seconds(self) -> HelpPluginConfig:
        """校验 AI tool 等待窗口，避免把同步监听误配为执行超时。"""
        if not math.isfinite(self.ai_command_auto_wait_seconds):
            raise ValueError("ai_command_auto_wait_seconds must be finite")
        if not math.isfinite(self.ai_command_max_wait_seconds):
            raise ValueError("ai_command_max_wait_seconds must be finite")
        if self.ai_command_auto_wait_seconds <= 0:
            raise ValueError("ai_command_auto_wait_seconds must be positive")
        if self.ai_command_max_wait_seconds <= 0:
            raise ValueError("ai_command_max_wait_seconds must be positive")
        if self.ai_command_max_wait_seconds < self.ai_command_auto_wait_seconds:
            raise ValueError(
                "ai_command_max_wait_seconds must be greater than or equal to "
                "ai_command_auto_wait_seconds"
            )
        if (
            not math.isfinite(self.ai_command_dedupe_window_seconds)
            or self.ai_command_dedupe_window_seconds < 0
        ):
            raise ValueError(
                "ai_command_dedupe_window_seconds must be finite and non-negative"
            )
        if self.command_history_retention_days < 1:
            raise ValueError("command_history_retention_days must be positive")
        return self

    @classmethod
    def from_astrbot_config(cls, raw_config: dict[str, Any] | None) -> HelpPluginConfig:
        """从AstrBot配置字典创建配置对象"""
        if not raw_config:
            return cls()

        regex_cfg = RegexConfig.from_dict(raw_config.get("regex", {}))
        ai_blacklist = raw_config.get("ai_command_blacklist")
        ai_blacklist_set = (
            set(ai_blacklist)
            if ai_blacklist is not None
            else DefaultCFG.AI_COMMAND_BLACKLIST.copy()
        )

        return cls(
            enable_ai_command_notify=raw_config.get("enable_ai_command_notify", True),
            enable_ai_command_result=raw_config.get("enable_ai_command_result", True),
            enable_ai_self_command=raw_config.get("enable_ai_self_command", False),
            ai_command_auto_wait_seconds=raw_config.get(
                "ai_command_auto_wait_seconds", 3
            ),
            ai_command_max_wait_seconds=raw_config.get(
                "ai_command_max_wait_seconds", 60
            ),
            enable_sensitive_delegation=raw_config.get(
                "enable_sensitive_delegation", False
            ),
            allow_admin_target_override=raw_config.get(
                "allow_admin_target_override", False
            ),
            ai_command_dedupe_window_seconds=raw_config.get(
                "ai_command_dedupe_window_seconds", 60
            ),
            command_history_retention_days=raw_config.get(
                "command_history_retention_days", 90
            ),
            ai_command_blacklist=ai_blacklist_set,
            regex=regex_cfg,
        )

    def save(self, raw_config: dict[str, Any]) -> None:
        """保存配置到原始配置字典"""
        config_dict = self.model_dump()
        for key, value in config_dict.items():
            if key != "custom_groups":
                raw_config[key] = value

    @property
    def max_examples(self) -> int:
        return self.regex.max_examples
