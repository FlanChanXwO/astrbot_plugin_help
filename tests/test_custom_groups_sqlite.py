"""SQLite 自定义命令目录公共接口行为测试。"""

from __future__ import annotations

import json

import pytest

from src.infrastructure.storage import CommandCatalog


def _group(name: str = "常用") -> dict[str, object]:
    return {
        "group_name": name,
        "description": "常见操作",
        "priority": 2,
        "hidden": False,
        "commands": [
            {
                "type": "command",
                "command": "打卡",
                "description": "每日签到",
                "permission_level": "admin",
                "delegation_policy": "sensitive",
                "history_mode": "command",
                "hidden": False,
                "aliases": ["签到"],
                "examples": ["打卡"],
                "sub_commands": ["补签"],
                "linked_plugin": "wakepro",
                "availability": "missing_plugin",
            }
        ],
    }


def test_catalog_custom_group_crud_round_trips_compatibility_fields(tmp_path):
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()

    catalog.create_custom_group(_group())
    stored = catalog.list_custom_groups()[0]

    assert stored["commands"][0] == {
        **_group()["commands"][0],
        "is_admin": True,
        "pattern": "",
    }

    replacement = _group("每日")
    replacement["commands"][0]["permission_level"] = "normal"
    replacement["commands"][0]["is_admin"] = False
    replacement["commands"][0]["delegation_policy"] = "normal"
    catalog.replace_custom_group("常用", replacement)
    catalog.create_custom_entry(
        "每日",
        {
            "type": "regex",
            "pattern": r"^给.+打卡$",
            "description": "代打卡",
            "permission_level": "normal",
            "delegation_policy": "normal",
            "history_mode": "command",
            "aliases": [],
            "examples": ["给橡皮糖打卡"],
            "sub_commands": [],
        },
    )
    catalog.delete_custom_entry("每日", "regex", r"^给.+打卡$")

    assert [group["group_name"] for group in catalog.list_custom_groups()] == ["每日"]
    assert (
        catalog.list_custom_groups()[0]["commands"][0]["permission_level"] == "normal"
    )
    catalog.delete_custom_group("每日")
    assert catalog.list_custom_groups() == []


def test_catalog_rejects_unsafe_history_atomically(tmp_path):
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    invalid = _group()
    invalid["commands"][0]["history_mode"] = "full"

    with pytest.raises(ValueError, match="history_mode=full"):
        catalog.create_custom_group(invalid)

    assert catalog.list_custom_groups() == []


def test_legacy_import_runs_only_once_even_when_source_changes(tmp_path):
    source = tmp_path / "custom_groups.json"
    source.write_text(json.dumps([_group()], ensure_ascii=False), encoding="utf-8")
    catalog = CommandCatalog(tmp_path / "command_catalog.db")

    first = catalog.import_legacy_custom_groups(source)
    source.write_text(
        json.dumps([_group("后来新增")], ensure_ascii=False), encoding="utf-8"
    )
    second = catalog.import_legacy_custom_groups(source)

    assert first.status == "imported"
    assert second.status == "already_migrated"
    assert [group["group_name"] for group in catalog.list_custom_groups()] == ["常用"]


@pytest.mark.asyncio
async def test_default_custom_group_service_commits_catalog_before_memory(
    tmp_path, monkeypatch
):
    from src.application.services import custom_group_service as service_module
    from src.infrastructure.config import config_manager

    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    config_manager.init_config({})
    service_module.bind_custom_group_catalog(catalog)
    invalidations: list[str] = []
    service = service_module.CustomGroupService(
        invalidate_command_index=lambda: invalidations.append("index"),
        clear_runtime_cache=lambda: None,
    )

    response = await service.create_group_with_commands(
        "SQLite",
        commands=[
            {
                "command": "查询",
                "permission_level": "normal",
                "is_admin": False,
                "delegation_policy": "normal",
                "history_mode": "command",
            }
        ],
    )

    assert response["success"] is True
    assert catalog.list_custom_groups()[0]["group_name"] == "SQLite"
    assert config_manager.get_config().custom_groups[0].group_name == "SQLite"
    assert invalidations == ["index"]
    service_module.reset_custom_group_service()
    config_manager.clear_config()


