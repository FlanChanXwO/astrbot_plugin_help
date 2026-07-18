"""AI 命令运行时公共入口的集成契约。"""

from __future__ import annotations

import json
from pathlib import Path
from types import MethodType, SimpleNamespace
from datetime import timedelta

import pytest

from src.application.services.command_runtime_service import CommandRuntimeService
from src.application.services.help_service import HelpService
from src.application.services.help_service import init_plugin_service
from src.application.services.delegated_command_service import DelegatedCommandService
from src.infrastructure.analysis.executor import CommandExecutor
from src.infrastructure.storage import CatalogCommand, CommandCatalog
from tests.mocks import MockAstrMessageEvent, MockContext, MockHandler

from src.infrastructure.config.datamodels import (
    CustomGroupCommand,
    CustomGroupConfig,
    HelpPluginConfig,
)


def test_runtime_security_configuration_defaults_are_explicit() -> None:
    """委托、去重及历史窗口必须有保守且可配置的默认值。"""
    config = HelpPluginConfig()

    assert config.enable_sensitive_delegation is False
    assert config.allow_admin_target_override is False
    assert config.ai_command_dedupe_window_seconds == 60
    assert config.command_history_retention_days == 90


def test_runtime_security_configuration_is_exposed_in_webui_schema() -> None:
    """运行时配置必须进入 AstrBot 配置 schema，不能只存在于代码。"""
    with open("_conf_schema.json", encoding="utf-8") as file:
        schema = json.load(file)

    assert schema["enable_sensitive_delegation"]["default"] is False
    assert schema["allow_admin_target_override"]["default"] is False
    assert schema["ai_command_dedupe_window_seconds"]["default"] == 60
    assert schema["command_history_retention_days"]["default"] == 90


def test_runtime_initialization_creates_catalog_and_imports_legacy_once(
    tmp_path: Path,
) -> None:
    """启动应在权威数据目录建库，并严格复用幂等旧数据迁移。"""
    legacy = tmp_path / "custom_groups.json"
    legacy.write_text(
        json.dumps(
            [
                {
                    "group_name": "签到",
                    "commands": [{"command": "打卡", "description": "每日签到"}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=None,
        command_executor=None,
    )

    first = runtime.initialize()
    second = runtime.initialize()

    assert (tmp_path / "command_catalog.db").is_file()
    assert first["legacy_import"]["status"] == "imported"
    assert second["legacy_import"]["status"] == "already_migrated"
    page = runtime.catalog_service.list_commands(page=1, page_size=10)
    assert page["items"][0]["command"] == "打卡"
    assert list(tmp_path.glob("custom_groups.json.backup.*"))


def test_runtime_initialization_does_not_leave_half_initialized_service(
    tmp_path: Path,
) -> None:
    """非法旧数据必须显露，运行时不能宣称已经可用。"""
    (tmp_path / "custom_groups.json").write_text("{broken", encoding="utf-8")
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=None,
        command_executor=None,
    )

    try:
        runtime.initialize()
    except ValueError as error:
        assert "JSON" in str(error)
    else:
        raise AssertionError("非法旧数据应阻止初始化")

    assert runtime.initialized is False
    assert CommandCatalog(tmp_path / "command_catalog.db").get_schema_version() == 1


def test_runtime_reconfigure_updates_all_time_windows(tmp_path: Path) -> None:
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=None,
        command_executor=None,
    )
    runtime.initialize()
    updated = HelpPluginConfig(
        ai_command_dedupe_window_seconds=12,
        command_history_retention_days=7,
    )

    runtime.reconfigure(updated)

    assert runtime.receipt_service.dedupe_window == timedelta(seconds=12)
    assert runtime.history_service.retention == timedelta(days=7)
    assert runtime.identity_service.retention_days == 7
    assert runtime.identity_service._resolver.retention_days == 7


@pytest.mark.parametrize(
    ("policy", "permission"),
    [("forbidden", "normal"), ("sensitive", "admin")],
)
def test_runtime_policy_matches_actual_regex_trigger(
    tmp_path: Path, policy: str, permission: str
) -> None:
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=None,
        command_executor=None,
    )
    runtime.initialize()
    runtime.catalog.save_command(
        CatalogCommand(
            source_kind="custom",
            command_key=r"^给\S+打卡$",
            entry_type="regex",
            permission_level=permission,
            delegation_policy=policy,
        )
    )

    matched = runtime.find_command("给橡皮糖打卡")

    assert matched is not None
    assert matched["delegation_policy"] == policy
    assert matched["permission_level"] == permission


def test_multiple_regex_policies_are_combined_to_strictest(tmp_path: Path) -> None:
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=None,
        command_executor=None,
    )
    runtime.initialize()
    runtime.catalog.save_command(
        CatalogCommand(
            source_kind="custom",
            command_key=r"打卡$",
            entry_type="regex",
            delegation_policy="sensitive",
            history_mode="command",
        )
    )
    runtime.catalog.save_command(
        CatalogCommand(
            source_kind="custom",
            command_key=r"^给.*",
            entry_type="regex",
            permission_level="admin",
            delegation_policy="forbidden",
            history_mode="none",
        )
    )

    matched = runtime.find_command("给橡皮糖打卡")

    assert matched is not None
    assert matched["delegation_policy"] == "forbidden"
    assert matched["permission_level"] == "admin"
    assert matched["history_mode"] == "none"
    assert len(matched["matched_command_ids"]) == 2


