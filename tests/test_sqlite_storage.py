"""SQLite 命令目录仓储的行为测试。"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.infrastructure.storage import (
    CatalogCommand,
    CommandCatalog,
    LegacyImportError,
)
from src.infrastructure.storage import catalog as catalog_module
from src.infrastructure.storage import legacy as legacy_module


def test_initialize_creates_versioned_catalog_database(tmp_path):
    """初始化会创建可复用且带明确 schema 版本的目录数据库。"""
    database_path = tmp_path / "command_catalog.db"
    catalog = CommandCatalog(database_path)

    report = catalog.initialize()

    assert database_path.is_file()
    assert report.schema_version == 1
    assert catalog.get_schema_version() == 1


def test_catalog_health_exposes_required_schema_and_sqlite_guards(tmp_path):
    """初始 schema 覆盖 v2 数据域，并确认每个仓储连接启用 WAL 与外键。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()

    health = catalog.get_health()

    assert health.journal_mode == "wal"
    assert health.foreign_keys_enabled is True
    assert {
        "commands",
        "command_aliases",
        "command_examples",
        "command_subcommands",
        "custom_groups",
        "custom_group_commands",
        "user_privacy_settings",
        "session_participants",
        "personal_aliases",
        "execution_receipts",
        "command_history",
        "command_usage_aggregates",
        "legacy_imports",
        "schema_migrations",
    } <= health.tables


