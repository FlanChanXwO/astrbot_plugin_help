"""Configuration data models.

Contains Pydantic models for plugin configuration.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ...shared.constants import DefaultCFG


class RenderingConfig(BaseModel):
    """渲染引擎配置"""

    timeout_analysis: float = Field(
        default=DefaultCFG.TIMEOUT_ANALYSIS, description="数据分析超时（秒）"
    )
    max_concurrent_tasks: int = Field(
        default=DefaultCFG.LIMIT_TASK, description="最大并发渲染数"
    )
    giant_threshold: int = Field(
        default=DefaultCFG.LIMIT_GIANT, description="巨型块阈值（pt）"
    )
    jpeg_quality: int = Field(default=95, description="JPEG图片质量")
    html_theme: str = Field(default="simple", description="HTML主题")
    use_t2i: bool = Field(default=False, description="使用AstrBot内置t2i渲染")
    render_wait_timeout: int = Field(default=10000, description="渲染等待超时（毫秒）")
    render_image_timeout: int = Field(
        default=5000, description="单张图片加载超时（毫秒）"
    )

    @field_validator("max_concurrent_tasks")
    @classmethod
    def validate_max_concurrent_tasks(cls, v: int) -> int:
        """验证并限制并发渲染数在合理范围内"""
        MIN_CONCURRENT_TASKS = 1  # 最小值防止阻塞
        MAX_CONCURRENT_TASKS = 20  # 最大值防止过载
        if v < MIN_CONCURRENT_TASKS:
            raise ValueError(
                f"max_concurrent_tasks must be at least {MIN_CONCURRENT_TASKS} to prevent blocking"
            )
        if v > MAX_CONCURRENT_TASKS:
            raise ValueError(
                f"max_concurrent_tasks must be at most {MAX_CONCURRENT_TASKS} to prevent overload"
            )
        return v

    @field_validator("render_wait_timeout", "render_image_timeout")
    @classmethod
    def validate_timeouts(cls, v: int) -> int:
        """验证超时配置为正数"""
        if v <= 0:
            raise ValueError("Timeout values must be positive integers")
        return v

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RenderingConfig:
        """从字典创建配置"""
        if not data:
            return cls()
        return cls.model_validate({**cls().model_dump(), **(data or {})})


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
    hidden: bool = Field(default=False, description="是否隐藏")
    aliases: list[str] = Field(
        default_factory=list, description="命令别名列表（供AI识别）"
    )
    pattern: str = Field(default="", description="正则模式")
    examples: list[str] = Field(default_factory=list, description="示例列表")
    sub_commands: list[str] = Field(default_factory=list, description="子命令列表")


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
    ignored_plugins: set[str] = Field(
        default_factory=lambda: DefaultCFG.IGNORED_PLUGINS.copy(),
        description="黑名单插件ID",
    )
    ai_command_blacklist: set[str] = Field(
        default_factory=lambda: DefaultCFG.AI_COMMAND_BLACKLIST.copy(),
        description="AI命令调用黑名单",
    )
    regex: RegexConfig = Field(default_factory=RegexConfig)
    custom_groups: list[CustomGroupConfig] = Field(default_factory=list)
    rendering: RenderingConfig = Field(default_factory=RenderingConfig)

    @classmethod
    def from_astrbot_config(cls, raw_config: dict[str, Any] | None) -> HelpPluginConfig:
        """从AstrBot配置字典创建配置对象"""
        if not raw_config:
            return cls()

        regex_cfg = RegexConfig.from_dict(raw_config.get("regex", {}))
        render_cfg = RenderingConfig.from_dict(raw_config.get("rendering", {}))

        ignored_list = raw_config.get("ignored_plugins")
        ignored_set = (
            set(ignored_list)
            if ignored_list is not None
            else DefaultCFG.IGNORED_PLUGINS.copy()
        )

        ai_blacklist = raw_config.get("ai_command_blacklist")
        ai_blacklist_set = (
            set(ai_blacklist)
            if ai_blacklist is not None
            else DefaultCFG.AI_COMMAND_BLACKLIST.copy()
        )

        return cls(
            enable_ai_command_notify=raw_config.get("enable_ai_command_notify", True),
            enable_ai_command_result=raw_config.get("enable_ai_command_result", True),
            ignored_plugins=ignored_set,
            ai_command_blacklist=ai_blacklist_set,
            regex=regex_cfg,
            rendering=render_cfg,
        )

    def save(self, raw_config: dict[str, Any]) -> None:
        """保存配置到原始配置字典"""
        config_dict = self.model_dump()
        for key, value in config_dict.items():
            if key != "custom_groups":
                raw_config[key] = value

    # 向后兼容属性

    @property
    def timeout_analysis(self) -> float:
        return self.rendering.timeout_analysis

    @property
    def max_concurrent_tasks(self) -> int:
        return self.rendering.max_concurrent_tasks

    @property
    def giant_threshold(self) -> int:
        return self.rendering.giant_threshold

    @property
    def jpeg_quality(self) -> int:
        return self.rendering.jpeg_quality

    @property
    def html_theme(self) -> str:
        return self.rendering.html_theme

    @property
    def use_t2i(self) -> bool:
        return self.rendering.use_t2i

    @property
    def max_examples(self) -> int:
        return self.regex.max_examples
