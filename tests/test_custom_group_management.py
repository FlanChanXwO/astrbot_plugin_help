"""自定义命令组应用服务的公共行为测试。"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from src.infrastructure.config.datamodels import CustomGroupConfig


def test_service_is_exported_from_application_services_package():
    """后续 Web API 与 LLM tool 可从应用服务包稳定导入该用例。"""
    from src.application.services import CustomGroupService

    assert CustomGroupService.__name__ == "CustomGroupService"


def test_get_custom_group_service_returns_one_process_wide_instance():
    """AI tool 与 Web API 必须共享删除确认 token 所在的服务实例。"""
    from src.application.services.custom_group_service import (
        get_custom_group_service,
        reset_custom_group_service,
    )

    reset_custom_group_service()
    try:
        assert get_custom_group_service() is get_custom_group_service()
    finally:
        reset_custom_group_service()


class InMemoryGroups:
    """为应用服务提供不依赖 AstrBot 运行时的存储边界。"""

    def __init__(self, groups: list[CustomGroupConfig] | None = None):
        self.groups = list(groups or [])
        self.saved: list[list[CustomGroupConfig]] = []

    def save(self, groups: list[CustomGroupConfig]) -> bool:
        self.saved.append(list(groups))
        return True

    def set_groups(self, groups: list[CustomGroupConfig]) -> None:
        self.groups = list(groups)


def _load_entry_module():
    """以包模块方式加载入口，保留 main.py 的相对导入语义。"""

    class PassthroughFilter:
        """使入口装饰器在单元测试中保留原始协程函数。"""

        PermissionType = types.SimpleNamespace(ADMIN="admin")
        EventMessageType = types.SimpleNamespace(GROUP_MESSAGE="group")

        @staticmethod
        def command(*_args, **_kwargs):
            return lambda function: function

        @staticmethod
        def permission_type(*_args, **_kwargs):
            return lambda function: function

        @staticmethod
        def llm_tool(*_args, **_kwargs):
            return lambda function: function

        event_message_type = llm_tool
        on_astrbot_loaded = llm_tool
        on_plugin_loaded = llm_tool
        on_plugin_unloaded = llm_tool

    class TestStar:
        pass

    class TestFunctionTool:
        """保留显式 schema，供入口层注册行为断言。"""

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    sys.modules["astrbot.api.event"].filter = PassthroughFilter
    sys.modules["astrbot.api.star"].Star = TestStar
    tool_module = types.ModuleType("astrbot.core.agent.tool")
    tool_module.FunctionTool = TestFunctionTool
    sys.modules["astrbot.core.agent.tool"] = tool_module
    package_name = "helpinfo_entry_test"
    sys.modules.pop(f"{package_name}.main", None)
    package = types.ModuleType(package_name)
    package.__path__ = [str(Path(__file__).parent.parent)]
    sys.modules[package_name] = package
    return importlib.import_module(f"{package_name}.main")


def test_execute_tool_declares_astrbot_parseable_parameters():
    """AstrBot 仅从 Args 段生成 tool schema，不能退化为空参数工具。"""
    module = _load_entry_module()

    docstring = inspect.getdoc(module.HelpPlugin.execute_astrbot_command)
    assert docstring is not None
    parameter_types = dict(
        re.findall(r"^[ \t]*(\w+)\((\w+)\):", docstring, flags=re.MULTILINE)
    )

    assert parameter_types == {
        "command": "string",
        "actor": "string",
        "result_mode": "string",
        "wait_seconds": "number",
        "target_user": "string",
    }


def test_execute_tool_registers_explicit_command_schema():
    """不能只依赖装饰器推导，执行工具必须显式注册 command schema。"""
    module = _load_entry_module()

    class Context:
        registered_tools: tuple[object, ...] = ()

        def add_llm_tools(self, *tools):
            self.registered_tools = tools

    plugin = object.__new__(module.HelpPlugin)
    plugin.context = Context()
    plugin._register_execute_command_tool()

    (tool,) = plugin.context.registered_tools
    assert tool.name == "execute_astrbot_command"
    assert tool.parameters == {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的完整命令文本。",
            },
            "actor": {
                "type": "string",
                "enum": ["user", "self"],
                "description": "执行身份；默认使用当前用户。",
            },
            "result_mode": {
                "type": "string",
                "enum": ["auto", "background", "custom"],
                "description": "结果监听方式；默认 auto。",
            },
            "wait_seconds": {
                "type": "number",
                "description": "custom 模式的监听秒数。",
            },
            "target_user": {
                "type": "string",
                "description": (
                    "代别人执行时必须传；填写用户昵称、UID、当前消息中的 @、"
                    "reply_target，或 resolve_astrbot_user 返回的 target_ref。"
                    "只有明确为请求者本人执行时才省略。"
                ),
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    assert tool.handler == plugin.execute_astrbot_command
    assert tool.handler_module_path == module.HelpPlugin.__module__


def test_runtime_tools_register_explicit_nonempty_schemas():
    """搜索、身份解析和别名写工具必须显式暴露参数。"""
    module = _load_entry_module()

    class Context:
        registered_tools: tuple[object, ...] = ()

        def add_llm_tools(self, *tools):
            self.registered_tools = tools

    plugin = object.__new__(module.HelpPlugin)
    plugin.context = Context()
    plugin._register_runtime_tools()

    tools = {tool.name: tool for tool in plugin.context.registered_tools}
    assert set(tools) == {
        "search_astrbot_command",
        "resolve_astrbot_user",
        "set_astrbot_user_alias",
        "list_astrbot_user_aliases",
        "delete_astrbot_user_alias",
    }
    assert tools["resolve_astrbot_user"].parameters["required"] == ["reference"]
    assert tools["set_astrbot_user_alias"].parameters["required"] == [
        "alias",
        "target_user",
    ]
    assert tools["delete_astrbot_user_alias"].parameters["required"] == ["alias"]
    assert set(tools["search_astrbot_command"].parameters["properties"]) == {
        "keyword",
        "permission_filter",
        "target_user",
        "preference_mode",
    }


def test_custom_group_tools_register_explicit_strict_schemas():
    """目录 tools 不依赖 docstring 推导，枚举与必填项必须进入真实 schema。"""
    module = _load_entry_module()

    class Context:
        registered_tools: tuple[object, ...] = ()

        def add_llm_tools(self, *tools):
            self.registered_tools = tools

    plugin = object.__new__(module.HelpPlugin)
    plugin.context = Context()
    plugin._register_custom_group_tools()

    tools = {tool.name: tool for tool in plugin.context.registered_tools}
    assert set(tools) == {
        "list_custom_groups",
        "create_custom_group",
        "update_custom_group",
        "preview_delete_custom_group",
        "confirm_delete_custom_group",
        "add_custom_group_command",
        "update_custom_group_command",
        "delete_custom_group_command",
    }
    assert len(tools) == len(plugin.context.registered_tools)
    for tool in tools.values():
        assert tool.parameters["additionalProperties"] is False
        assert tool.handler_module_path == module.HelpPlugin.__module__
    add_schema = tools["add_custom_group_command"].parameters
    assert add_schema["required"] == ["group_name", "command_type"]
    assert add_schema["properties"]["command_type"]["enum"] == ["command", "regex"]
    assert add_schema["properties"]["permission_level"]["enum"] == ["normal", "admin"]
    assert add_schema["properties"]["delegation_policy"]["enum"] == [
        "normal",
        "sensitive",
        "forbidden",
    ]
    assert add_schema["properties"]["history_mode"]["enum"] == [
        "none",
        "command",
        "full",
    ]
    update_schema = tools["update_custom_group_command"].parameters
    assert update_schema["properties"]["clear_linked_plugin"] == {
        "type": "boolean",
        "description": "显式清除插件关联；与非空 linked_plugin 互斥。",
    }
    source = (Path(__file__).parent.parent / "main.py").read_text(encoding="utf-8")
    for name in tools:
        assert f'@filter.llm_tool(name="{name}")' not in source


def test_real_astrbot_context_preserves_all_explicit_tool_ownership(tmp_path):
    """真实 Context 会重写归属；注册完成后 13 个工具必须全部可由插件卸载识别。"""
    root = Path(__file__).parent.parent
    python_candidates = [Path(sys.executable)]
    python_candidates.extend(
        parent / ".venv/bin/python"
        for parent in root.parents
        if (parent / ".venv/bin/python").is_file()
    )
    real_astrbot_python = next(
        (
            executable
            for executable in python_candidates
            if subprocess.run(
                [str(executable), "-c", "import astrbot"],
                capture_output=True,
                check=False,
                cwd=tmp_path,
            ).returncode
            == 0
        ),
        None,
    )
    if real_astrbot_python is None:
        pytest.skip("当前测试环境未安装真实 AstrBot SDK")
    script = r"""
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from astrbot.api.star import Context
from astrbot.core.provider.func_tool_manager import FunctionToolManager