def test_command_policy_defaults_and_history_constraint_are_enforced(tmp_path):
    """普通命令采用安全默认值，敏感委托不能保存完整参数。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()

    command_id = catalog.save_command(
        CatalogCommand(source_kind="custom", command_key="打卡")
    )

    stored = catalog.get_command(command_id)
    assert stored.permission_level == "normal"
    assert stored.delegation_policy == "normal"
    assert stored.history_mode == "command"

    with pytest.raises(ValueError, match="history_mode=full"):
        catalog.save_command(
            CatalogCommand(
                source_kind="custom",
                command_key="管理",
                delegation_policy="sensitive",
                history_mode="full",
            )
        )


def test_legacy_import_preserves_groups_triggers_examples_and_permissions(tmp_path):
    """旧 JSON 的目录语义被完整导入，并在写数据库前保留原文备份。"""
    source_path = tmp_path / "custom_groups.json"
    source_path.write_text(
        json.dumps(
            [
                {
                    "group_name": "日常",
                    "description": "日常命令",
                    "priority": 8,
                    "hidden": False,
                    "commands": [
                        {
                            "type": "command",
                            "command": "打卡",
                            "description": "每日打卡",
                            "is_admin": True,
                            "aliases": ["签到"],
                            "examples": ["打卡"],
                            "sub_commands": ["补签"],
                        },
                        {
                            "type": "regex",
                            "pattern": "^天气.+$",
                            "description": "查询天气",
                            "examples": ["天气上海"],
                        },
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()

    report = catalog.import_legacy_custom_groups(source_path)

    assert report.status == "imported"
    assert report.group_count == 1
    assert report.command_count == 2
    assert report.backup_path is not None
    assert report.backup_path.read_bytes() == source_path.read_bytes()
    assert source_path.is_file()
    assert catalog.list_custom_groups() == [
        {
            "group_name": "日常",
            "description": "日常命令",
            "priority": 8,
            "hidden": False,
            "commands": [
                {
                    "type": "command",
                    "command": "打卡",
                    "pattern": "",
                    "description": "每日打卡",
                    "permission_level": "admin",
                    "is_admin": True,
                    "delegation_policy": "sensitive",
                    "history_mode": "command",
                    "hidden": False,
                    "linked_plugin": None,
                    "availability": "available",
                    "aliases": ["签到"],
                    "examples": ["打卡"],
                    "sub_commands": ["补签"],
                },
                {
                    "type": "regex",
                    "command": "^天气.+$",
                    "pattern": "^天气.+$",
                    "description": "查询天气",
                    "permission_level": "normal",
                    "is_admin": False,
                    "delegation_policy": "normal",
                    "history_mode": "command",
                    "hidden": False,
                    "linked_plugin": None,
                    "availability": "available",
                    "aliases": [],
                    "examples": ["天气上海"],
                    "sub_commands": [],
                },
            ],
        }
    ]


def test_legacy_import_accepts_alias_only_normal_command(tmp_path):
    """旧目录允许普通 command 为空，只要存在规范化后的非空 alias。"""
    source_path = tmp_path / "custom_groups.json"
    source_path.write_text(
        '[{"group_name":"legacy","commands":['
        '{"type":"command","command":"   ","aliases":["  /Help  "]}'
        "]}]",
        encoding="utf-8",
    )
    catalog = CommandCatalog(tmp_path / "command_catalog.db")

    catalog.import_legacy_custom_groups(source_path)

    command = catalog.list_custom_groups()[0]["commands"][0]
    assert command["command"] == ""
    assert command["aliases"] == ["/Help"]


def test_legacy_import_only_normalizes_names_commands_and_aliases(tmp_path):
    """正则及展示字段逐字保留，只有既有契约规定的字段去首尾空白。"""
    source_path = tmp_path / "custom_groups.json"
    source_path.write_text(
        json.dumps(
            [
                {
                    "group_name": "  regex group  ",
                    "description": "  group description  ",
                    "commands": [
                        {
                            "type": "regex",
                            "pattern": "  token  ",
                            "description": "  regex description  ",
                            "aliases": ["  alias  "],
                            "examples": ["  xx  token  yy  "],
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

    catalog.import_legacy_custom_groups(source_path)

    group = catalog.list_custom_groups()[0]
    command = group["commands"][0]
    assert group["group_name"] == "regex group"
    assert group["description"] == "  group description  "
    assert command["command"] == "  token  "
    assert command["description"] == "  regex description  "
    assert command["aliases"] == ["alias"]
    assert command["examples"] == ["  xx  token  yy  "]
    assert command["sub_commands"] == ["  child  ", ""]


def test_legacy_import_is_idempotent_for_same_source_checksum(tmp_path):
    """相同来源与内容哈希只导入一次，也只创建一个备份。"""
    source_path = tmp_path / "custom_groups.json"
    source_path.write_text(
        '[{"group_name":"常用","commands":[{"command":"帮助"}]}]',
        encoding="utf-8",
    )
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()

    first = catalog.import_legacy_custom_groups(source_path)
    second = catalog.import_legacy_custom_groups(source_path)

    assert first.status == "imported"
    assert second.status == "already_migrated"
    assert second.backup_path == first.backup_path
    assert len(catalog.list_custom_groups()) == 1
    assert len(list(tmp_path.glob("custom_groups.json.backup.*"))) == 1


def test_legacy_import_is_idempotent_by_checksum_across_source_paths(tmp_path):
    """同一内容即使来自不同路径，也不得重复导入或创建第二份备份。"""
    payload = b'[{"group_name":"common","commands":[{"command":"help"}]}]'
    first_source = tmp_path / "first" / "custom_groups.json"
    second_source = tmp_path / "second" / "renamed_groups.json"
    first_source.parent.mkdir()
    second_source.parent.mkdir()
    first_source.write_bytes(payload)
    second_source.write_bytes(payload)
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()

    first = catalog.import_legacy_custom_groups(first_source)
    second = catalog.import_legacy_custom_groups(second_source)

    assert first.status == "imported"
    assert second.status == "already_migrated"
    assert second.source_path == first_source.resolve()
    assert second.backup_path == first.backup_path
    assert len(catalog.list_custom_groups()) == 1
    assert len(list(first_source.parent.glob("custom_groups.json.backup.*"))) == 1
    assert list(second_source.parent.glob("renamed_groups.json.backup.*")) == []


def test_concurrent_same_content_imports_are_serialized_by_checksum(tmp_path):
    """双连接并发初始化和导入时只写一次，不暴露 locked 或 UNIQUE 竞态。"""
    payload = b'[{"group_name":"concurrent"}]'
    first_source = tmp_path / "first.json"
    second_source = tmp_path / "second.json"
    first_source.write_bytes(payload)
    second_source.write_bytes(payload)
    database_path = tmp_path / "command_catalog.db"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                CommandCatalog(database_path).import_legacy_custom_groups, source
            )
            for source in (first_source, second_source)
        ]
        reports = [future.result() for future in futures]

    assert sorted(report.status for report in reports) == [
        "already_migrated",
        "imported",
    ]
    assert len(list(tmp_path.glob("*.backup.*"))) == 1
    assert len(CommandCatalog(database_path).list_custom_groups()) == 1


def test_initialize_waits_for_abandoned_lock_and_returns_only_after_wal(tmp_path):
    """持锁连接异常关闭后，等待者继续初始化且成功返回时 WAL 已生效。"""
    database_path = tmp_path / "command_catalog.db"
    catalog = CommandCatalog(database_path)
    catalog.initialize()
    with sqlite3.connect(database_path, isolation_level=None) as setup:
        setup.execute("PRAGMA journal_mode = DELETE")
    holder = sqlite3.connect(database_path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(CommandCatalog(database_path).initialize)
        # close 模拟持锁进程异常退出；SQLite/OS 负责释放事务锁。
        holder.close()
        report = future.result()

    assert report.schema_version == 1
    assert CommandCatalog(database_path).get_health().journal_mode == "wal"


def test_initialize_exposes_lock_failure_when_configured_wait_is_exhausted(tmp_path):
    """客观锁等待窗口耗尽时显露 SQLite 错误，不能伪造初始化成功。"""
    database_path = tmp_path / "command_catalog.db"
    CommandCatalog(database_path).initialize()
    holder = sqlite3.connect(database_path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            CommandCatalog(database_path, lock_wait_seconds=0).initialize()
    finally:
        holder.close()


@pytest.mark.parametrize(
    ("payload", "error_fragment"),
    [
        ("{", "JSON 无效"),
        (
            '[{"group_name":"正则","commands":[{"type":"regex","pattern":"["}]}]',
            "分组 '正则' 的命令[0].pattern",
        ),
        (
            '[{"group_name":"正则","commands":'
            '[{"type":"regex","pattern":"^天气.+$",'
            '"examples":["查询天气"]}]}]',
            "不匹配正则",
        ),
    ],
)
def test_invalid_legacy_data_has_no_database_or_backup_side_effects(
    tmp_path, payload, error_fragment
):
    """损坏 JSON、非法正则及错误示例均显露位置，且不产生部分迁移。"""
    source_path = tmp_path / "custom_groups.json"
    source_path.write_text(payload, encoding="utf-8")
    database_path = tmp_path / "command_catalog.db"
    catalog = CommandCatalog(database_path)
    catalog.initialize()

    with pytest.raises(LegacyImportError) as error:
        catalog.import_legacy_custom_groups(source_path)

    assert error_fragment in str(error.value)
    assert catalog.list_custom_groups() == []
    assert list(tmp_path.glob("custom_groups.json.backup.*")) == []


def test_changed_source_is_ignored_after_completed_legacy_import(tmp_path):
    """首次导入完成后，变化的历史 JSON 不再追加或覆盖数据库。"""
    database_path = tmp_path / "command_catalog.db"
    baseline_source = tmp_path / "baseline.json"
    failing_source = tmp_path / "failing.json"
    baseline_source.write_text('[{"group_name":"existing"}]', encoding="utf-8")
    failing_source.write_text(
        '[{"group_name":"new"},{"group_name":"existing"}]', encoding="utf-8"
    )
    catalog = CommandCatalog(database_path)
    catalog.initialize()
    catalog.import_legacy_custom_groups(baseline_source)

    report = catalog.import_legacy_custom_groups(failing_source)

    assert report.status == "already_migrated"
    assert [group["group_name"] for group in catalog.list_custom_groups()] == [
        "existing"
    ]
    assert len(list(tmp_path.glob("failing.json.backup.*"))) == 0


def test_backup_uses_the_exact_bytes_that_were_validated(tmp_path, monkeypatch):
    """源文件在校验后变化时，备份仍与 checksum 对应的原始字节完全一致。"""
    source_path = tmp_path / "custom_groups.json"
    original = b'[{"group_name":"validated"}]'
    changed = b'[{"group_name":"changed"}]'
    source_path.write_bytes(original)
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    real_create_backup = catalog_module.create_legacy_backup

    def mutate_source_after_validation(plan):
        source_path.write_bytes(changed)
        return real_create_backup(plan)

    monkeypatch.setattr(
        catalog_module, "create_legacy_backup", mutate_source_after_validation
    )

    report = catalog.import_legacy_custom_groups(source_path)

    assert source_path.read_bytes() == changed
    assert report.backup_path is not None
    assert report.backup_path.read_bytes() == original
    assert catalog.list_custom_groups()[0]["group_name"] == "validated"


def test_backup_syncs_parent_directory_entry(tmp_path, monkeypatch):
    """备份内容落盘后还会 fsync 父目录，确保目录项可恢复。"""
    source_path = tmp_path / "custom_groups.json"
    source_path.write_text('[{"group_name":"synced"}]', encoding="utf-8")
    directory_fds: set[int] = set()
    synced_directory_fds: list[int] = []
    real_open = legacy_module.os.open
    real_fsync = legacy_module.os.fsync

    def record_open(path, flags):
        fd = real_open(path, flags)
        if Path(path) == tmp_path:
            directory_fds.add(fd)
        return fd

    def record_fsync(fd):
        if fd in directory_fds:
            synced_directory_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(legacy_module.os, "open", record_open)
    monkeypatch.setattr(legacy_module.os, "fsync", record_fsync)

    CommandCatalog(tmp_path / "command_catalog.db").import_legacy_custom_groups(
        source_path
    )

    assert synced_directory_fds


def test_dry_run_validates_without_creating_database_or_backup(tmp_path):
    """dry-run 只返回验证报告，不初始化数据库也不写备份。"""
    source_path = tmp_path / "custom_groups.json"
    source_path.write_text('[{"group_name":"常用"}]', encoding="utf-8")
    database_path = tmp_path / "command_catalog.db"
    catalog = CommandCatalog(database_path)

    report = catalog.import_legacy_custom_groups(source_path, dry_run=True)

    assert report.status == "validated"
    assert report.dry_run is True
    assert report.group_count == 1
    assert report.command_count == 0
    assert not database_path.exists()
    assert list(tmp_path.glob("custom_groups.json.backup.*")) == []