@pytest.mark.asyncio
async def test_bare_regex_does_not_bypass_stricter_regex_policy(
    tmp_path: Path,
) -> None:
    """regex `foo` 不是普通 trigger，必须与 `^foo$` 一起合成 forbidden。"""
    service, executor, runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    runtime.catalog.save_command(
        CatalogCommand(
            source_kind="custom",
            command_key="foo",
            entry_type="regex",
            delegation_policy="normal",
        )
    )
    runtime.catalog.save_command(
        CatalogCommand(
            source_kind="custom",
            command_key=r"^foo$",
            entry_type="regex",
            delegation_policy="forbidden",
        )
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    policy = runtime.find_command("foo")
    result = json.loads(
        await service.execute_command(event, "foo", target_user="uid:target")
    )

    assert policy is not None
    assert policy["delegation_policy"] == "forbidden"
    assert len(policy["matched_command_ids"]) == 2
    assert result["execution_state"] == "rejected"
    assert result["error"] == "该命令禁止跨用户委托"
    assert executor.calls == []


def test_sync_all_excludes_inactive_entries_like_active_plugin_snapshot(
    tmp_path: Path,
) -> None:
    class Index:
        def get_all_commands(self):
            return {
                "/active": {"plugin": "active", "command": "/active"},
                "/disabled": {
                    "plugin": "disabled",
                    "command": "/disabled",
                    "inactive": True,
                },
            }

    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=Index(),
        command_executor=None,
    )
    runtime.initialize()

    result = runtime.sync_all(
        [
            SimpleNamespace(name="active", activated=True),
            SimpleNamespace(name="disabled", activated=False),
        ]
    )

    assert result["upserted"] == 1
    page = runtime.catalog_service.list_commands(page=1, page_size=10)
    assert [item["plugin"] for item in page["items"]] == ["active"]


def test_plugin_reinitialize_rebinds_context_config_and_runtime_singletons(
    tmp_path: Path, monkeypatch
) -> None:
    """同一模块内重建插件也不能沿用旧 HelpService/Executor/Context。"""
    from astrbot.api.star import StarTools

    plugin_dir = tmp_path / "astrbot_plugin_helpinfo"
    plugin_dir.mkdir()
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _name: tmp_path / "runtime")
    first_context = MockContext()
    second_context = MockContext()

    first = init_plugin_service(
        first_context, {"ai_command_dedupe_window_seconds": 60}, plugin_dir
    )
    second = init_plugin_service(
        second_context, {"ai_command_dedupe_window_seconds": 11}, plugin_dir
    )

    assert second is not first
    assert second.context is second_context
    assert second.command_executor is not first.command_executor
    assert second.config.ai_command_dedupe_window_seconds == 11