root = Path(sys.argv[1])
for name, path in (
    ("data", root.parent.parent),
    ("data.plugins", root.parent),
    ("data.plugins.astrbot_plugin_helpinfo", root),
):
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package
spec = importlib.util.spec_from_file_location(
    "data.plugins.astrbot_plugin_helpinfo.main", root / "main.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

context = object.__new__(Context)
manager = FunctionToolManager()
context.provider_manager = SimpleNamespace(llm_tools=manager)
plugin = object.__new__(module.HelpPlugin)
plugin.context = context
plugin._register_runtime_tools()
plugin._register_custom_group_tools()

expected = "data.plugins.astrbot_plugin_helpinfo.main"
owned = [tool.name for tool in manager.func_list if tool.handler_module_path == expected]
removable = [
    tool.name
    for tool in manager.func_list
    if tool.handler_module_path and tool.handler_module_path.startswith(expected)
]
print(json.dumps({"owned": owned, "removable": removable}))
"""
    completed = subprocess.run(
        [str(real_astrbot_python), "-c", script, str(root)],
        check=True,
        capture_output=True,
        text=True,
        # 真实 AstrBot SDK 导入会初始化 cwd/data；隔离到 pytest 临时目录，
        # 防止框架副作用污染插件根目录并掩盖运行态路径回归。
        cwd=tmp_path,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert len(result["owned"]) == 13
    assert set(result["removable"]) == set(result["owned"])


def test_all_parameterized_llm_tools_declare_astrbot_parseable_parameters():
    """带参数的 AI tool 都必须避免被 AstrBot 注册成空 schema。"""
    module = _load_entry_module()
    tool_names = (
        "search_command",
        "execute_astrbot_command",
        "create_custom_group",
        "update_custom_group",
        "preview_delete_custom_group",
        "confirm_delete_custom_group",
        "add_custom_group_command",
        "update_custom_group_command",
        "delete_custom_group_command",
    )

    for tool_name in tool_names:
        tool = getattr(module.HelpPlugin, tool_name)
        expected_names = {
            parameter.name
            for parameter in inspect.signature(tool).parameters.values()
            if parameter.name not in {"self", "event"}
        }
        documented_names = {
            name
            for name, _ in re.findall(
                r"^[ \t]*(\w+)\(([\w\[\]]+)\):",
                inspect.getdoc(tool) or "",
                flags=re.MULTILINE,
            )
        }

        assert documented_names == expected_names, tool_name


@pytest.mark.asyncio
async def test_llm_write_tool_rejects_non_admin_without_calling_shared_service(
    monkeypatch, mock_event
):
    """入口层必须在写入服务之前拒绝普通用户。"""
    module = _load_entry_module()

    class Service:
        called = False

        async def create_group(self, *_args):
            self.called = True
            raise AssertionError("普通用户不得进入写服务")

    service = Service()
    monkeypatch.setattr(module, "get_custom_group_service", lambda: service)
    plugin = object.__new__(module.HelpPlugin)

    response = json.loads(await plugin.create_custom_group(mock_event, "常用"))

    assert response["error"] == "permission_denied"
    assert service.called is False


@pytest.mark.asyncio
async def test_web_create_keeps_payload_shape_and_uses_one_atomic_service_call(
    monkeypatch,
):
    """旧 Web create payload 必须委托同一原子服务操作。"""
    from quart import Quart

    module = _load_entry_module()

    class Service:
        received: dict | None = None

        async def create_group_with_commands(self, group_name, **kwargs):
            self.received = {"group_name": group_name, **kwargs}
            return {"success": True, "error": None, "warnings": []}

    service = Service()
    monkeypatch.setattr(module, "get_custom_group_service", lambda: service)
    plugin = object.__new__(module.HelpPlugin)
    app = Quart(__name__)
    payload = {
        "group_name": "常用",
        "description": "常用查询",
        "priority": 1,
        "hidden": False,
        "commands": [{"type": "command", "command": "天气", "aliases": ["查天气"]}],
    }

    async with app.test_request_context("/", json=payload):
        response = await plugin.api_create_custom_group()

    assert await response.get_json() == {"success": True}
    assert service.received == {
        "group_name": "常用",
        "description": "常用查询",
        "priority": 1,
        "hidden": False,
        "commands": payload["commands"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("commands", [None, False, ""])
async def test_web_create_rejects_explicit_invalid_commands_without_mutating_groups(
    monkeypatch, commands
):
    """缺失 commands 允许空组，但显式无效值必须以 400 拒绝且不写入。"""
    from quart import Quart
    from src.application.services.custom_group_service import CustomGroupService

    module = _load_entry_module()
    store = InMemoryGroups([CustomGroupConfig(group_name="已有")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )
    monkeypatch.setattr(module, "get_custom_group_service", lambda: service)
    plugin = object.__new__(module.HelpPlugin)
    app = Quart(__name__)

    async with app.test_request_context("/", json={"group_name": "空组"}):
        success_response = await plugin.api_create_custom_group()
    assert await success_response.get_json() == {"success": True}
    assert store.groups[-1].group_name == "空组"

    payload = {"group_name": "不应写入", "commands": commands}
    async with app.test_request_context("/", json=payload):
        response, status = await plugin.api_create_custom_group()

    assert status == 400
    assert (await response.get_json())["success"] is False
    assert [group.group_name for group in store.groups] == ["已有", "空组"]


@pytest.mark.asyncio
async def test_web_update_rejects_explicit_null_commands_without_replacing_group(
    monkeypatch,
):
    """更新同样只把缺失 commands 视为空列表，显式 null 不得清空旧组。"""
    from quart import Quart
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    module = _load_entry_module()
    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="保留",
                commands=[CustomGroupCommand(command="查询")],
            )
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )
    monkeypatch.setattr(module, "get_custom_group_service", lambda: service)
    plugin = object.__new__(module.HelpPlugin)
    app = Quart(__name__)
    payload = {
        "index": 0,
        "group": {"group_name": "保留", "commands": None},
    }

    async with app.test_request_context("/", json=payload):
        response, status = await plugin.api_update_custom_group()

    assert status == 400
    assert (await response.get_json())["success"] is False
    assert store.groups[0].commands[0].command == "查询"


@pytest.mark.asyncio
async def test_web_success_response_keeps_runtime_invalidation_warnings(monkeypatch):
    """Web 成功写入不能吞掉命令索引或渲染缓存失效警告。"""
    from quart import Quart
    from src.application.services.custom_group_service import CustomGroupService

    module = _load_entry_module()
    store = InMemoryGroups()

    def fail_index_invalidation() -> None:
        raise RuntimeError("index unavailable")

    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=fail_index_invalidation,
        clear_runtime_cache=lambda: None,
    )
    monkeypatch.setattr(module, "get_custom_group_service", lambda: service)
    plugin = object.__new__(module.HelpPlugin)
    app = Quart(__name__)

    async with app.test_request_context("/", json={"group_name": "已保存"}):
        response = await plugin.api_create_custom_group()

    body = await response.get_json()
    assert body["success"] is True
    assert any("命令索引失效失败" in warning for warning in body["warnings"])


def test_sync_config_resets_custom_group_service_on_reload(monkeypatch):
    """配置重载必须废弃旧服务实例，以使已签发的删除 token 无效。"""
    from src.application.services import help_service
    from src.application.services.custom_group_service import (
        get_custom_group_service,
        reset_custom_group_service,
    )

    reset_custom_group_service()
    old_service = get_custom_group_service()
    reset_calls: list[None] = []

    class CommandIndex:
        def update_config(self):
            pass

    class Executor:
        cfg = None

    class Config:
        pass

    service = object.__new__(help_service.HelpService)
    service.command_index = CommandIndex()
    service.command_executor = Executor()
    monkeypatch.setattr(help_service, "refresh_config", lambda _raw: None)
    monkeypatch.setattr(help_service, "get_config", lambda: Config())
    monkeypatch.setattr(
        help_service,
        "reset_custom_group_service",
        lambda: reset_calls.append(None) or reset_custom_group_service(),
    )

    service.sync_config({})

    assert reset_calls == [None]
    assert get_custom_group_service() is not old_service
    reset_custom_group_service()


@pytest.mark.asyncio
async def test_replace_group_validates_all_commands_before_one_atomic_commit():
    """Web 整组更新遇到非法条目时，不得留下半写入的分组。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="常用")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.replace_group(
        "常用",
        group_name="新常用",
        commands=[
            {"type": "command", "command": "查询"},
            {"type": "regex", "pattern": "["},
        ],
    )

    assert response["success"] is False
    assert response["error"] == "invalid_pattern"
    assert [group.group_name for group in store.groups] == ["常用"]
    assert store.saved == []


