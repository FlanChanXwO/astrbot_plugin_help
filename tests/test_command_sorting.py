"""Tests for command sorting in help menu rendering."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure mocks are set up before plugin imports
for mod_name in [
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.api.star",
    "astrbot.api.message_components",
    "astrbot.core",
    "astrbot.core.message",
    "astrbot.core.message.components",
    "astrbot.core.message.message_event_result",
    "astrbot.core.agent",
    "astrbot.core.agent.mcp_client",
    "astrbot.core.agent.tool",
    "astrbot.core.star",
    "astrbot.core.star.filter",
    "astrbot.core.star.filter.command",
    "astrbot.core.star.filter.command_group",
    "astrbot.core.star.filter.event_message_type",
    "astrbot.core.star.filter.regex",
    "astrbot.core.star.filter.permission",
    "astrbot.core.star.filter.platform_adapter_type",
    "astrbot.core.star.star_handler",
    "astrbot.core.pipeline",
    "astrbot.core.pipeline.context",
    "astrbot.core.pipeline.waking_check",
    "astrbot.core.pipeline.waking_check.stage",
    "astrbot.core.pipeline.process_stage",
    "astrbot.core.pipeline.process_stage.stage",
]:
    sys.modules[mod_name] = MagicMock()

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.domain.entities.command import CommandEntry
from src.domain.entities.plugin import PluginCommandSummary, RenderNode
from src.infrastructure import context_holder
from src.infrastructure.analysis.analyzers import CommandAnalyzer
import src.infrastructure.config.config_manager as _cm


def _make_analyzer() -> CommandAnalyzer:
    """Create a CommandAnalyzer with mocked config and context."""
    context_holder._context_instance = MagicMock()
    context_holder._context_instance.get_all_stars.return_value = []

    mock_config = MagicMock()
    mock_config.ignored_plugins = set()
    mock_config.custom_groups = []
    mock_config.regex.max_examples = 10
    _cm._config_instance = mock_config

    return CommandAnalyzer()


class TestCommandSorting:
    """Verify the sorting rule: normal -> regex -> group."""

    def test_standalone_commands_sorted_normal_then_regex(self):
        """普通命令应排在正则命令之前。"""
        analyzer = _make_analyzer()
        commands = [
            CommandEntry(command="/regex1", description="", plugin="p", type="regex", pattern="^r1$"),
            CommandEntry(command="/normal2", description="", plugin="p", type="command"),
            CommandEntry(command="/normal1", description="", plugin="p", type="command"),
            CommandEntry(command="/regex2", description="", plugin="p", type="regex", pattern="^r2$"),
        ]
        tree = analyzer._build_plugin_command_tree(commands)
        names = [node.name for node in tree]
        assert names == ["normal1", "normal2", "^r1$", "^r2$"]

    def test_groups_sorted_after_standalone_commands(self):
        """命令组应排在独立命令之后。"""
        analyzer = _make_analyzer()
        commands = [
            CommandEntry(command="/group1", description="", plugin="p", type="group", group_name="group1"),
            CommandEntry(command="/normal1", description="", plugin="p", type="command"),
            CommandEntry(command="/regex1", description="", plugin="p", type="regex", pattern="^r1$"),
            CommandEntry(command="/group1/sub1", description="", plugin="p", type="command", group_name="group1"),
        ]
        tree = analyzer._build_plugin_command_tree(commands)
        assert len(tree) == 3
        assert tree[0].name == "normal1" and not tree[0].is_group
        assert tree[1].name == "^r1$" and not tree[1].is_group
        assert tree[2].name == "group1" and tree[2].is_group

    def test_group_children_sorted_normal_then_regex(self):
        """分组内部的子命令也应满足普通 -> 正则。"""
        analyzer = _make_analyzer()
        commands = [
            CommandEntry(command="/group1", description="", plugin="p", type="group", group_name="group1"),
            CommandEntry(command="/group1/regex1", description="", plugin="p", type="regex", pattern="^r1$", group_name="group1"),
            CommandEntry(command="/group1/normal1", description="", plugin="p", type="command", group_name="group1"),
        ]
        tree = analyzer._build_plugin_command_tree(commands)
        group = tree[0]
        assert group.is_group
        child_names = [child.name for child in group.children]
        assert child_names == ["group1/normal1", "^r1$"]

    def test_flattened_single_command_group_behaves_like_standalone(self):
        """单命令分组扁平化后应按其真实类型排序。"""
        analyzer = _make_analyzer()
        commands = [
            CommandEntry(command="/group1", description="", plugin="p", type="group", group_name="group1"),
            CommandEntry(command="/group1", description="", plugin="p", type="command", group_name="group1"),
            CommandEntry(command="/normal1", description="", plugin="p", type="command"),
        ]
        tree = analyzer._build_plugin_command_tree(commands)
        # Flattened: group1 is replaced by its single child because child.name == group_name
        names = [node.name for node in tree]
        assert names == ["group1", "normal1"]

    def test_render_node_sort_key(self):
        """RenderNode._sort_key 应产生正确顺序。"""
        normal = RenderNode(name="a", type="command")
        regex = RenderNode(name="b", type="regex")
        group = RenderNode(name="c", is_group=True, type="group")
        assert normal._sort_key() < regex._sort_key()
        assert regex._sort_key() < group._sort_key()

    def test_render_node_sort_children_recursive(self):
        """sort_children 应递归排序所有后代。"""
        parent = RenderNode(name="parent", is_group=True, children=[
            RenderNode(name="z", type="command"),
            RenderNode(name="a", type="regex"),
            RenderNode(name="m", is_group=True, type="group", children=[
                RenderNode(name="y", type="command"),
                RenderNode(name="x", type="regex"),
            ]),
        ])
        parent.sort_children()
        assert [c.name for c in parent.children] == ["z", "a", "m"]
        assert [c.name for c in parent.children[2].children] == ["y", "x"]