@pytest.mark.asyncio
async def test_management_resolution_can_recover_opted_out_user_without_exposing_ref(
    tmp_path: Path,
) -> None:
    """deny_all 目标仍可由管理员安全定位并恢复，但管理结果不泄露 target_ref。"""
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=None,
        command_executor=None,
    )
    runtime.initialize()
    event = MockAstrMessageEvent(user_id="admin", group_id="group", is_admin_flag=True)
    runtime.identity_service.set_user_settings(
        event.get_platform_id(),
        "target",
        allow_llm_operation=False,
        allow_sensitive_delegation=False,
    )

    ordinary = await runtime.identity_service.resolve(
        event, "uid:target", requester_id="admin"
    )
    public, target_id = await runtime.identity_service.resolve_for_management(
        event, "uid:target", requester_id="admin"
    )

    assert ordinary["status"] == "unavailable"
    assert target_id == "target"
    assert public["status"] == "resolved"
    assert "target_ref" not in public


class _Executor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "success": True,
            "execution_state": "completed",
            "dispatched": True,
            "output_complete": True,
            "messages": ["签到成功"],
            "error": None,
            "matched_handlers": [],
        }


class _GenericOnlyExecutor(_Executor):
    """模拟只由通用消息监听器接管的外部命令路由。"""

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        handler = MockHandler(
            "route_message",
            event_filters=[SimpleNamespace(filter_type="event_message_type")],
            handler_module_path="external_router",
        )
        assert CommandExecutor.is_generic_handler(handler) is True
        return {
            "success": True,
            "execution_state": "external_dispatched",
            "dispatched": True,
            "output_complete": False,
            "messages": [],
            "error": None,
            "matched_handlers": [handler],
        }


class _FailOnceExecutor(_Executor):
    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {
                "success": False,
                "execution_state": "failed",
                "dispatched": False,
                "output_complete": True,
                "messages": [],
                "error": "temporary failure",
                "matched_handlers": [],
            }
        return {
            "success": True,
            "execution_state": "completed",
            "dispatched": True,
            "output_complete": True,
            "messages": ["签到成功"],
            "error": None,
            "matched_handlers": [],
        }


class _DispatchedFailExecutor(_Executor):
    """模拟 handler 已获调度权后才抛错并由 executor 汇总为 failed。"""

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "success": False,
            "execution_state": "failed",
            "dispatched": True,
            "output_complete": True,
            "messages": [],
            "error": "handler failed after dispatch",
            "matched_handlers": [],
        }


class _StartupPendingExecutor(_Executor):
    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "success": True,
            "execution_state": "accepted",
            "result_type": "startup_pending",
            "dispatched": True,
            "output_complete": False,
            "retryable": False,
            "messages": [],
            "error": None,
            "matched_handlers": [],
        }


class _RaisingOnceExecutor(_Executor):
    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("executor exploded before dispatch confirmation")
        return {
            "success": True,
            "execution_state": "completed",
            "dispatched": True,
            "output_complete": True,
            "messages": [],
            "error": None,
            "matched_handlers": [],
        }


class _Index:
    def search_commands(self, *_args, **_kwargs):
        return []


def _runtime_help_service(tmp_path: Path, config: HelpPluginConfig):
    executor = _Executor()
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=config,
        context=None,
        command_index=None,
        command_executor=executor,
    )
    runtime.initialize()
    service = HelpService.__new__(HelpService)
    service.config = config
    service.command_runtime = runtime
    service.command_executor = executor
    service.command_index = _Index()
    service.prefixes = ["/"]

    async def allowed(_self, _event):
        return None

    service._resolve_allowed_plugins = MethodType(allowed, service)
    service.delegated_command_service = DelegatedCommandService(
        runtime=runtime,
        command_executor=executor,
        command_index=service.command_index,
        config_getter=lambda: service.config,
        prefixes_getter=lambda: service.prefixes,
        resolve_target=service._resolve_target,
        resolve_allowed_plugins=service._resolve_allowed_plugins,
        is_command_invokable=service._is_command_invokable,
    )
    return service, executor, runtime