@pytest.mark.asyncio
async def test_list_groups_filters_hidden_and_admin_entries_for_regular_user():
    """普通读取只暴露可见组中的普通可见目录条目。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="公开",
                commands=[
                    CustomGroupCommand(command="查询"),
                    CustomGroupCommand(command="管理", is_admin=True),
                    CustomGroupCommand(command="隐藏命令", hidden=True),
                ],
            ),
            CustomGroupConfig(group_name="隐藏组", hidden=True),
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
    )

    response = await service.list_groups(is_admin=False)

    assert response["success"] is True
    assert response["filtered"] is True
    assert response["groups"] == [
        {
            "group_name": "公开",
            "description": "",
            "commands": [
                {
                    "command": "查询",
                    "type": "command",
                    "description": "",
                    "is_admin": False,
                    "permission_level": "normal",
                    "delegation_policy": "normal",
                    "history_mode": "command",
                    "hidden": False,
                    "aliases": [],
                    "pattern": "",
                    "examples": [],
                    "sub_commands": [],
                    "linked_plugin": None,
                    "availability": "available",
                }
            ],
            "priority": 0,
            "hidden": False,
        }
    ]


@pytest.mark.asyncio
async def test_create_group_trims_name_and_rejects_duplicate_without_mutating_store():
    """创建空组使用规范化名称，重复写入不会触发保存。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups()
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    created = await service.create_group("  常用  ", description="常用目录")
    duplicate = await service.create_group("常用")

    assert created["success"] is True
    assert created["group"]["group_name"] == "常用"
    assert store.groups[0].commands == []
    assert duplicate["success"] is False
    assert duplicate["error"] == "group_already_exists"
    assert len(store.saved) == 1


