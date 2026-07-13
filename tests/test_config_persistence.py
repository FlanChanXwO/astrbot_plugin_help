"""配置与自定义命令组持久化的行为测试。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.infrastructure.config import config_manager
from src.infrastructure.config.config_manager import (
    clear_config,
    init_config,
    save_custom_groups_to_storage,
)
from src.infrastructure.config.datamodels import (
    CustomGroupCommand,
    CustomGroupConfig,
    HelpPluginConfig,
)
from src.infrastructure.persistence.cache_manager import CacheManager


def test_legacy_raw_config_uses_ai_command_wait_defaults():
    """旧配置缺少新等待字段时，仍使用产品默认等待窗口。"""
    config = HelpPluginConfig.from_astrbot_config({"enable_ai_command_notify": False})

    assert config.ai_command_auto_wait_seconds == 3
    assert config.ai_command_max_wait_seconds == 60


def test_valid_raw_ai_command_wait_configuration_preserves_values():
    """用户配置的合法等待窗口会完整保留给后续执行 tool 使用。"""
    config = HelpPluginConfig.from_astrbot_config(
        {
            "ai_command_auto_wait_seconds": 4,
            "ai_command_max_wait_seconds": 10,
        }
    )

    assert config.ai_command_auto_wait_seconds == 4
    assert config.ai_command_max_wait_seconds == 10


@pytest.mark.parametrize(
    "raw_config",
    [
        {"ai_command_auto_wait_seconds": 0},
        {"ai_command_max_wait_seconds": -1},
        {"ai_command_auto_wait_seconds": 3, "ai_command_max_wait_seconds": 2},
    ],
)
def test_invalid_ai_command_wait_configuration_is_rejected(raw_config):
    """等待窗口必须为正数，且单次上限不得小于自动窗口。"""
    with pytest.raises(ValidationError):
        HelpPluginConfig.from_astrbot_config(raw_config)


@pytest.mark.parametrize(
    "raw_config",
    [
        {"ai_command_auto_wait_seconds": float("nan")},
        {"ai_command_max_wait_seconds": float("inf")},
        {"ai_command_auto_wait_seconds": "NaN"},
        {"ai_command_max_wait_seconds": "Infinity"},
    ],
)
def test_non_finite_ai_command_wait_configuration_is_rejected(raw_config):
    """配置解析后的等待值必须是有限正数。"""
    with pytest.raises(ValidationError):
        HelpPluginConfig.from_astrbot_config(raw_config)


def test_config_schema_exposes_ai_command_wait_windows():
    """配置界面公开同步监听窗口和自定义等待上限，而非执行取消超时。"""
    schema_path = Path(__file__).parent.parent / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    auto_wait = schema["ai_command_auto_wait_seconds"]
    max_wait = schema["ai_command_max_wait_seconds"]

    assert auto_wait["default"] == 3
    assert max_wait["default"] == 60
    assert "同步监听" in auto_wait["hint"]
    assert "不会取消" in auto_wait["hint"]


def test_save_custom_groups_atomically_replaces_storage_file(tmp_path, monkeypatch):
    """保存成功时，组数据由同目录临时文件原子替换到持久化文件。"""
    storage_path = tmp_path / "custom_groups.json"
    replacement_calls: list[tuple[Path, Path]] = []

    monkeypatch.setattr(
        config_manager, "_get_custom_groups_storage_path", lambda: storage_path
    )
    real_replace = config_manager.os.replace

    def record_replace(source, destination):
        replacement_calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(config_manager.os, "replace", record_replace)

    saved = save_custom_groups_to_storage([CustomGroupConfig(group_name="常用")])

    assert saved is True
    assert json.loads(storage_path.read_text(encoding="utf-8")) == [
        {
            "group_name": "常用",
            "description": "",
            "commands": [],
            "priority": 0,
            "hidden": False,
        }
    ]
    assert len(replacement_calls) == 1
    assert replacement_calls[0][1] == storage_path
    assert replacement_calls[0][0].parent == storage_path.parent
    assert not replacement_calls[0][0].exists()


def test_failed_atomic_save_preserves_existing_file_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    """替换失败时，调用方能收到失败且旧持久化内容与临时文件状态正确。"""
    storage_path = tmp_path / "custom_groups.json"
    old_content = '[{"group_name": "旧分组"}]'
    storage_path.write_text(old_content, encoding="utf-8")

    monkeypatch.setattr(
        config_manager, "_get_custom_groups_storage_path", lambda: storage_path
    )

    def fail_replace(source, destination):
        raise OSError("disk replacement failed")

    monkeypatch.setattr(config_manager.os, "replace", fail_replace)

    saved = save_custom_groups_to_storage([CustomGroupConfig(group_name="新分组")])

    assert saved is False
    assert storage_path.read_text(encoding="utf-8") == old_content
    assert list(tmp_path.glob(".custom_groups.json.*.tmp")) == []


def test_successful_atomic_save_syncs_storage_directory(tmp_path, monkeypatch):
    """替换完成后同步父目录，提升崩溃后的目录项持久化保障。"""
    storage_path = tmp_path / "custom_groups.json"
    directory_fds: set[int] = set()
    synced_directory_fds: list[int] = []

    monkeypatch.setattr(
        config_manager, "_get_custom_groups_storage_path", lambda: storage_path
    )
    real_open = config_manager.os.open
    real_fsync = config_manager.os.fsync

    def record_open(path, flags, *args):
        fd = real_open(path, flags, *args)
        if Path(path) == storage_path.parent:
            directory_fds.add(fd)
        return fd

    def record_fsync(fd):
        if fd in directory_fds:
            synced_directory_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(config_manager.os, "open", record_open)
    monkeypatch.setattr(config_manager.os, "fsync", record_fsync)

    saved = save_custom_groups_to_storage([CustomGroupConfig(group_name="常用")])

    assert saved is True
    assert synced_directory_fds


def test_directory_sync_unavailability_does_not_report_successful_save_as_failed(
    tmp_path, monkeypatch
):
    """目录 fsync 不支持时，文件替换仍应按成功结果返回。"""
    storage_path = tmp_path / "custom_groups.json"
    directory_fds: set[int] = set()

    monkeypatch.setattr(
        config_manager, "_get_custom_groups_storage_path", lambda: storage_path
    )
    real_open = config_manager.os.open
    real_fsync = config_manager.os.fsync

    def record_open(path, flags, *args):
        fd = real_open(path, flags, *args)
        if Path(path) == storage_path.parent:
            directory_fds.add(fd)
        return fd

    def reject_directory_sync(fd):
        if fd in directory_fds:
            raise OSError("directory fsync unavailable")
        real_fsync(fd)

    monkeypatch.setattr(config_manager.os, "open", record_open)
    monkeypatch.setattr(config_manager.os, "fsync", reject_directory_sync)

    saved = save_custom_groups_to_storage([CustomGroupConfig(group_name="常用")])

    assert saved is True
    assert (
        json.loads(storage_path.read_text(encoding="utf-8"))[0]["group_name"] == "常用"
    )


def test_cache_key_changes_when_same_named_group_commands_change(tmp_path, monkeypatch):
    """同名分组内命令变化时，已渲染的帮助图片不能继续命中缓存。"""
    from src.infrastructure.analysis import command_index

    class EmptyContext:
        def get_all_stars(self):
            return []

    class EmptyCommandIndex:
        context = EmptyContext()

    monkeypatch.setattr(command_index, "get_command_index", EmptyCommandIndex)
    monkeypatch.setattr(
        config_manager,
        "_get_custom_groups_storage_path",
        lambda: tmp_path / "custom_groups.json",
    )
    monkeypatch.setattr(
        "src.infrastructure.persistence.cache_manager.get_data_dir", lambda: tmp_path
    )
    config = init_config({})
    cache_manager = CacheManager()

    try:
        config.custom_groups = [
            CustomGroupConfig(
                group_name="常用",
                commands=[CustomGroupCommand(command="天气")],
            )
        ]
        first_key = cache_manager.get_cache_key("all", None, False)

        config.custom_groups = [
            CustomGroupConfig(
                group_name="常用",
                commands=[CustomGroupCommand(command="新闻")],
            )
        ]
        second_key = cache_manager.get_cache_key("all", None, False)
    finally:
        clear_config()

    assert second_key != first_key


def test_help_service_cache_key_changes_when_group_hidden_flag_changes():
    """实际帮助渲染缓存键会包含同名分组的完整可持久化内容。"""
    from src.application.services.help_service import HelpService

    class EmptyContext:
        def get_all_stars(self):
            return []

    service = HelpService.__new__(HelpService)
    service.context = EmptyContext()
    service.config = HelpPluginConfig(
        custom_groups=[
            CustomGroupConfig(
                group_name="常用",
                commands=[CustomGroupCommand(command="天气")],
                hidden=False,
            )
        ]
    )
    first_key = service._get_cache_key("command", None, False)

    service.config.custom_groups = [
        CustomGroupConfig(
            group_name="常用",
            commands=[CustomGroupCommand(command="天气")],
            hidden=True,
        )
    ]
    second_key = service._get_cache_key("command", None, False)

    assert second_key != first_key
