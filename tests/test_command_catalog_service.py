"""命令目录应用服务的行为测试。"""

import pytest

from src.application.services.command_catalog_service import CommandCatalogService
from src.infrastructure.storage import CatalogCommand, CommandCatalog


def test_full_runtime_sync_reconciles_runtime_without_deleting_custom(tmp_path):
    """全量同步删除消失的运行时命令，但绝不删除自定义目录条目。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    custom_id = catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="自定义打卡")
    )
    service = CommandCatalogService(catalog)

    first = service.sync_all_runtime(
        [
            {
                "plugin": "daily",
                "command": "打卡",
                "type": "command",
                "handler_name": "check_in",
                "filter_signature": "CommandFilter:打卡",
                "description": "每日打卡",
            },
            {
                "plugin": "weather",
                "command": "天气",
                "type": "command",
                "handler_name": "weather",
                "filter_signature": "CommandFilter:天气",
            },
        ],
        active_plugins={"daily", "weather"},
    )
    second = service.sync_all_runtime(
        [
            {
                "plugin": "daily",
                "command": "打卡",
                "type": "command",
                "handler_name": "check_in",
                "filter_signature": "CommandFilter:打卡",
                "description": "新版描述",
                "aliases": ["签到"],
                "examples": ["打卡", "打卡 补签"],
                "sub_commands": ["补签"],
            }
        ],
        active_plugins={"daily"},
    )

    page = service.list_commands(page=1, page_size=20)
    assert first == {"upserted": 2, "removed": 0}
    assert second == {"upserted": 1, "removed": 1}
    assert page["total"] == 2
    assert {(item["source_type"], item["command"]) for item in page["items"]} == {
        ("custom", "自定义打卡"),
        ("runtime", "打卡"),
    }
    assert catalog.get_command(custom_id).command_key == "自定义打卡"
    runtime = next(item for item in page["items"] if item["source_type"] == "runtime")
    assert runtime["aliases"] == ["签到"]
    assert runtime["examples"] == ["打卡", "打卡 补签"]
    assert runtime["sub_commands"] == ["补签"]


def test_plugin_lifecycle_marks_linked_custom_and_reconciles_only_that_plugin(tmp_path):
    """卸载删除插件 runtime，并只标记其关联的 custom；重载恢复可用。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    catalog.save_command(
        CatalogCommand(
            source_kind="custom", source_plugin="daily", command_key="代打卡"
        )
    )
    service = CommandCatalogService(catalog)
    entry = {
        "plugin": "daily",
        "command": "打卡",
        "handler_name": "check_in",
        "filter_signature": "CommandFilter:打卡",
    }
    service.sync_all_runtime(
        [entry, {"plugin": "weather", "command": "天气", "handler_name": "weather"}],
        active_plugins={"daily", "weather"},
    )

    unloaded = service.on_plugin_unloaded("daily")
    after_unload = service.list_commands(page=1, page_size=20)
    reloaded = service.sync_plugin_runtime("daily", [entry])
    after_reload = service.list_commands(
        page=1, page_size=20, filter={"plugin": "daily"}
    )

    assert unloaded == {"runtime_removed": 1, "custom_marked_missing": 1}
    assert {(row["plugin"], row["source_type"]) for row in after_unload["items"]} == {
        ("daily", "custom"),
        ("weather", "runtime"),
    }
    custom = next(
        row for row in after_unload["items"] if row["source_type"] == "custom"
    )
    assert custom["availability"] == "missing_plugin"
    assert reloaded == {"upserted": 1, "removed": 0, "custom_restored": 1}
    assert {row["availability"] for row in after_reload["items"]} == {"available"}