@pytest.mark.asyncio
async def test_add_unverified_command_keeps_directory_entry_and_returns_warning():
    """目录条目不要求真实 handler 存在，但 AI 必须收到未验证提示。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="常用")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        find_real_command=lambda _: False,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.add_command("常用", "command", command="  查询  ")

    assert response["success"] is True
    assert response["verified"] is False
    assert "未在真实命令索引" in response["warnings"][0]
    assert store.groups[0].commands[0].command == "查询"


@pytest.mark.asyncio
async def test_add_regex_rejects_invalid_pattern_or_nonmatching_explicit_examples():
    """新增正则必须可编译，且每个显式 example 必须真的匹配。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="正则")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    invalid = await service.add_command("正则", "regex", pattern="[")
    mismatch = await service.add_command(
        "正则", "regex", pattern=r"^天气.+$", examples=["查询天气"]
    )

    assert invalid["error"] == "invalid_pattern"
    assert mismatch["error"] == "regex_example_mismatch"
    assert store.groups[0].commands == []
    assert store.saved == []


@pytest.mark.asyncio
async def test_command_primary_triggers_and_aliases_are_unique_inside_group():
    """同组主触发式与别名组成同一命名空间，不能静默覆盖。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="常用",
                commands=[CustomGroupCommand(command="查询", aliases=["查"])],
            )
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.add_command("常用", "command", command="查")

    assert response["success"] is False
    assert response["error"] == "trigger_conflict"
    assert len(store.groups[0].commands) == 1


@pytest.mark.asyncio
async def test_regex_pattern_and_alias_cannot_reuse_the_same_trigger():
    """正则的 pattern 同样是主触发式，不能与其 aliases 重名。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="正则")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.add_command(
        "正则", "regex", pattern=r"^查询$", aliases=[r"^查询$"]
    )

    assert response["success"] is False
    assert response["error"] == "trigger_conflict"