@pytest.mark.asyncio
async def test_custom_linked_to_blacklisted_plugin_can_use_generic_external_router(
    tmp_path: Path,
) -> None:
    """custom 的插件关联是目录元数据，不能挡住实际仅通用 handler 的外部路由。"""
    service, _executor, runtime = _runtime_help_service(
        tmp_path,
        HelpPluginConfig(ai_command_blacklist={"blocked_plugin"}),
    )
    executor = _GenericOnlyExecutor()
    service.command_executor = executor
    service.delegated_command_service.command_executor = executor
    runtime.catalog.save_command(
        CatalogCommand(
            source_kind="custom",
            source_plugin="blocked_plugin",
            command_key="外部签到",
        )
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    result = json.loads(await service.execute_command(event, "外部签到"))

    assert result["execution_state"] == "external_dispatched"
    assert result["dispatched"] is True
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_runtime_command_from_blacklisted_plugin_is_rejected_before_dispatch(
    tmp_path: Path,
) -> None:
    """放行 custom 不能退化成跳过全部目录黑名单检查。"""
    service, _executor, runtime = _runtime_help_service(
        tmp_path,
        HelpPluginConfig(ai_command_blacklist={"blocked_plugin"}),
    )
    executor = _GenericOnlyExecutor()
    service.command_executor = executor
    service.delegated_command_service.command_executor = executor
    runtime.catalog.save_command(
        CatalogCommand(
            source_kind="runtime",
            source_plugin="blocked_plugin",
            command_key="后台管理",
        )
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    result = json.loads(await service.execute_command(event, "后台管理"))

    assert result["execution_state"] == "rejected"
    assert result["error"] == "命令所属插件在 AI 调用黑名单中"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_execute_delegated_command_is_dispatched_once_and_history_belongs_to_target(
    tmp_path: Path,
) -> None:
    """跨用户成功调用只调度一次，重复返回原回执，历史归目标。"""
    service, executor, runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    command_id = runtime.catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="打卡")
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    first = json.loads(
        await service.execute_command(event, "打卡", target_user="uid:target")
    )
    second = json.loads(
        await service.execute_command(event, "打卡", target_user="uid:target")
    )

    assert first["execution_state"] == "completed"
    assert second["execution_state"] == "duplicate_suppressed"
    assert len(executor.calls) == 1
    assert executor.calls[0]["target_user_id"] == "target"
    history = runtime.history_service.list_recent(
        platform_id=event.get_platform_id(),
        target_user_id="target",
        requester_user_id="requester",
        is_admin=True,
    )
    assert history == [
        {
            "command_id": command_id,
            "command_key": "打卡",
            "command_text": None,
            "execution_state": "completed",
            "used_at": history[0]["used_at"],
        }
    ]


@pytest.mark.asyncio
async def test_sensitive_cross_user_command_requires_global_enable(
    tmp_path: Path,
) -> None:
    """即使请求者为管理员，敏感跨用户委托默认仍拒绝。"""
    service, executor, runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    runtime.catalog.save_command(
        CatalogCommand(
            source_kind="custom",
            command_key="管理签到",
            permission_level="admin",
            delegation_policy="sensitive",
        )
    )
    event = MockAstrMessageEvent(user_id="admin", group_id="group", is_admin_flag=True)

    result = json.loads(
        await service.execute_command(event, "管理签到", target_user="uid:target")
    )

    assert result["execution_state"] == "rejected"
    assert "全局未启用" in result["error"]
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [False, True])
async def test_admin_override_controls_execution_for_deny_all_target(
    tmp_path: Path, override: bool
) -> None:
    service, executor, runtime = _runtime_help_service(
        tmp_path, HelpPluginConfig(allow_admin_target_override=override)
    )
    runtime.catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="打卡")
    )
    event = MockAstrMessageEvent(user_id="admin", group_id="group", is_admin_flag=True)
    runtime.identity_service.set_user_settings(
        event.get_platform_id(),
        "target",
        allow_llm_operation=False,
        allow_sensitive_delegation=False,
    )

    result = json.loads(
        await service.execute_command(event, "打卡", target_user="uid:target")
    )

    if override:
        assert result["execution_state"] == "completed"
        assert len(executor.calls) == 1
        assert "target_ref" not in result["target"]
        assert "target" not in json.dumps(result["target"], ensure_ascii=False)
    else:
        assert result["execution_state"] == "rejected"
        assert executor.calls == []