def test_runtime_permission_is_conservatively_promoted_and_manual_policy_survives(
    tmp_path,
):
    """管理员检测只向严格方向提升，人工委托策略不会被后续同步降级。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = CommandCatalogService(catalog)
    entry = {
        "plugin": "ops",
        "command": "维护",
        "handler_name": "maintenance",
        "filter_signature": "CommandFilter:维护",
    }
    service.sync_all_runtime([entry], active_plugins={"ops"})
    command_id = service.list_commands(page=1, page_size=10)["items"][0]["id"]
    service.update_command_policy(command_id, delegation_policy="forbidden")

    service.sync_all_runtime(
        [{**entry, "permission_level": "admin"}], active_plugins={"ops"}
    )
    promoted = service.list_commands(page=1, page_size=10)["items"][0]
    service.sync_all_runtime(
        [{**entry, "permission_level": "normal"}], active_plugins={"ops"}
    )
    after_lower_signal = service.list_commands(page=1, page_size=10)["items"][0]

    assert promoted["permission_level"] == "admin"
    assert promoted["delegation_policy"] == "forbidden"
    assert after_lower_signal["permission_level"] == "admin"
    assert after_lower_signal["delegation_policy"] == "forbidden"
    with pytest.raises(ValueError, match="history_mode=full"):
        service.update_command_policy(command_id, history_mode="full")


def test_full_sync_uses_explicit_active_plugin_snapshot_for_linked_custom(tmp_path):
    """无命令但仍活跃的插件不能被误判卸载，冷启动也会恢复关联条目。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    catalog.save_command(
        CatalogCommand(source_kind="custom", source_plugin="empty", command_key="占位")
    )
    catalog.save_command(
        CatalogCommand(source_kind="custom", source_plugin="gone", command_key="旧命令")
    )
    service = CommandCatalogService(catalog)
    service.on_plugin_unloaded("empty")

    service.sync_all_runtime([], active_plugins={"empty"})

    page = service.list_commands(page=1, page_size=20)
    availability = {row["command"]: row["availability"] for row in page["items"]}
    assert availability == {"占位": "available", "旧命令": "missing_plugin"}


def test_runtime_key_canonicalizes_handler_and_filter_container_order(tmp_path):
    """语义相同的 handler/filter 容器不因 dict 插入顺序生成新 runtime。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = CommandCatalogService(catalog)
    common = {"plugin": "daily", "command": "打卡", "type": "command"}
    first_entry = {
        **common,
        "handler_identity": {"module": "daily", "qualname": "Plugin.check_in"},
        "filter_signature": {
            "type": "CommandFilter",
            "config": {"command": "打卡", "aliases": ["签到", "checkin"]},
        },
    }
    second_entry = {
        **common,
        "handler_identity": {"qualname": "Plugin.check_in", "module": "daily"},
        "filter_signature": {
            "config": {"aliases": ["签到", "checkin"], "command": "打卡"},
            "type": "CommandFilter",
        },
    }

    service.sync_all_runtime([first_entry], active_plugins={"daily"})
    first_id = service.list_commands(page=1, page_size=10)["items"][0]["id"]
    report = service.sync_all_runtime([second_entry], active_plugins={"daily"})
    rows = service.list_commands(page=1, page_size=10)["items"]

    assert report == {"upserted": 1, "removed": 0}
    assert len(rows) == 1
    assert rows[0]["id"] == first_id


def test_full_reconcile_rolls_back_upserts_when_stale_runtime_delete_fails(tmp_path):
    """reconcile 任一阶段失败时，新旧 runtime 与关联元数据都保持调用前状态。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = CommandCatalogService(catalog)
    service.sync_all_runtime(
        [
            {
                "plugin": "daily",
                "command": "旧命令",
                "handler_name": "old",
                "aliases": ["旧别名"],
            }
        ],
        active_plugins={"daily"},
    )
    with catalog._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER force_runtime_delete_failure
            BEFORE DELETE ON commands
            WHEN OLD.source_kind = 'runtime'
            BEGIN
                SELECT RAISE(ABORT, 'forced delete failure');
            END
            """
        )

    with pytest.raises(Exception, match="forced delete failure"):
        service.sync_all_runtime(
            [
                {
                    "plugin": "daily",
                    "command": "新命令",
                    "handler_name": "new",
                    "aliases": ["新别名"],
                }
            ],
            active_plugins={"daily"},
        )

    page = service.list_commands(page=1, page_size=20)
    assert [(row["command"], row["aliases"]) for row in page["items"]] == [
        ("旧命令", ["旧别名"])
    ]