@pytest.mark.asyncio
async def test_add_command_rejects_invalid_alias_container_instead_of_silently_clearing_it():
    """调用方传入非列表 aliases 时必须暴露参数错误，不能静默降级为空列表。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="常用")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.add_command("常用", "command", command="查询", aliases="")

    assert response["success"] is False
    assert response["error"] == "invalid_alias"
    assert store.groups[0].commands == []


@pytest.mark.asyncio
async def test_update_command_distinguishes_omitted_fields_from_explicit_empty_values():
    """更新中的 None 保持字段，空字符串和空列表按调用者意图清空。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="常用",
                commands=[
                    CustomGroupCommand(
                        command="查询",
                        description="旧描述",
                        aliases=["查"],
                        examples=["查询天气"],
                        sub_commands=["今日"],
                    )
                ],
            )
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.update_command(
        "常用",
        "command",
        "查询",
        description="",
        aliases=[],
        examples=[],
        sub_commands=[],
    )

    assert response["success"] is True
    command = store.groups[0].commands[0]
    assert command.command == "查询"
    assert command.description == ""
    assert command.aliases == []
    assert command.examples == []
    assert command.sub_commands == []


@pytest.mark.asyncio
async def test_update_command_can_explicitly_clear_linked_plugin_without_overloading_empty_string():
    """清除关联必须用专用布尔字段；省略保持原值，冲突请求明确拒绝。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="常用")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )
    created = await service.add_command(
        "常用",
        "command",
        command="查询",
        linked_plugin="weather_plugin",
        availability="missing_plugin",
    )
    preserved = await service.update_command(
        "常用", "command", "查询", description="保留关联"
    )
    conflict = await service.update_command(
        "常用",
        "command",
        "查询",
        linked_plugin="other_plugin",
        clear_linked_plugin=True,
    )
    cleared = await service.update_command(
        "常用", "command", "查询", clear_linked_plugin=True
    )

    assert created["success"] is True
    assert preserved["group"]["commands"][0]["linked_plugin"] == "weather_plugin"
    assert conflict["success"] is False
    assert conflict["error"] == "linked_plugin_clear_conflict"
    assert cleared["success"] is True
    command = store.groups[0].commands[0]
    assert command.linked_plugin is None
    assert command.availability == "available"


@pytest.mark.asyncio
async def test_alias_only_legacy_entries_require_unambiguous_alias_to_update():
    """旧 alias-only 数据可继续定位；同一 alias 旧数据歧义时不能猜测。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="旧数据",
                commands=[
                    CustomGroupCommand(command="", aliases=["单独"]),
                    CustomGroupCommand(command="", aliases=["歧义"]),
                    CustomGroupCommand(command="", aliases=["歧义"]),
                ],
            )
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    updated = await service.update_command(
        "旧数据", "command", "单独", description="已迁移"
    )
    ambiguous = await service.update_command(
        "旧数据", "command", "歧义", description="不能写"
    )

    assert updated["success"] is True
    assert store.groups[0].commands[0].description == "已迁移"
    assert ambiguous["success"] is False
    assert ambiguous["error"] == "ambiguous_trigger"


