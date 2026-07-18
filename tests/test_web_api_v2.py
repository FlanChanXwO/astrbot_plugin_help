"""v2 Web API 与前端契约测试。"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from quart import Quart

from tests.test_custom_group_management import InMemoryGroups, _load_entry_module
from src.application.services.custom_group_service import CustomGroupService
from src.infrastructure.config.datamodels import CustomGroupConfig


@pytest.mark.asyncio
async def test_web_group_delete_requires_preview_token(monkeypatch):
    module = _load_entry_module()
    store = InMemoryGroups([CustomGroupConfig(group_name="危险组")])
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

    async with app.test_request_context("/", json={"group_name": "危险组"}):
        preview = await plugin.api_preview_delete_custom_group()
    preview_body = await preview.get_json()
    async with app.test_request_context(
        "/",
        json={
            "group_name": "危险组",
            "confirmation_token": preview_body["delete_token"],
        },
    ):
        deleted = await plugin.api_delete_custom_group()

    assert preview_body["success"] is True
    assert preview_body["group"]["group_name"] == "危险组"
    assert (await deleted.get_json())["success"] is True
    assert store.groups == []


@pytest.mark.asyncio
async def test_commands_api_returns_pagination_and_updates_policy(monkeypatch):
    module = _load_entry_module()

    class CatalogService:
        def list_commands(self, **kwargs):
            assert kwargs == {"page": 2, "page_size": 5, "filter": "天气"}
            return {"items": [{"id": 7}], "total": 11, "page": 2, "page_size": 5}

        def update_command_policy(self, command_id, **policy):
            assert command_id == 7
            assert policy["delegation_policy"] == "forbidden"
            return {"id": 7, **policy}

    runtime = SimpleNamespace(
        catalog_service=CatalogService(),
        catalog=SimpleNamespace(list_custom_groups=lambda: []),
    )
    invalidations: list[str] = []
    help_service = SimpleNamespace(
        command_runtime=runtime,
        config=SimpleNamespace(custom_groups=[]),
        command_index=SimpleNamespace(update_config=lambda: None),
    )
    monkeypatch.setattr(module, "get_help_service", lambda: help_service)
    monkeypatch.setattr(
        module, "invalidate_command_cache", lambda: invalidations.append("cache")
    )
    plugin = object.__new__(module.HelpPlugin)
    app = Quart(__name__)

    async with app.test_request_context(
        "/", query_string={"page": 2, "page_size": 5, "query": "天气"}
    ):
        listed = await plugin.api_get_commands()
    async with app.test_request_context(
        "/", json={"command_id": 7, "delegation_policy": "forbidden"}
    ):
        updated = await plugin.api_update_command_policy()

    assert await listed.get_json() == {
        "success": True,
        "items": [{"id": 7}],
        "total": 11,
        "page": 2,
        "page_size": 5,
    }
    assert (await updated.get_json())["command"]["delegation_policy"] == "forbidden"
    assert invalidations == ["cache"]


@pytest.mark.asyncio
async def test_policy_update_invalidates_warm_search_and_execution_immediately(
    tmp_path, monkeypatch
):
    from src.application.services.command_runtime_service import CommandRuntimeService
    from src.application.services.delegated_command_service import (
        DelegatedCommandService,
    )
    from src.application.services.help_service import HelpService
    from src.infrastructure.analysis import command_index as index_module
    from src.infrastructure.config import get_config, init_config
    from src.infrastructure.context_holder import set_context
    from tests.mocks import MockAstrMessageEvent, MockContext

    module = _load_entry_module()
    config = init_config({})
    context = MockContext()
    set_context(context)
    monkeypatch.setattr(
        index_module,
        "get_commands_cache_path",
        lambda: tmp_path / "commands_cache.json",
    )
    index_module.reset_command_index()
    index = index_module.get_command_index()
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=config,
        context=context,
        command_index=index,
        command_executor=None,
    )
    runtime.initialize()
    runtime.catalog.create_custom_group(
        {
            "group_name": "即时策略",
            "commands": [{"command": "热命令", "permission_level": "normal"}],
        }
    )
    config.custom_groups = [
        CustomGroupConfig.model_validate(group)
        for group in runtime.catalog.list_custom_groups()
    ]
    index.update_config()
    assert index.search_commands("热命令")[0]["tag"] == "normal"

    class Executor:
        async def execute(self, **_kwargs):
            raise AssertionError("普通用户不得执行刚提升的管理员命令")

    executor = Executor()
    service = HelpService.__new__(HelpService)
    service.config = get_config()
    service.context = context
    service.command_runtime = runtime
    service.command_index = index
    service.command_executor = executor
    service.prefixes = ["/"]

    async def allowed(_event):
        return None

    service._resolve_allowed_plugins = allowed
    service.delegated_command_service = DelegatedCommandService(
        runtime=runtime,
        command_executor=executor,
        command_index=index,
        config_getter=lambda: service.config,
        prefixes_getter=lambda: service.prefixes,
        resolve_target=service._resolve_target,
        resolve_allowed_plugins=service._resolve_allowed_plugins,
        is_command_invokable=service._is_command_invokable,
    )
    monkeypatch.setattr(module, "get_help_service", lambda: service)
    command_id = runtime.catalog_service.list_commands(page=1, page_size=10)["items"][
        0
    ]["id"]
    plugin = object.__new__(module.HelpPlugin)
    app = Quart(__name__)

    async with app.test_request_context(
        "/",
        json={
            "command_id": command_id,
            "permission_level": "admin",
            "delegation_policy": "sensitive",
        },
    ):
        response = await plugin.api_update_command_policy()

    event = MockAstrMessageEvent(user_id="user", group_id="group")
    search = json.loads(await service.search_command(event, "热命令"))
    execution = json.loads(await service.execute_command(event, "热命令"))

    assert (await response.get_json())["success"] is True
    assert "/热命令" not in json.dumps(search, ensure_ascii=False)
    assert execution["execution_state"] == "rejected"
    assert "管理员" in execution["error"]


def test_custom_command_tools_and_ui_expose_v2_policy_fields():
    module = _load_entry_module()
    add_parameters = inspect.signature(
        module.HelpPlugin.add_custom_group_command
    ).parameters
    update_parameters = inspect.signature(
        module.HelpPlugin.update_custom_group_command
    ).parameters
    for name in (
        "permission_level",
        "delegation_policy",
        "history_mode",
        "linked_plugin",
        "availability",
    ):
        assert name in add_parameters
        assert name in update_parameters

    root = Path(__file__).parent.parent
    html = (root / "pages/dashboard/index.html").read_text(encoding="utf-8")
    api = (root / "pages/dashboard/js/api.js").read_text(encoding="utf-8")
    assert "命令目录" in html
    assert "permission_level" in html
    assert "delete-preview" in api
    assert "result.success !== true" in api
    assert "assume success" not in api.casefold()
    app = (root / "pages/dashboard/app.js").read_text(encoding="utf-8")
    assert 'v-model="cmd.is_admin"' not in html
    assert "is_admin: cmd.is_admin" not in app
    assert "currentGroupName" in app
    assert "current_group_name" in api


def test_command_examples_editor_is_shared_by_normal_and_regex_entries():
    """两种触发类型必须复用同一个 examples 编辑器及同一提交字段。"""
    root = Path(__file__).parent.parent
    html = (root / "pages/dashboard/index.html").read_text(encoding="utf-8")
    app = (root / "pages/dashboard/app.js").read_text(encoding="utf-8")

    assert html.count('data-field="command-examples"') == 1
    assert 'data-command-types="command regex"' in html
    assert 'v-for="item in cmd.examples"' in html
    assert "cmd.examples.push" in html
    assert "cmd.examples.splice" in html
    assert "examples: [...(c.examples || [])]" in app
    assert "examples: [...(cmd.examples || [])]" in app


@pytest.mark.asyncio
async def test_web_update_uses_stable_group_name_after_list_position_drifts(
    monkeypatch,
):
    module = _load_entry_module()
    store = InMemoryGroups(
        [
            CustomGroupConfig(group_name="A"),
            CustomGroupConfig(group_name="B"),
            CustomGroupConfig(group_name="C"),
        ]
    )
    service = CustomGroupService(
        get_groups=lambda: store.groups,
        set_groups=store.set_groups,
        save_groups=store.save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )
    store.groups.pop(0)  # 用户打开 B 后，另一个管理者删除了 A。
    monkeypatch.setattr(module, "get_custom_group_service", lambda: service)
    plugin = object.__new__(module.HelpPlugin)
    app = Quart(__name__)

    async with app.test_request_context(
        "/",
        json={
            "index": 1,
            "current_group_name": "B",
            "group": {"group_name": "B", "description": "已稳定更新"},
        },
    ):
        response = await plugin.api_update_custom_group()

    assert (await response.get_json())["success"] is True
    assert [(group.group_name, group.description) for group in store.groups] == [
        ("B", "已稳定更新"),
        ("C", ""),
    ]


@pytest.mark.asyncio
async def test_web_rename_without_stable_group_name_is_rejected(monkeypatch):
    module = _load_entry_module()
    store = InMemoryGroups(
        [CustomGroupConfig(group_name="A"), CustomGroupConfig(group_name="B")]
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

    async with app.test_request_context(
        "/", json={"index": 1, "group": {"group_name": "新名称"}}
    ):
        response, status = await plugin.api_update_custom_group()

    assert status == 400
    assert "current_group_name" in (await response.get_json())["error"]
    assert [group.group_name for group in store.groups] == ["A", "B"]