@pytest.mark.asyncio
async def test_cross_user_unknown_catalog_policy_fails_closed(tmp_path: Path) -> None:
    service, executor, _runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    result = json.loads(
        await service.execute_command(event, "目录外命令", target_user="uid:target")
    )

    assert result["execution_state"] == "rejected"
    assert result["error"] == "command_policy_unknown"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_actor_self_and_target_user_are_mutually_exclusive(
    tmp_path: Path,
) -> None:
    service, executor, _runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    event = MockAstrMessageEvent(group_id="group")

    result = json.loads(
        await service.execute_command(
            event, "打卡", actor="self", target_user="uid:target"
        )
    )

    assert result["execution_state"] == "rejected"
    assert "互斥" in result["error"]
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("permission_filter", ["admin", "all"])
async def test_normal_requester_cannot_expand_search_permission(
    tmp_path: Path, permission_filter: str
) -> None:
    """普通请求者不能通过 tool 参数越权读取管理员命令。"""
    service, _executor, _runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    event = MockAstrMessageEvent(user_id="normal", group_id="group")

    result = json.loads(
        await service.search_command(event, "管理", permission_filter=permission_filter)
    )

    assert result["success"] is False
    assert result["error"] == "permission_filter_exceeds_requester"


@pytest.mark.asyncio
async def test_custom_group_search_fallback_filters_hidden_and_admin_entries(
    tmp_path: Path,
) -> None:
    """无真实搜索结果时，普通用户也不能从诊断 fallback 读取受限目录。"""
    service, _executor, _runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    service.config.custom_groups = [
        CustomGroupConfig(
            group_name="混合目录",
            commands=[
                CustomGroupCommand(command="公开查询"),
                CustomGroupCommand(
                    command="管理触发",
                    permission_level="admin",
                    is_admin=True,
                    delegation_policy="sensitive",
                ),
                CustomGroupCommand(command="隐形触发", hidden=True),
            ],
        ),
        CustomGroupConfig(
            group_name="绝密目录",
            hidden=True,
            commands=[CustomGroupCommand(command="秘密查询")],
        ),
    ]
    normal = MockAstrMessageEvent(user_id="normal", group_id="group")
    admin = MockAstrMessageEvent(user_id="admin", group_id="group", is_admin_flag=True)

    normal_admin = await service.search_command(normal, "管理触发")
    normal_hidden = await service.search_command(normal, "隐形触发")
    normal_group = await service.search_command(normal, "绝密目录")
    normal_mixed = json.loads(await service.search_command(normal, "混合目录"))
    admin_result = await service.search_command(admin, "管理触发")

    for response in (normal_admin, normal_hidden, normal_group):
        parsed = json.loads(response)
        assert parsed["success"] is False
        assert "Found a custom group configuration" not in parsed["message"]
        assert "混合目录" not in parsed["message"]
        assert "公开查询" not in parsed["message"]
        assert "秘密查询" not in parsed["message"]
    assert (
        "Found a custom group configuration '混合目录'"
        in json.loads(admin_result)["message"]
    )
    assert "公开查询" in normal_mixed["message"]
    assert "管理触发" not in normal_mixed["message"]
    assert "隐形触发" not in normal_mixed["message"]


@pytest.mark.asyncio
async def test_custom_group_search_note_filters_group_name_by_requester_permission(
    tmp_path: Path,
) -> None:
    """有真实结果时的附注同样不能泄露隐藏组名，但管理员仍可见。"""
    service, _executor, _runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    service.config.custom_groups = [
        CustomGroupConfig(
            group_name="秘密目录",
            hidden=True,
            commands=[CustomGroupCommand(command="秘密命令")],
        )
    ]

    class SearchIndex:
        def search_commands(self, *_args, **_kwargs):
            return [
                {
                    "command": "/普通结果",
                    "description": "公开结果",
                    "plugin": "public_plugin",
                    "aliases": [],
                    "type": "command",
                    "tag": "normal",
                }
            ]

    service.command_index = SearchIndex()
    normal = MockAstrMessageEvent(user_id="normal", group_id="group")
    admin = MockAstrMessageEvent(user_id="admin", group_id="group", is_admin_flag=True)

    normal_result = json.loads(await service.search_command(normal, "秘密"))
    admin_result = json.loads(await service.search_command(admin, "秘密"))

    assert normal_result["success"] is True
    assert "秘密目录" not in normal_result["message"]
    assert admin_result["success"] is True
    assert "秘密目录" in admin_result["message"]