@pytest.mark.asyncio
async def test_delete_group_token_is_single_use_and_invalid_after_content_changes():
    """整组删除只能确认一次，预览后的任何内容变化都会使 token 失效。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="可删",
                commands=[CustomGroupCommand(command="查询")],
            )
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    stale_preview = await service.preview_delete_group("可删")
    await service.update_group("可删", description="内容已变")
    stale = await service.confirm_delete_group("可删", stale_preview["delete_token"])
    preview = await service.preview_delete_group("可删")
    deleted = await service.confirm_delete_group("可删", preview["delete_token"])
    reused = await service.confirm_delete_group("可删", preview["delete_token"])

    assert stale["error"] == "invalid_delete_token"
    assert deleted["success"] is True
    assert store.groups == []
    assert reused["error"] == "invalid_delete_token"


@pytest.mark.asyncio
async def test_delete_command_keeps_empty_group():
    """删除单条命令不需要 token，且不得连带删除已为空的分组。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="保留", commands=[CustomGroupCommand(command="查询")]
            )
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.delete_command("保留", "command", "查询")

    assert response["success"] is True
    assert len(store.groups) == 1
    assert store.groups[0].commands == []


@pytest.mark.asyncio
async def test_persistence_failure_leaves_current_memory_and_caches_untouched():
    """落盘失败时，候选数据不得提前写入内存或执行缓存失效。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="已有")])
    invalidations: list[str] = []
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=lambda _: False,
        invalidate_command_index=lambda: invalidations.append("index"),
        clear_runtime_cache=lambda: invalidations.append("render"),
    )

    response = await service.create_group("不会写入")

    assert response["success"] is False
    assert response["error"] == "persistence_failed"
    assert [group.group_name for group in store.groups] == ["已有"]
    assert invalidations == []


@pytest.mark.asyncio
async def test_invalidation_failure_is_warning_after_successful_persistence():
    """保存成功后缓存失效失败必须保留成功状态并向调用者显露。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups()

    def fail_index_invalidation() -> None:
        raise RuntimeError("index unavailable")

    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=fail_index_invalidation,
        clear_runtime_cache=lambda: None,
    )

    response = await service.create_group("已保存")

    assert response["success"] is True
    assert [group.group_name for group in store.groups] == ["已保存"]
    assert any("命令索引失效失败" in warning for warning in response["warnings"])


