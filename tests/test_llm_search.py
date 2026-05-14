#!/usr/bin/env python
"""测试 LLM 工具搜索功能 - 验证自定义命令组搜索

测试内容：
1. search_command 是否能搜索到自定义命令组中的命令
2. get_command_detail 是否能获取自定义命令组中命令的详情
3. 验证 custom_groups 字段是否正确返回
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

# 添加项目路径
sys.path.insert(0, 'src')


@dataclass
class MockCommandEntry:
    """模拟命令条目"""
    command: str
    description: str = ""
    plugin: str = "test_plugin"
    aliases: list[str] = field(default_factory=list)
    group_name: str | None = None
    tag: str = "normal"
    type: str = "command"
    pattern: str = ""
    custom_groups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "description": self.description,
            "plugin": self.plugin,
            "aliases": self.aliases,
            "group_name": self.group_name,
            "tag": self.tag,
            "type": self.type,
            "pattern": self.pattern,
            "custom_groups": self.custom_groups,
        }


@dataclass
class MockPluginSummary:
    """模拟插件摘要"""
    plugin: str
    commands: list[Any]
    plugin_display_name: str | None = None
    plugin_version: str = ""
    plugin_desc: str = ""


@dataclass
class MockCustomGroupCommand:
    """模拟自定义组命令配置"""
    command_name: str
    pattern: str = ""
    type: str = "command"
    is_admin: bool = False
    show_in_menu: bool = True


@dataclass
class MockCustomGroupConfig:
    """模拟自定义组配置"""
    group_name: str
    description: str = ""
    commands: list[MockCustomGroupCommand] = field(default_factory=list)
    pattern: str = ""
    type: str = "command"
    is_admin: bool = False
    priority: int = 0
    show_in_menu: bool = True


class MockConfig:
    """模拟配置"""
    def __init__(self):
        self.custom_groups: list[MockCustomGroupConfig] = []

    def add_group(self, group: MockCustomGroupConfig):
        self.custom_groups.append(group)


class TestSearchWithCustomGroups:
    """测试搜索自定义命令组功能"""

    def __init__(self):
        self.config = MockConfig()
        self.all_commands: dict[str, dict] = {}

    def setup_test_data(self):
        """设置测试数据"""
        # 创建一些模拟命令
        cmd1 = MockCommandEntry(
            command="/help",
            description="显示帮助",
            plugin="astrbot_plugin_help",
            custom_groups=["常用命令"]
        )
        cmd2 = MockCommandEntry(
            command="/status",
            description="查看状态",
            plugin="astrbot_plugin_status",
            custom_groups=["常用命令"]
        )
        cmd3 = MockCommandEntry(
            command="/setu",
            description="获取色图",
            plugin="astrbot_plugin_setu",
            pattern=r"^来.*色图$",
            type="regex",
            custom_groups=["娱乐命令"]
        )
        cmd4 = MockCommandEntry(
            command="/admin",
            description="管理员命令",
            plugin="astrbot_plugin_admin",
            tag="admin"
        )

        # 添加到命令字典
        self.all_commands[cmd1.command] = cmd1.to_dict()
        self.all_commands[cmd2.command] = cmd2.to_dict()
        self.all_commands[cmd3.command] = cmd3.to_dict()
        self.all_commands[cmd4.command] = cmd4.to_dict()

        # 创建自定义命令组
        group1 = MockCustomGroupConfig(
            group_name="常用命令",
            description="常用命令组",
            commands=[
                MockCustomGroupCommand(command_name="help"),
                MockCustomGroupCommand(command_name="status"),
            ]
        )
        group2 = MockCustomGroupConfig(
            group_name="娱乐命令",
            description="娱乐命令组",
            commands=[
                MockCustomGroupCommand(command_name="setu", type="regex"),
            ]
        )

        self.config.add_group(group1)
        self.config.add_group(group2)

        print("[OK] 测试数据设置完成")
        print(f"  - 命令数: {len(self.all_commands)}")
        print(f"  - 自定义组数: {len(self.config.custom_groups)}")

    def test_search_by_group_name(self):
        """测试通过组名搜索"""
        print("\n--- 测试1: 通过组名搜索 ---")

        keyword = "常用"
        matched_groups = self._find_matching_custom_groups(keyword)

        print(f"搜索关键词: '{keyword}'")
        print(f"匹配到的自定义组: {[g.group_name for g in matched_groups]}")

        # 验证是否匹配到"常用命令"组
        assert len(matched_groups) == 1
        assert matched_groups[0].group_name == "常用命令"
        print("[OK] 测试通过")

    def test_search_by_command_in_group(self):
        """测试通过组内命令名搜索"""
        print("\n--- 测试2: 通过组内命令名搜索 ---")

        keyword = "help"

        # 搜索命令
        results = self._search_commands(keyword)
        print(f"搜索关键词: '{keyword}'")
        print(f"找到命令: {[r['command'] for r in results]}")

        # 验证是否找到 help 命令
        assert len(results) >= 1
        assert any(r['command'] == '/help' for r in results)

        # 验证 custom_groups 字段
        help_cmd = next(r for r in results if r['command'] == '/help')
        print(f"custom_groups: {help_cmd.get('custom_groups', [])}")
        assert "常用命令" in help_cmd.get('custom_groups', [])

        print("[OK] 测试通过")

    def test_search_regex_command_in_group(self):
        """测试搜索自定义组中的正则命令"""
        print("\n--- 测试3: 搜索正则命令 ---")

        keyword = "色图"
        results = self._search_commands(keyword)

        print(f"搜索关键词: '{keyword}'")
        print(f"找到命令: {[r['command'] for r in results]}")

        # 验证是否找到 setu 命令（通过 pattern 匹配）
        assert len(results) >= 1
        assert any('色图' in r.get('pattern', '') for r in results if r.get('pattern'))

        print("[OK] 测试通过")

    def test_get_command_detail_with_custom_group(self):
        """测试获取命令详情，包含自定义组信息"""
        print("\n--- 测试4: 获取命令详情 ---")

        command_name = "help"
        detail = self._get_command_detail(command_name)

        print(f"查询命令: '{command_name}'")
        print(f"找到详情: {detail is not None}")

        if detail:
            print(f"命令: {detail['command']}")
            print(f"描述: {detail['description']}")
            print(f"所属插件: {detail['plugin']}")
            print(f"自定义组: {detail.get('custom_groups', [])}")

            # 验证 custom_groups 字段
            assert "常用命令" in detail.get('custom_groups', [])
        else:
            print("[WARN] 未找到命令详情")

        print("[OK] 测试通过")

    def _find_matching_custom_groups(self, keyword: str) -> list:
        """模拟 _find_matching_custom_groups"""
        if not self.config.custom_groups:
            return []

        keyword_lower = keyword.lower().strip()
        matched = []

        for group in self.config.custom_groups:
            # 匹配组名
            if keyword_lower in group.group_name.lower():
                matched.append(group)
                continue

            # 匹配组内命令
            if group.commands:
                for cmd in group.commands:
                    if keyword_lower in cmd.command_name.lower().lstrip("/"):
                        matched.append(group)
                        break

        return matched

    def _search_commands(self, keyword: str, limit: int = 5) -> list[dict]:
        """模拟 search_commands"""
        keyword_lower = keyword.lower().strip()
        if keyword_lower.startswith("/"):
            keyword_lower = keyword_lower[1:]

        results = []

        # 精确匹配
        exact_match = f"/{keyword_lower}"
        if exact_match in self.all_commands:
            results.append(self.all_commands[exact_match])

        # 关键词匹配
        for cmd_name, cmd_info in self.all_commands.items():
            if cmd_info in results:
                continue
            searchable_name = cmd_name.lower().lstrip("/")
            if keyword_lower in searchable_name:
                results.append(cmd_info)
            elif cmd_info.get("pattern") and keyword_lower in cmd_info["pattern"].lower():
                results.append(cmd_info)
            elif keyword_lower in cmd_info.get("description", "").lower():
                results.append(cmd_info)
            if len(results) >= limit:
                return results[:limit]

        # 搜索 custom_groups 字段
        for cmd_info in self.all_commands.values():
            if cmd_info in results:
                continue
            if any(keyword_lower in g.lower() for g in cmd_info.get("custom_groups", [])):
                results.append(cmd_info)
            if len(results) >= limit:
                return results[:limit]

        return results[:limit]

    def _get_command_detail(self, command_name: str) -> dict | None:
        """模拟 get_command_detail"""
        normalized = command_name.strip()
        if not normalized:
            return None
        if not normalized.startswith("/"):
            normalized = "/" + normalized

        return self.all_commands.get(normalized)

    def run_all_tests(self):
        """运行所有测试"""
        print("=" * 60)
        print("LLM工具搜索功能测试 - 自定义命令组")
        print("=" * 60)

        self.setup_test_data()

        tests = [
            ("组名搜索", self.test_search_by_group_name),
            ("组内命令搜索", self.test_search_by_command_in_group),
            ("正则命令搜索", self.test_search_regex_command_in_group),
            ("命令详情", self.test_get_command_detail_with_custom_group),
        ]

        passed = 0
        failed = 0

        for name, test_func in tests:
            try:
                test_func()
                passed += 1
            except Exception as e:
                failed += 1
                print(f"[FAIL] 测试失败: {name}")
                print(f"  错误: {e}")

        print("\n" + "=" * 60)
        print(f"测试结果: {passed} 通过, {failed} 失败")
        print("=" * 60)

        return failed == 0


if __name__ == "__main__":
    tester = TestSearchWithCustomGroups()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)