@pytest.mark.asyncio
async def test_failed_execution_can_be_retried_and_is_not_added_to_history(
    tmp_path: Path,
) -> None:
    """真实失败不占用成功去重键，下一次调用必须能重新调度。"""
    service, _executor, runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    executor = _FailOnceExecutor()
    service.command_executor = executor
    service.delegated_command_service.command_executor = executor
    runtime.catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="打卡")
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    failed = json.loads(await service.execute_command(event, "打卡"))
    retried = json.loads(await service.execute_command(event, "打卡"))

    assert failed["execution_state"] == "failed"
    assert failed["retryable"] is True
    assert retried["execution_state"] == "completed"
    assert len(executor.calls) == 2
    history = runtime.history_service.list_recent(
        platform_id=event.get_platform_id(),
        target_user_id="requester",
        requester_user_id="requester",
    )
    assert len(history) == 1


@pytest.mark.asyncio
async def test_failed_after_dispatch_is_suppressed_without_becoming_accepted(
    tmp_path: Path,
) -> None:
    """已派发失败保留 failed，立即重复只复用原回执且不再次执行。"""
    service, _executor, runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    executor = _DispatchedFailExecutor()
    service.command_executor = executor
    service.delegated_command_service.command_executor = executor
    runtime.catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="半途失败")
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    failed = json.loads(await service.execute_command(event, "半途失败"))
    duplicate = json.loads(await service.execute_command(event, "半途失败"))

    assert failed["execution_state"] == "failed"
    assert failed["dispatched"] is True
    assert failed["retryable"] is False
    assert duplicate["execution_state"] == "duplicate_suppressed"
    assert duplicate["receipt_id"] == failed["receipt_id"]
    assert len(executor.calls) == 1
    assert (
        runtime.history_service.list_recent(
            platform_id=event.get_platform_id(),
            target_user_id="requester",
            requester_user_id="requester",
        )
        == []
    )


@pytest.mark.asyncio
async def test_startup_pending_receipt_suppresses_immediate_duplicate(
    tmp_path: Path,
) -> None:
    """调度器仍初始化时已算受理，立即重复不得创建第二个后台任务。"""
    service, _executor, runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    executor = _StartupPendingExecutor()
    service.command_executor = executor
    service.delegated_command_service.command_executor = executor
    runtime.catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="慢命令")
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    first = json.loads(await service.execute_command(event, "慢命令"))
    duplicate = json.loads(await service.execute_command(event, "慢命令"))

    assert first["execution_state"] == "accepted"
    assert first["retryable"] is False
    assert duplicate["execution_state"] == "duplicate_suppressed"
    assert duplicate["receipt_id"] == first["receipt_id"]
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_executor_exception_finalizes_failed_and_next_call_can_retry(
    tmp_path: Path,
) -> None:
    service, _executor, runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    executor = _RaisingOnceExecutor()
    service.command_executor = executor
    service.delegated_command_service.command_executor = executor
    runtime.catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="会失败")
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    failed = json.loads(await service.execute_command(event, "会失败"))
    retried = json.loads(await service.execute_command(event, "会失败"))

    assert failed["execution_state"] == "failed"
    assert failed["retryable"] is True
    assert retried["execution_state"] == "completed"
    assert len(executor.calls) == 2


@pytest.mark.asyncio
async def test_history_failure_warns_after_accepted_receipt_and_still_dedupes(
    tmp_path: Path, monkeypatch
) -> None:
    service, _executor, runtime = _runtime_help_service(tmp_path, HelpPluginConfig())
    executor = _StartupPendingExecutor()
    service.command_executor = executor
    service.delegated_command_service.command_executor = executor
    runtime.catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="慢命令")
    )
    monkeypatch.setattr(
        runtime.history_service,
        "record_execution",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("history unavailable")),
    )
    event = MockAstrMessageEvent(user_id="requester", group_id="group")

    accepted = json.loads(await service.execute_command(event, "慢命令"))
    duplicate = json.loads(await service.execute_command(event, "慢命令"))

    assert accepted["execution_state"] == "accepted"
    assert "history unavailable" in accepted["warnings"][0]
    assert duplicate["execution_state"] == "duplicate_suppressed"
    assert (
        runtime.receipt_service.get_receipt(accepted["receipt_id"])["execution_state"]
        == "accepted"
    )
    assert len(executor.calls) == 1
