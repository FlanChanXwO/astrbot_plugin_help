"""Tests for command indexing functionality."""

from __future__ import annotations

import pytest

from tests.mocks import MockDataFactory


class TestCommandIndexBasic:
    """测试命令索引基本功能."""

    def test_create_command_entry(self):
        """测试创建命令条目."""
        entry = MockDataFactory.create_command_entry(
            "/help",
            "显示帮助信息",
            aliases=["/h", "/?"],
        )
        assert entry.command == "/help"
        assert entry.description == "显示帮助信息"
        assert "/h" in entry.aliases
        assert "/?" in entry.aliases

    def test_create_regex_entry(self):
        """测试创建正则命令条目."""
        entry = MockDataFactory.create_regex_entry(
            r"^来.*色图$",
            "获取色图",
            examples=["来份色图", "来张色图"],
        )
        assert entry.type == "regex"
        assert entry.pattern == r"^来.*色图$"
        assert "来份色图" in entry.examples

    def test_create_admin_command(self):
        """测试创建管理员命令."""
        entry = MockDataFactory.create_command_entry(
            "/admin",
            "管理员命令",
            tag="admin",
        )
        assert entry.tag == "admin"

    def test_create_plugin_summary(self):
        """测试创建插件摘要."""
        commands = [
            MockDataFactory.create_command_entry("/cmd1", "命令1"),
            MockDataFactory.create_command_entry("/cmd2", "命令2"),
        ]
        summary = MockDataFactory.create_plugin_summary(
            "test_plugin",
            display_name="测试插件",
            version="v1.0.0",
            desc="测试用插件",
            commands=commands,
        )
        assert summary.plugin == "test_plugin"
        assert summary.plugin_display_name == "测试插件"
        assert len(summary.commands) == 2


class TestConfigCreation:
    """测试配置创建."""

    def test_create_default_config(self):
        """测试创建默认配置."""
        config = MockDataFactory.create_config()
        assert "ai_command_blacklist" in config
        assert "regex" in config


class TestTestPatterns:
    """测试提供的测试模式."""

    def test_all_patterns_are_valid_regex(self):
        """测试所有测试模式都是有效的正则."""
        import re

        patterns = MockDataFactory.get_test_patterns()

        for name, pattern in patterns.items():
            try:
                compiled = re.compile(pattern)
                assert compiled is not None
            except re.error as e:
                pytest.fail(f"模式 '{name}': '{pattern}' 不是有效的正则: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