@pytest.mark.asyncio
async def test_update_group_renames_without_losing_commands_and_rejects_name_collision():
    """分组重命名保留其目录内容，并按规范名称检测冲突。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="旧名", commands=[CustomGroupCommand(command="查询")]
            ),
            CustomGroupConfig(group_name="已存在"),
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    renamed = await service.update_group("旧名", new_group_name="  新名  ")
    collision = await service.update_group("新名", new_group_name="已存在")

    assert renamed["success"] is True
    assert store.groups[0].group_name == "新名"
    assert store.groups[0].commands[0].command == "查询"
    assert collision["error"] == "group_already_exists"


@pytest.mark.asyncio
async def test_delete_token_cannot_cross_service_instances_after_reload():
    """确认 token 只驻留当前服务实例，重载后不可继续使用。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="可删")])
    first = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )
    preview = await first.preview_delete_group("可删")
    reloaded = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await reloaded.confirm_delete_group("可删", preview["delete_token"])

    assert response["error"] == "invalid_delete_token"
    assert [group.group_name for group in store.groups] == ["可删"]


@pytest.mark.asyncio
async def test_concurrent_mutations_are_serialized_before_duplicate_check_and_save():
    """同一服务实例的并发写操作不得绕过唯一性校验。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups()

    async def slow_save(groups: list[CustomGroupConfig]) -> bool:
        await asyncio.sleep(0)
        store.saved.append(list(groups))
        return True

    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=slow_save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    first, second = await asyncio.gather(
        service.create_group("并发"), service.create_group("并发")
    )

    assert sorted(response["success"] for response in [first, second]) == [False, True]
    assert [group.group_name for group in store.groups] == ["并发"]


@pytest.mark.asyncio
async def test_write_inputs_reject_invalid_scalar_and_list_types_before_persistence():
    """写入边界不依赖 Pydantic 隐式转换，错误输入不得保存或失效。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="常用", commands=[CustomGroupCommand(command="查询")]
            )
        ]
    )
    invalidations: list[str] = []
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: invalidations.append("index"),
        clear_runtime_cache=lambda: invalidations.append("render"),
    )

    invalid_priority = await service.create_group("错误优先级", priority="high")
    invalid_examples = await service.add_command(
        "常用", "regex", pattern="^a$", examples="ab"
    )
    invalid_admin = await service.add_command(
        "常用", "command", command="管理", is_admin="true"
    )
    invalid_hidden = await service.add_command(
        "常用", "command", command="隐藏", hidden=1
    )
    invalid_sub_commands = await service.add_command(
        "常用", "command", command="子命令", sub_commands=["有效", 1]
    )
    invalid_command_type = await service.delete_command("常用", ["command"], "查询")
    invalid_trigger = await service.delete_command("常用", "command", None)

    assert invalid_priority["error"] == "invalid_priority"
    assert invalid_examples["error"] == "invalid_examples"
    assert invalid_admin["error"] == "invalid_is_admin"
    assert invalid_hidden["error"] == "invalid_hidden"
    assert invalid_sub_commands["error"] == "invalid_sub_commands"
    assert invalid_command_type["error"] == "invalid_command_type"
    assert invalid_trigger["error"] == "invalid_trigger"
    assert len(store.saved) == 0
    assert invalidations == []


