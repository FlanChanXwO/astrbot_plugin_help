"""Tests for custom group functionality."""

from __future__ import annotations

import pytest

from tests.mocks import MockDataFactory


class TestCustomGroupCreation:
    """测试自定义命令组从配置直接创建命令."""

    def test_create_command_from_custom_group_config(self):
        """测试从自定义组配置创建命令条目."""
        from src.infrastructure.analysis.command_index import CommandIndex
        from src.infrastructure.config.datamodels import (
            CustomGroupCommand,
            CustomGroupConfig,
        )

        index = CommandIndex()
        index._custom_groups = [
            CustomGroupConfig(
                group_name="二重螺旋",
                description="二重螺旋游戏插件",
                commands=[
                    CustomGroupCommand(
                        command="dna密函",
                        type="command",
                        is_admin=False,
                        hidden=False,
                        aliases=["d密函", "dmh"],  # aliases for AI recognition
                    ),
                ],
                priority=0,
                hidden=False,
            ),
        ]

        plugin_dict = {}
        commands_dict = {}

        index._apply_custom_groups(plugin_dict, commands_dict)

        # Should create _custom_group_二重螺旋
        assert "_custom_group_二重螺旋" in plugin_dict
        custom_summary = plugin_dict["_custom_group_二重螺旋"]
        assert custom_summary.plugin_display_name == "二重螺旋"
        assert len(custom_summary.commands) == 1

        # Check command details
        cmd = custom_summary.commands[0]
        assert cmd.command == "/dna密函"  # Should have slash prefix
        assert cmd.group_name is None  # Should be standalone, not in a group
        assert cmd.type == "command"
        assert cmd.plugin == "_custom_group_二重螺旋"
        # aliases stored for AI data, not displayed in menu
        assert cmd.aliases == ["d密函", "dmh"]
        assert cmd.sub_commands == []

    def test_create_multiple_commands_in_group(self):
        """测试创建包含多个命令的自定义组."""
        from src.infrastructure.analysis.command_index import CommandIndex
        from src.infrastructure.config.datamodels import (
            CustomGroupCommand,
            CustomGroupConfig,
        )

        index = CommandIndex()
        index._custom_groups = [
            CustomGroupConfig(
                group_name="游戏命令",
                description="游戏相关命令",
                commands=[
                    CustomGroupCommand(command="start", type="command"),
                    CustomGroupCommand(command="stop", type="command"),
                    CustomGroupCommand(command="status", type="command"),
                ],
                priority=1,
                hidden=False,
            ),
        ]

        plugin_dict = {}
        commands_dict = {}

        index._apply_custom_groups(plugin_dict, commands_dict)

        assert "_custom_group_游戏命令" in plugin_dict
        assert len(plugin_dict["_custom_group_游戏命令"].commands) == 3

        # Check all commands have correct prefix and to_dict uses 'type'
        for cmd in plugin_dict["_custom_group_游戏命令"].commands:
            assert cmd.command.startswith("/")
            cmd_dict = cmd.to_dict()
            assert "type" in cmd_dict  # Should have 'type' field (mapped from kind)
            assert cmd_dict["type"] == "command"
            assert "kind" not in cmd_dict  # Should not have 'kind' in output

    def test_hidden_commands_not_created(self):
        """测试隐藏的命令不会被创建."""
        from src.infrastructure.analysis.command_index import CommandIndex
        from src.infrastructure.config.datamodels import (
            CustomGroupCommand,
            CustomGroupConfig,
        )

        index = CommandIndex()
        index._custom_groups = [
            CustomGroupConfig(
                group_name="测试组",
                description="",
                commands=[
                    CustomGroupCommand(command="visible", type="command", hidden=False),
                    CustomGroupCommand(command="hidden", type="command", hidden=True),
                ],
                priority=0,
                hidden=False,
            ),
        ]

        plugin_dict = {}
        commands_dict = {}

        index._apply_custom_groups(plugin_dict, commands_dict)

        assert len(plugin_dict["_custom_group_测试组"].commands) == 1
        assert plugin_dict["_custom_group_测试组"].commands[0].command == "/visible"

    def test_hidden_group_not_created(self):
        """测试隐藏的分组不会被创建."""
        from src.infrastructure.analysis.command_index import CommandIndex
        from src.infrastructure.config.datamodels import (
            CustomGroupCommand,
            CustomGroupConfig,
        )

        index = CommandIndex()
        index._custom_groups = [
            CustomGroupConfig(
                group_name="隐藏组",
                description="",
                commands=[
                    CustomGroupCommand(command="cmd", type="command"),
                ],
                priority=0,
                hidden=True,  # Hidden group
            ),
        ]

        plugin_dict = {}
        commands_dict = {}

        index._apply_custom_groups(plugin_dict, commands_dict)

        assert "_custom_group_隐藏组" not in plugin_dict
        assert len(plugin_dict) == 0

    def test_command_with_slash_prefix(self):
        """测试命令名称已带/前缀的情况."""
        from src.infrastructure.analysis.command_index import CommandIndex
        from src.infrastructure.config.datamodels import (
            CustomGroupCommand,
            CustomGroupConfig,
        )

        index = CommandIndex()
        index._custom_groups = [
            CustomGroupConfig(
                group_name="测试组",
                description="",
                commands=[
                    CustomGroupCommand(command="/already_has_slash", type="command"),
                ],
                priority=0,
                hidden=False,
            ),
        ]

        plugin_dict = {}
        commands_dict = {}

        index._apply_custom_groups(plugin_dict, commands_dict)

        cmd = plugin_dict["_custom_group_测试组"].commands[0]
        assert cmd.command == "/already_has_slash"  # Should not duplicate slash

    def test_regex_type_command(self):
        """测试正则类型命令."""
        from src.infrastructure.analysis.command_index import CommandIndex
        from src.infrastructure.config.datamodels import (
            CustomGroupCommand,
            CustomGroupConfig,
        )

        index = CommandIndex()
        index._custom_groups = [
            CustomGroupConfig(
                group_name="正则组",
                description="",
                commands=[
                    CustomGroupCommand(
                        command="regex:测试",
                        type="regex",
                        pattern="^测试.*$",
                        examples=["测试例子"],
                    ),
                ],
                priority=0,
                hidden=False,
            ),
        ]

        plugin_dict = {}
        commands_dict = {}

        index._apply_custom_groups(plugin_dict, commands_dict)

        cmd = plugin_dict["_custom_group_正则组"].commands[0]
        assert cmd.type == "regex"
        assert cmd.pattern == "^测试.*$"
        assert cmd.examples == ["测试例子"]

    def test_merge_into_existing_plugin_with_same_name(self):
        """测试与同名现有插件合并."""
        from src.infrastructure.analysis.command_index import CommandIndex
        from src.infrastructure.config.datamodels import (
            CustomGroupCommand,
            CustomGroupConfig,
        )

        index = CommandIndex()
        index._custom_groups = [
            CustomGroupConfig(
                group_name="existing_plugin",  # Same name as existing plugin
                description="自定义描述",
                commands=[
                    CustomGroupCommand(
                        command="custom_cmd",
                        type="command",
                        aliases=["cc", "c"],  # aliases for AI recognition
                    ),
                ],
                priority=0,
                hidden=False,
            ),
        ]

        # Create existing plugin
        entry = MockDataFactory.create_command_entry(
            "/existing_cmd", "Existing command"
        )
        summary = MockDataFactory.create_plugin_summary(
            "existing_plugin", commands=[entry]
        )

        plugin_dict = {"existing_plugin": summary}
        commands_dict = {}

        index._apply_custom_groups(plugin_dict, commands_dict)

        # Should merge into existing plugin, not create new virtual one
        assert "_custom_group_existing_plugin" not in plugin_dict
        assert len(plugin_dict["existing_plugin"].commands) == 2  # Original + custom

        # Check custom command properties
        custom_entry = [
            c
            for c in plugin_dict["existing_plugin"].commands
            if c.command == "/custom_cmd"
        ][0]
        assert custom_entry.group_name is None  # Should not be in a group
        assert custom_entry.aliases == ["cc", "c"]  # aliases stored for AI recognition


class TestAggregateSimpleCommands:
    """测试单命令插件聚合."""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