@pytest.mark.asyncio
async def test_is_admin_and_permission_level_are_bidirectionally_compatible():
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import (
        CustomGroupCommand,
        CustomGroupConfig,
    )

    groups = [
        CustomGroupConfig(
            group_name="兼容", commands=[CustomGroupCommand(command="管理")]
        )
    ]

    def save(candidate):
        return True

    def publish(candidate):
        groups[:] = candidate

    service = CustomGroupService(
        get_groups=lambda: groups,
        set_groups=publish,
        save_groups=save,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    promoted = await service.update_command("兼容", "command", "管理", is_admin=True)
    downgraded = await service.update_command(
        "兼容", "command", "管理", permission_level="normal"
    )

    assert promoted["success"] is True
    assert promoted["group"]["commands"][0]["permission_level"] == "admin"
    assert promoted["group"]["commands"][0]["delegation_policy"] == "sensitive"
    assert downgraded["success"] is True
    assert groups[0].commands[0].is_admin is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "permission", "is_admin"),
    [
        ({"permission_level": "admin"}, "admin", True),
        ({"is_admin": True}, "admin", True),
        ({}, "normal", False),
        ({"is_admin": False}, "normal", False),
    ],
)
async def test_service_distinguishes_missing_legacy_admin_from_explicit_false(
    payload, permission, is_admin
):
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupConfig

    groups = [CustomGroupConfig(group_name="兼容")]
    service = CustomGroupService(
        get_groups=lambda: groups,
        set_groups=lambda candidate: groups.__setitem__(slice(None), candidate),
        save_groups=lambda _candidate: True,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.add_command("兼容", "command", command="测试", **payload)

    assert response["success"] is True
    command = groups[0].commands[0]
    assert command.permission_level == permission
    assert command.is_admin is is_admin


@pytest.mark.asyncio
async def test_service_rejects_only_explicit_permission_conflict():
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupConfig

    groups = [CustomGroupConfig(group_name="冲突")]
    service = CustomGroupService(
        get_groups=lambda: groups,
        set_groups=lambda candidate: groups.__setitem__(slice(None), candidate),
        save_groups=lambda _candidate: True,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.create_group_with_commands(
        "新组",
        commands=[
            {
                "command": "管理",
                "permission_level": "admin",
                "is_admin": False,
            }
        ],
    )

    assert response["error"] == "inconsistent_permission"
    assert [group.group_name for group in groups] == ["冲突"]


@pytest.mark.asyncio
async def test_group_payload_and_update_accept_permission_without_legacy_field():
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupConfig

    groups = [CustomGroupConfig(group_name="更新")]
    service = CustomGroupService(
        get_groups=lambda: groups,
        set_groups=lambda candidate: groups.__setitem__(slice(None), candidate),
        save_groups=lambda _candidate: True,
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    created = await service.create_group_with_commands(
        "Web",
        commands=[{"command": "管理", "permission_level": "admin"}],
    )
    await service.add_command("更新", "command", command="切换")
    updated = await service.update_command(
        "更新", "command", "切换", permission_level="admin"
    )

    assert created["success"] is True
    assert created["group"]["commands"][0]["is_admin"] is True
    assert created["group"]["commands"][0]["delegation_policy"] == "sensitive"
    assert updated["success"] is True
    assert updated["group"]["commands"][0]["is_admin"] is True
    assert updated["group"]["commands"][0]["delegation_policy"] == "sensitive"


def test_sqlite_schema_and_repository_reject_admin_normal_policy(tmp_path):
    import sqlite3

    from src.infrastructure.storage import CatalogCommand

    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()

    with pytest.raises(ValueError, match="至少为 sensitive"):
        catalog.save_command(
            CatalogCommand(
                source_kind="custom",
                command_key="危险",
                permission_level="admin",
                delegation_policy="normal",
            )
        )
    with catalog._connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO commands(
                source_kind, command_key, permission_level, delegation_policy
            ) VALUES ('custom', '绕过', 'admin', 'normal')
            """
        )

    with catalog._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 0


def test_group_replacement_and_delete_remove_orphan_command_policies(tmp_path):
    from src.application.services.command_runtime_service import CommandRuntimeService
    from src.infrastructure.config.datamodels import HelpPluginConfig

    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=None,
        command_executor=None,
    )
    runtime.initialize()
    runtime.catalog.create_custom_group(
        {"group_name": "策略", "commands": [{"command": "切换"}]}
    )

    runtime.catalog.replace_custom_group(
        "策略",
        {
            "group_name": "策略",
            "commands": [
                {
                    "command": "切换",
                    "permission_level": "admin",
                    "delegation_policy": "sensitive",
                }
            ],
        },
    )
    replaced = runtime.find_command("切换")
    assert replaced is not None
    assert replaced["permission_level"] == "admin"
    assert runtime.catalog_service.list_commands(page=1, page_size=10)["total"] == 1

    runtime.catalog.replace_all_custom_groups(
        [
            {
                "group_name": "策略",
                "commands": [
                    {
                        "command": "切换",
                        "permission_level": "admin",
                        "delegation_policy": "forbidden",
                    }
                ],
            }
        ]
    )

    policy = runtime.find_command("切换")
    assert policy is not None
    assert policy["permission_level"] == "admin"
    assert policy["delegation_policy"] == "forbidden"
    assert runtime.catalog_service.list_commands(page=1, page_size=10)["total"] == 1

    runtime.catalog.delete_custom_group("策略")
    assert runtime.find_command("切换") is None
    assert runtime.catalog_service.list_commands(page=1, page_size=10)["total"] == 0


def test_replace_all_rolls_back_orphan_cleanup_with_invalid_new_group(tmp_path):
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    catalog.create_custom_group(
        {"group_name": "原组", "commands": [{"command": "保留"}]}
    )

    with pytest.raises(ValueError):
        catalog.replace_all_custom_groups(
            [
                {"group_name": "临时", "commands": [{"command": "半写入"}]},
                {"group_name": "损坏", "commands": [{"command": ""}]},
            ]
        )

    assert [group["group_name"] for group in catalog.list_custom_groups()] == ["原组"]
    commands = catalog.list_custom_groups()[0]["commands"]
    assert [command["command"] for command in commands] == ["保留"]


def test_concurrent_group_replacements_leave_one_complete_command_set(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    catalog.create_custom_group(
        {"group_name": "并发", "commands": [{"command": "初始"}]}
    )
    candidates = [
        [{"group_name": "并发", "commands": [{"command": "甲"}]}],
        [{"group_name": "并发", "commands": [{"command": "乙"}]}],
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(catalog.replace_all_custom_groups, candidate)
            for candidate in candidates
        ]
        for future in futures:
            future.result()

    groups = catalog.list_custom_groups()
    assert len(groups) == 1
    assert len(groups[0]["commands"]) == 1
    assert groups[0]["commands"][0]["command"] in {"甲", "乙"}
    with catalog._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_legacy_alias_only_and_whitespace_round_trip_when_group_metadata_changes(
    tmp_path,
):
    from src.application.services.custom_group_service import CustomGroupService
    from src.infrastructure.config.datamodels import CustomGroupConfig

    source = tmp_path / "custom_groups.json"
    source.write_text(
        json.dumps(
            [
                {
                    "group_name": "legacy",
                    "description": "  old group  ",
                    "commands": [
                        {
                            "type": "command",
                            "command": "   ",
                            "description": "  original description  ",
                            "aliases": ["  /Alias  "],
                            "examples": ["  example  ", ""],
                            "sub_commands": ["  child  ", ""],
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.import_legacy_custom_groups(source)
    before = catalog.list_custom_groups()[0]
    groups = [CustomGroupConfig.model_validate(before)]
    service = CustomGroupService(
        get_groups=lambda: groups,
        set_groups=lambda candidate: groups.__setitem__(slice(None), candidate),
        save_groups=lambda candidate: (
            catalog.replace_all_custom_groups(
                [group.model_dump(mode="json") for group in candidate]
            )
            is None
        ),
        invalidate_command_index=lambda: None,
        clear_runtime_cache=lambda: None,
    )

    response = await service.update_group("legacy", description="新分组描述")

    after = catalog.list_custom_groups()[0]
    assert response["success"] is True
    assert after["description"] == "新分组描述"
    assert after["commands"] == before["commands"]
