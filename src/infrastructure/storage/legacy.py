"""旧版 ``custom_groups.json`` 的严格解析与备份。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LegacyImportError(ValueError):
    """旧数据不满足迁移契约。"""


@dataclass(frozen=True)
class LegacyCommand:
    """验证后的旧命令条目。"""

    entry_type: str
    command_key: str
    description: str
    permission_level: str
    delegation_policy: str
    hidden: bool
    aliases: tuple[str, ...]
    examples: tuple[str, ...]
    subcommands: tuple[str, ...]


@dataclass(frozen=True)
class LegacyGroup:
    """验证后的旧分组。"""

    group_name: str
    description: str
    priority: int
    hidden: bool
    commands: tuple[LegacyCommand, ...]


@dataclass(frozen=True)
class LegacyImportPlan:
    """无副作用的旧数据迁移计划。"""

    source_path: Path
    checksum: str
    source_bytes: bytes
    groups: tuple[LegacyGroup, ...]

    @property
    def command_count(self) -> int:
        return sum(len(group.commands) for group in self.groups)


@dataclass(frozen=True)
class LegacyImportReport:
    """可供启动流程和 CLI 共同消费的迁移报告。"""

    status: str
    source_path: Path
    checksum: str
    group_count: int
    command_count: int
    backup_path: Path | None = None
    dry_run: bool = False

    def to_dict(self) -> dict[str, object]:
        """转换为机器可读结构。"""
        return {
            "status": self.status,
            "source_path": str(self.source_path),
            "checksum": self.checksum,
            "group_count": self.group_count,
            "command_count": self.command_count,
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "dry_run": self.dry_run,
        }


def _require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LegacyImportError(f"{location}: 必须是对象")
    return value


def _require_string(value: Any, location: str) -> str:
    """只校验字符串类型，不改变展示字段或正则原文。"""
    if not isinstance(value, str):
        raise LegacyImportError(f"{location}: 必须是字符串")
    return value


def _normalize_required_text(value: Any, location: str) -> str:
    """按既有目录契约规范化名称和普通命令。"""
    result = _require_string(value, location).strip()
    if not result:
        raise LegacyImportError(f"{location}: 不能为空")
    return result


def _optional_bool(data: dict[str, Any], key: str, location: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise LegacyImportError(f"{location}.{key}: 必须是布尔值")
    return value


def _preserved_string_list(
    data: dict[str, Any], key: str, location: str
) -> tuple[str, ...]:
    """校验字符串列表，但逐字保留 examples/sub_commands。"""
    value = data.get(key, [])
    if not isinstance(value, list):
        raise LegacyImportError(f"{location}.{key}: 必须是字符串数组")
    for index, item in enumerate(value):
        _require_string(item, f"{location}.{key}[{index}]")
    return tuple(value)


def _trigger_key(trigger: str, entry_type: str) -> str:
    if entry_type == "regex":
        return trigger
    normalized = trigger.strip()
    while normalized.startswith("/"):
        normalized = normalized[1:]
    return normalized.casefold()


def _normalized_aliases(
    data: dict[str, Any], location: str, entry_type: str
) -> tuple[str, ...]:
    value = data.get("aliases", [])
    if not isinstance(value, list):
        raise LegacyImportError(f"{location}.aliases: 必须是字符串数组")
    aliases: list[str] = []
    keys: set[str] = set()
    for index, item in enumerate(value):
        alias = _require_string(item, f"{location}.aliases[{index}]").strip()
        if not alias:
            raise LegacyImportError(f"{location}.aliases[{index}]: 不能为空")
        key = _trigger_key(alias, entry_type)
        if key in keys:
            raise LegacyImportError(f"{location}.aliases: 不能包含重复触发式")
        aliases.append(alias)
        keys.add(key)
    return tuple(aliases)


def _parse_command(raw: Any, group_name: str, index: int) -> LegacyCommand:
    location = f"分组 {group_name!r} 的命令[{index}]"
    data = _require_mapping(raw, location)
    entry_type = data.get("type", "command")
    if entry_type not in {"command", "regex"}:
        raise LegacyImportError(f"{location}.type: 仅支持 command 或 regex")
    aliases = _normalized_aliases(data, location, entry_type)
    examples = _preserved_string_list(data, "examples", location)
    subcommands = _preserved_string_list(data, "sub_commands", location)

    if entry_type == "regex":
        raw_pattern = data.get("pattern", "")
        if raw_pattern == "" and isinstance(data.get("command"), str):
            raw_pattern = data["command"]
            if raw_pattern.startswith("regex:"):
                raw_pattern = raw_pattern.removeprefix("regex:")
        command_key = _require_string(raw_pattern, f"{location}.pattern")
        if command_key == "":
            raise LegacyImportError(f"{location}.pattern: 不能为空")
        try:
            compiled = re.compile(command_key, re.IGNORECASE)
        except re.error as error:
            raise LegacyImportError(
                f"{location}.pattern: 非法正则 {command_key!r}: {error}"
            ) from error
        for example_index, example in enumerate(examples):
            if compiled.search(example.lower().strip()) is None:
                raise LegacyImportError(
                    f"{location}.examples[{example_index}]: 示例 {example!r} "
                    f"不匹配正则 {command_key!r}"
                )
    else:
        raw_command = _require_string(data.get("command", ""), f"{location}.command")
        command_key = raw_command.strip()
        if not command_key and not aliases:
            raise LegacyImportError(
                f"{location}.command: command 与 aliases 不能同时为空"
            )

    primary_key = _trigger_key(command_key, entry_type) if command_key else None
    if primary_key is not None and primary_key in {
        _trigger_key(alias, entry_type) for alias in aliases
    }:
        raise LegacyImportError(f"{location}: 主触发式与 aliases 不能重复")

    is_admin = _optional_bool(data, "is_admin", location)
    return LegacyCommand(
        entry_type=entry_type,
        command_key=command_key,
        description=_require_string(
            data.get("description", ""), f"{location}.description"
        ),
        permission_level="admin" if is_admin else "normal",
        # 旧管理员命令按计划保守提升，避免迁移后扩大跨用户委托权限。
        delegation_policy="sensitive" if is_admin else "normal",
        hidden=_optional_bool(data, "hidden", location),
        aliases=aliases,
        examples=examples,
        subcommands=subcommands,
    )


def build_legacy_import_plan(source_path: str | Path) -> LegacyImportPlan:
    """读取并完整验证旧文件，不产生任何写入副作用。"""
    path = Path(source_path).resolve()
    try:
        source_bytes = path.read_bytes()
    except OSError as error:
        raise LegacyImportError(f"无法读取旧数据文件 {path}: {error}") from error
    checksum = hashlib.sha256(source_bytes).hexdigest()
    try:
        raw = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LegacyImportError(f"旧数据 JSON 无效: {error}") from error
    if not isinstance(raw, list):
        raise LegacyImportError("旧数据顶层必须是分组数组")

    groups: list[LegacyGroup] = []
    names: set[str] = set()
    for group_index, group_raw in enumerate(raw):
        location = f"分组[{group_index}]"
        data = _require_mapping(group_raw, location)
        group_name = _normalize_required_text(
            data.get("group_name"), f"{location}.group_name"
        )
        if group_name in names:
            raise LegacyImportError(f"{location}.group_name: 重复分组 {group_name!r}")
        names.add(group_name)
        commands_raw = data.get("commands", [])
        if not isinstance(commands_raw, list):
            raise LegacyImportError(f"分组 {group_name!r}.commands: 必须是数组")
        priority = data.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise LegacyImportError(f"分组 {group_name!r}.priority: 必须是整数")
        groups.append(
            LegacyGroup(
                group_name=group_name,
                description=_require_string(
                    data.get("description", ""),
                    f"分组 {group_name!r}.description",
                ),
                priority=priority,
                hidden=_optional_bool(data, "hidden", f"分组 {group_name!r}"),
                commands=tuple(
                    _parse_command(item, group_name, command_index)
                    for command_index, item in enumerate(commands_raw)
                ),
            )
        )
    return LegacyImportPlan(
        source_path=path,
        checksum=checksum,
        source_bytes=source_bytes,
        groups=tuple(groups),
    )


def create_legacy_backup(plan: LegacyImportPlan) -> Path:
    """以时间和内容哈希命名备份，并同步文件以降低迁移中断风险。"""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = plan.source_path.with_name(
        f"{plan.source_path.name}.backup.{timestamp}.{plan.checksum[:12]}"
    )
    # 必须写入已经通过校验并参与 checksum 的同一份 bytes；若再次读取源路径，
    # 文件在校验和备份之间变化时会产生“哈希与备份不一致”的 TOCTOU 缺陷。
    with backup_path.open("xb") as backup_file:
        backup_file.write(plan.source_bytes)
        backup_file.flush()
        os.fsync(backup_file.fileno())
    # 文件 fsync 只保证内容；父目录也必须同步，才能保证崩溃后备份目录项存在。
    directory_fd = os.open(backup_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return backup_path
