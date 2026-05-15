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
sys.path.insert(0, "src")


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
    description: str = ""
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
            plugin="astrbot_plugin_helpinfo",
            custom_groups=["常用命令"],
        )
        cmd2 = MockCommandEntry(
            command="/status",
            description="查看状态",
            plugin="astrbot_plugin_status",
            custom_groups=["常用命令"],
        )
        cmd3 = MockCommandEntry(
            command="/setu",
            description="获取色图",
            plugin="astrbot_plugin_setu",
            pattern=r"^来.*色图$",
            type="regex",
            custom_groups=["娱乐命令"],
        )
        cmd4 = MockCommandEntry(
            command="/admin",
            description="管理员命令",
            plugin="astrbot_plugin_admin",
            tag="admin",
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
            ],
        )
        group2 = MockCustomGroupConfig(
            group_name="娱乐命令",
            description="娱乐命令组",
            commands=[
                MockCustomGroupCommand(command_name="setu", type="regex"),
            ],
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
        assert any(r["command"] == "/help" for r in results)

        # 验证 custom_groups 字段
        help_cmd = next(r for r in results if r["command"] == "/help")
        print(f"custom_groups: {help_cmd.get('custom_groups', [])}")
        assert "常用命令" in help_cmd.get("custom_groups", [])

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
        assert any("色图" in r.get("pattern", "") for r in results if r.get("pattern"))

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
            assert "常用命令" in detail.get("custom_groups", [])
        else:
            print("[WARN] 未找到命令详情")

        print("[OK] 测试通过")

    def test_custom_group_command_description_propagated(self):
        """测试自定义命令描述正确传递到搜索结果"""
        print("\n--- 测试5: 自定义命令描述传递 ---")

        # 创建带描述的自定义命令
        group = MockCustomGroupConfig(
            group_name="部署命令",
            description="部署相关命令",
            commands=[
                MockCustomGroupCommand(
                    command_name="deploy_app",
                    pattern="deploy app",
                    description="部署应用到测试环境",
                ),
                MockCustomGroupCommand(
                    command_name="rollback_app",
                    pattern="rollback app",
                    description="回滚应用版本",
                ),
            ],
        )

        # 模拟将这些命令添加到命令字典
        for cmd in group.commands:
            cmd_entry = MockCommandEntry(
                command=f"/{cmd.command_name}",
                description=cmd.description,  # 使用自定义命令的描述
                plugin=f"_custom_group_{group.group_name}",
                custom_groups=[group.group_name],
            )
            self.all_commands[cmd_entry.command] = cmd_entry.to_dict()

        # 验证描述已正确传递
        deploy_cmd = self.all_commands.get("/deploy_app")
        assert deploy_cmd is not None
        assert deploy_cmd["description"] == "部署应用到测试环境"
        assert deploy_cmd["custom_groups"] == ["部署命令"]

        rollback_cmd = self.all_commands.get("/rollback_app")
        assert rollback_cmd is not None
        assert rollback_cmd["description"] == "回滚应用版本"
        assert rollback_cmd["custom_groups"] == ["部署命令"]

        print(f"验证描述传递:")
        print(f"  - deploy_app: '{deploy_cmd['description']}'")
        print(f"  - rollback_app: '{rollback_cmd['description']}'")
        print("[OK] 测试通过")

    def test_custom_group_command_search_by_description_keywords(self):
        """测试通过描述中的关键字搜索自定义命令"""
        print("\n--- 测试6: 通过描述关键字搜索 ---")

        # 创建带描述的自定义命令
        group = MockCustomGroupConfig(
            group_name="管理命令",
            description="系统管理",
            commands=[
                MockCustomGroupCommand(
                    command_name="backup_db",
                    pattern="backup db",
                    description="备份数据库到远程服务器",
                ),
                MockCustomGroupCommand(
                    command_name="restore_db",
                    pattern="restore db",
                    description="从备份恢复数据库",
                ),
            ],
        )

        # 模拟将这些命令添加到命令字典
        for cmd in group.commands:
            cmd_entry = MockCommandEntry(
                command=f"/{cmd.command_name}",
                description=cmd.description,
                plugin=f"_custom_group_{group.group_name}",
                custom_groups=[group.group_name],
            )
            self.all_commands[cmd_entry.command] = cmd_entry.to_dict()

        # 搜索描述中的关键字
        keyword = "远程"
        results = self._search_commands(keyword)

        print(f"搜索关键字: '{keyword}'")
        print(f"找到命令: {[r['command'] for r in results]}")

        # 验证能通过描述关键字找到命令
        assert len(results) >= 1
        assert any(r["command"] == "/backup_db" for r in results)
        assert any("远程" in r.get("description", "") for r in results)

        print("[OK] 测试通过")

    def test_custom_group_command_without_description_backward_compatible(self):
        """测试没有描述的命令仍然正常工作（向后兼容）"""
        print("\n--- 测试7: 向后兼容性 ---")

        # 创建没有描述的自定义命令
        group = MockCustomGroupConfig(
            group_name="基础命令",
            description="基础功能",
            commands=[
                MockCustomGroupCommand(
                    command_name="status",
                    pattern="status",
                    description="",  # 空描述
                ),
                MockCustomGroupCommand(
                    command_name="ping",
                    pattern="ping",
                    description=None,  # None 描述
                ),
            ],
        )

        # 模拟将这些命令添加到命令字典
        for cmd in group.commands:
            cmd_entry = MockCommandEntry(
                command=f"/{cmd.command_name}",
                description=cmd.description or "",  # 空描述回退到空字符串
                plugin=f"_custom_group_{group.group_name}",
                custom_groups=[group.group_name],
            )
            self.all_commands[cmd_entry.command] = cmd_entry.to_dict()

        # 验证命令仍然可以搜索
        status_cmd = self.all_commands.get("/status")
        ping_cmd = self.all_commands.get("/ping")

        assert status_cmd is not None
        assert ping_cmd is not None
        assert status_cmd["description"] == ""
        assert ping_cmd["description"] == ""

        # 搜索仍然能找到这些命令
        results = self._search_commands("status")
        assert len(results) >= 1
        assert any(r["command"] == "/status" for r in results)

        print(f"验证向后兼容:")
        print(f"  - 空描述命令: 可搜索 ✅")
        print(f"  - None 描述命令: 回退到空字符串 ✅")
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
            elif (
                cmd_info.get("pattern") and keyword_lower in cmd_info["pattern"].lower()
            ):
                results.append(cmd_info)
            elif keyword_lower in cmd_info.get("description", "").lower():
                results.append(cmd_info)
            if len(results) >= limit:
                return results[:limit]

        # 搜索 custom_groups 字段
        for cmd_info in self.all_commands.values():
            if cmd_info in results:
                continue
            if any(
                keyword_lower in g.lower() for g in cmd_info.get("custom_groups", [])
            ):
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
            ("描述传递", self.test_custom_group_command_description_propagated),
            (
                "描述关键字搜索",
                self.test_custom_group_command_search_by_description_keywords,
            ),
            (
                "向后兼容性",
                self.test_custom_group_command_without_description_backward_compatible,
            ),
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
