"""Mock data factories for testing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MockCommandEntry:
    """模拟命令条目."""

    command: str
    description: str = ""
    plugin: str = "test_plugin"
    plugin_display_name: str | None = None
    plugin_version: str = "v1.0.0"
    aliases: list[str] = field(default_factory=list)
    is_alias_of: str | None = None
    group_name: str | None = None
    tag: str = "normal"
    type: str = "command"
    pattern: str = ""
    show_pattern: bool = True
    examples: list[str] = field(default_factory=list)
    sub_commands: list[str] = field(default_factory=list)
    usage_hint: str = ""
    handler_name: str = ""
    custom_groups: list[str] = field(default_factory=list)
    priority: int | None = None


@dataclass
class MockPluginSummary:
    """模拟插件摘要."""

    plugin: str
    plugin_display_name: str | None = None
    plugin_version: str = ""
    plugin_desc: str = ""
    commands: list[MockCommandEntry] = field(default_factory=list)


class MockDataFactory:
    """模拟数据工厂."""

    @staticmethod
    def create_command_entry(
        command: str,
        description: str = "Test command",
        **kwargs,
    ) -> MockCommandEntry:
        """创建命令条目."""
        return MockCommandEntry(
            command=command,
            description=description,
            **kwargs,
        )

    @staticmethod
    def create_regex_entry(
        pattern: str,
        description: str = "Regex trigger",
        examples: list[str] | None = None,
        **kwargs,
    ) -> MockCommandEntry:
        """创建正则命令条目."""
        return MockCommandEntry(
            command=f"regex:{pattern}",
            description=description,
            type="regex",
            pattern=pattern,
            examples=examples or [],
            **kwargs,
        )

    @staticmethod
    def create_plugin_summary(
        name: str,
        display_name: str | None = None,
        version: str = "v1.0.0",
        desc: str = "",
        commands: list[MockCommandEntry] | None = None,
    ) -> MockPluginSummary:
        """创建插件摘要."""
        return MockPluginSummary(
            plugin=name,
            plugin_display_name=display_name or name,
            plugin_version=version,
            plugin_desc=desc,
            commands=commands or [],
        )

    @staticmethod
    def create_config(
        enable_ai_command_notify: bool = True,
        ignored_plugins: list[str] | None = None,
        custom_groups: list[dict] | None = None,
    ) -> dict:
        """创建插件配置."""
        return {
            "enable_ai_command_notify": enable_ai_command_notify,
            "enable_ai_command_result": True,
            "ai_command_blacklist": ["admin_plugin"],
            "ignored_plugins": ignored_plugins or [],
            "regex": {"max_examples": 10},
            "custom_groups": custom_groups or [],
            "rendering": {
                "use_t2i": False,
                "html_theme": "simple",
                "jpeg_quality": 95,
                "timeout_analysis": 10.0,
                "max_concurrent_tasks": 2,
                "giant_threshold": 1500,
            },
        }

    @staticmethod
    def get_test_patterns() -> dict[str, str]:
        """获取测试用的正则模式."""
        return {
            "simple": r"hello",
            "anchor_start": r"^test",
            "anchor_end": r"world$",
            "anchor_both": r"^exact$",
            "char_class": r"[a-z]+",
            "negated_class": r"[^abc]+",
            "digit": r"\d{2,4}",
            "word": r"\w+",
            "space": r"\s+",
            "chinese": r"^来.*色图$",
            "complex": r"^/?(来\s*(.*?)(份|个|张|点))(.*?)(?:福利|色|瑟|涩|塞)?图$",
            "alternation": r"hello|world",
            "optional": r"colou?r",
            "group_repeat": r"(abc)+",
        }