@pytest.mark.asyncio
async def test_normal_command_trigger_equivalence_ignores_slash_and_case():
    """普通命令的冲突和自然键定位遵循运行时命令查询的 slash/case 等价。"""
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupCommand

    store = InMemoryGroups(
        [
            CustomGroupConfig(
                group_name="常用",
                commands=[CustomGroupCommand(command="Foo", aliases=["Bar"])],
            )
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
        command_prefixes=lambda: ["/", "!"],
    )

    conflict = await service.add_command("常用", "command", command="/foo")
    injected_prefix_conflict = await service.add_command(
        "常用", "command", command="!foo"
    )
    updated = await service.update_command(
        "常用", "command", "/BAR", description="已定位 alias"
    )

    assert conflict["error"] == "trigger_conflict"
    assert injected_prefix_conflict["error"] == "trigger_conflict"
    assert updated["success"] is True
    assert store.groups[0].commands[0].description == "已定位 alias"


@pytest.mark.asyncio
async def test_regex_examples_follow_runtime_ignorecase_matching():
    """正则示例校验必须完整复用命令索引的匹配前处理与 flags。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="正则")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.add_command(
        "正则", "regex", pattern="ABC", examples=["abc"]
    )
    inline_case_sensitive = await service.add_command(
        "正则", "regex", pattern=r"(?-i:ABC)", examples=["ABC"]
    )
    raw_pattern = "(?x)ABC "
    preserved = await service.add_command(
        "正则", "regex", pattern=raw_pattern, examples=["abc"]
    )
    deleted = await service.delete_command("正则", "regex", raw_pattern)

    assert response["success"] is True
    assert inline_case_sensitive["error"] == "regex_example_mismatch"
    assert preserved["success"] is True
    assert preserved["group"]["commands"][-1]["pattern"] == raw_pattern
    assert deleted["success"] is True


@pytest.mark.asyncio
async def test_delete_preview_keeps_only_latest_token_and_mutations_clear_it():
    """同组预览不会累积 token，更新、确认和删除都会清理关联确认状态。"""
    from src.application.services.custom_group_service import CustomGroupService

    store = InMemoryGroups([CustomGroupConfig(group_name="可删")])
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    first = await service.preview_delete_group("可删")
    second = await service.preview_delete_group("可删")
    obsolete = await service.confirm_delete_group("可删", first["delete_token"])
    await service.update_group("可删", description="已更新")
    after_update = await service.confirm_delete_group("可删", second["delete_token"])
    latest = await service.preview_delete_group("可删")
    deleted = await service.confirm_delete_group("可删", latest["delete_token"])

    assert obsolete["error"] == "invalid_delete_token"
    assert after_update["error"] == "invalid_delete_token"
    assert deleted["success"] is True
    assert service._delete_tokens == {}
    assert service._latest_delete_tokens == {}
