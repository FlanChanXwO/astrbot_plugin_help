"""运行时命令目录同步及策略管理。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from typing import Any, Collection, Mapping, Sequence

from ...infrastructure.storage import CommandCatalog


class CommandCatalogService:
    """用小型公开接口封装目录同步、筛选与安全策略。"""

    def __init__(self, catalog: CommandCatalog) -> None:
        self.catalog = catalog

    @staticmethod
    def _entry_dict(entry: object) -> dict[str, Any]:
        if isinstance(entry, Mapping):
            return dict(entry)
        if is_dataclass(entry):
            return asdict(entry)
        to_dict = getattr(entry, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        raise TypeError("命令条目必须是 CommandEntry、dataclass 或 mapping")

    @classmethod
    def _canonicalize_signature(cls, value: object) -> object:
        """把 handler/filter 签名转换为可稳定 JSON 编码的容器。"""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Mapping):
            return {
                str(key): cls._canonicalize_signature(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (set, frozenset)):
            normalized = [cls._canonicalize_signature(item) for item in value]
            return sorted(
                normalized,
                key=lambda item: json.dumps(
                    item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            )
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [cls._canonicalize_signature(item) for item in value]
        return str(value)

    @classmethod
    def _signature_value(
        cls, entry: Mapping[str, Any], primary: str, fallback: str
    ) -> object:
        value = entry.get(primary)
        if value is None or value == "":
            value = entry.get(fallback, "")
        return cls._canonicalize_signature(value)

    @classmethod
    def _signature_text(
        cls, entry: Mapping[str, Any], primary: str, fallback: str
    ) -> str:
        value = cls._signature_value(entry, primary, fallback)
        if isinstance(value, str):
            return value
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _runtime_key(entry: Mapping[str, Any]) -> str:
        """生成不依赖源码行号的运行时命令身份。"""
        identity = {
            "plugin": str(entry.get("plugin") or entry.get("source_plugin") or ""),
            "command": str(entry.get("command") or entry.get("pattern") or ""),
            "type": str(entry.get("type") or "command"),
            "handler": CommandCatalogService._signature_value(
                entry, "handler_identity", "handler_name"
            ),
            "filter": CommandCatalogService._signature_value(
                entry,
                "filter_signature",
                "pattern" if entry.get("pattern") is not None else "filters",
            ),
        }
        encoded = json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _permission(entry: Mapping[str, Any]) -> str:
        value = entry.get("permission_level")
        if value is None:
            value = (
                "admin"
                if entry.get("is_admin") or entry.get("tag") == "admin"
                else "normal"
            )
        value = str(value)
        if value not in {"normal", "admin"}:
            raise ValueError(f"未知 permission_level: {value}")
        return value

    @staticmethod
    def _string_values(entry: Mapping[str, Any], field: str) -> list[str]:
        values = entry.get(field, [])
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError(f"{field} 必须是字符串列表")
        result = [str(value) for value in values]
        if any(not value.strip() for value in result):
            raise ValueError(f"{field} 不能包含空字符串")
        return result

    def sync_all_runtime(
        self, entries: Sequence[object], *, active_plugins: Collection[str]
    ) -> dict[str, int]:
        """以传入快照为准同步全部运行时目录。"""
        plugins = {str(plugin).strip() for plugin in active_plugins}
        if any(not plugin for plugin in plugins):
            raise ValueError("active_plugins 不能包含空插件名")
        normalized = [self._entry_dict(entry) for entry in entries]
        entry_plugins = {
            str(entry.get("plugin") or entry.get("source_plugin") or "")
            for entry in normalized
        }
        if not entry_plugins <= plugins:
            unknown = sorted(entry_plugins - plugins)
            raise ValueError(f"runtime 条目不在 active_plugins 快照中: {unknown}")
        with self.catalog._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                keys = self._sync_runtime(normalized, connection)
                if keys:
                    placeholders = ",".join("?" for _ in keys)
                    cursor = connection.execute(
                        f"DELETE FROM commands WHERE source_kind = 'runtime' "
                        f"AND runtime_key NOT IN ({placeholders})",
                        tuple(keys),
                    )
                else:
                    cursor = connection.execute(
                        "DELETE FROM commands WHERE source_kind = 'runtime'"
                    )
                if plugins:
                    placeholders = ",".join("?" for _ in plugins)
                    connection.execute(
                        f"UPDATE commands SET missing_plugin = 0, "
                        f"updated_at = CURRENT_TIMESTAMP "
                        f"WHERE source_kind = 'custom' "
                        f"AND source_plugin IN ({placeholders})",
                        tuple(sorted(plugins)),
                    )
                    connection.execute(
                        f"UPDATE commands SET missing_plugin = 1, "
                        f"updated_at = CURRENT_TIMESTAMP "
                        f"WHERE source_kind = 'custom' "
                        f"AND source_plugin IS NOT NULL "
                        f"AND source_plugin NOT IN ({placeholders})",
                        tuple(sorted(plugins)),
                    )
                else:
                    connection.execute(
                        "UPDATE commands SET missing_plugin = 1, "
                        "updated_at = CURRENT_TIMESTAMP "
                        "WHERE source_kind = 'custom' "
                        "AND source_plugin IS NOT NULL"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"upserted": len(keys), "removed": int(cursor.rowcount)}

    def sync_plugin_runtime(
        self, plugin: str, entries: Sequence[object]
    ) -> dict[str, int]:
        """同步单个插件目录，并恢复显式关联的自定义条目。"""
        if not plugin.strip():
            raise ValueError("plugin 不能为空")
        normalized = [self._entry_dict(entry) for entry in entries]
        if any(
            str(entry.get("plugin") or entry.get("source_plugin") or "") != plugin
            for entry in normalized
        ):
            raise ValueError("单插件同步包含其他插件的命令")
        with self.catalog._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                keys = self._sync_runtime(normalized, connection)
                if keys:
                    placeholders = ",".join("?" for _ in keys)
                    removed = connection.execute(
                        f"DELETE FROM commands WHERE source_kind = 'runtime' "
                        f"AND source_plugin = ? "
                        f"AND runtime_key NOT IN ({placeholders})",
                        (plugin, *keys),
                    ).rowcount
                else:
                    removed = connection.execute(
                        "DELETE FROM commands WHERE source_kind = 'runtime' "
                        "AND source_plugin = ?",
                        (plugin,),
                    ).rowcount
                restored = connection.execute(
                    "UPDATE commands SET missing_plugin = 0, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE source_kind = 'custom' AND source_plugin = ? "
                    "AND missing_plugin = 1",
                    (plugin,),
                ).rowcount
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "upserted": len(keys),
            "removed": int(removed),
            "custom_restored": int(restored),
        }

    def on_plugin_unloaded(self, plugin: str) -> dict[str, int]:
        """卸载仅删除 runtime，自定义条目保留并标记关联缺失。"""
        if not plugin.strip():
            raise ValueError("plugin 不能为空")
        with self.catalog._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            removed = connection.execute(
                "DELETE FROM commands WHERE source_kind = 'runtime' "
                "AND source_plugin = ?",
                (plugin,),
            ).rowcount
            marked = connection.execute(
                "UPDATE commands SET missing_plugin = 1, updated_at = CURRENT_TIMESTAMP "
                "WHERE source_kind = 'custom' AND source_plugin = ? "
                "AND missing_plugin = 0",
                (plugin,),
            ).rowcount
            connection.commit()
        return {
            "runtime_removed": int(removed),
            "custom_marked_missing": int(marked),
        }

    def update_command_policy(
        self,
        command_id: int,
        *,
        permission_level: str | None = None,
        delegation_policy: str | None = None,
        history_mode: str | None = None,
    ) -> dict[str, object]:
        """更新人工策略，并明确拒绝不安全的字段组合。"""
        allowed_permissions = {"normal", "admin"}
        allowed_delegations = {"normal", "sensitive", "forbidden"}
        allowed_history = {"none", "command", "full"}
        if permission_level is not None and permission_level not in allowed_permissions:
            raise ValueError(f"未知 permission_level: {permission_level}")
        if (
            delegation_policy is not None
            and delegation_policy not in allowed_delegations
        ):
            raise ValueError(f"未知 delegation_policy: {delegation_policy}")
        if history_mode is not None and history_mode not in allowed_history:
            raise ValueError(f"未知 history_mode: {history_mode}")
        with self.catalog._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM commands WHERE id = ?", (command_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(command_id)
            final_permission = permission_level or str(row["permission_level"])
            final_delegation = delegation_policy or str(row["delegation_policy"])
            final_history = history_mode or str(row["history_mode"])
            # 管理员命令至少是 sensitive；显式 forbidden 仍然更严格。
            if final_permission == "admin" and final_delegation == "normal":
                if delegation_policy == "normal":
                    connection.rollback()
                    raise ValueError("admin command delegation_policy 至少为 sensitive")
                final_delegation = "sensitive"
            if (
                final_delegation in {"sensitive", "forbidden"}
                and final_history == "full"
            ):
                connection.rollback()
                raise ValueError(
                    "sensitive/forbidden command cannot use history_mode=full"
                )
            connection.execute(
                """
                UPDATE commands SET
                    permission_level = ?, delegation_policy = ?, history_mode = ?,
                    permission_manual = CASE WHEN ? IS NULL THEN permission_manual ELSE 1 END,
                    delegation_manual = CASE WHEN ? IS NULL THEN delegation_manual ELSE 1 END,
                    history_manual = CASE WHEN ? IS NULL THEN history_manual ELSE 1 END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    final_permission,
                    final_delegation,
                    final_history,
                    permission_level,
                    delegation_policy,
                    history_mode,
                    command_id,
                ),
            )
            connection.commit()
        return {
            "id": command_id,
            "permission_level": final_permission,
            "delegation_policy": final_delegation,
            "history_mode": final_history,
        }

    def _sync_runtime(
        self,
        entries: Sequence[Mapping[str, Any]],
        connection: sqlite3.Connection,
    ) -> list[str]:
        keys: list[str] = []
        for entry in entries:
            plugin = str(entry.get("plugin") or entry.get("source_plugin") or "")
            command = str(entry.get("command") or entry.get("pattern") or "").strip()
            if not plugin or not command:
                raise ValueError("运行时命令必须包含 plugin 和 command/pattern")
            runtime_key = self._runtime_key(entry)
            keys.append(runtime_key)
            permission = self._permission(entry)
            delegation = "sensitive" if permission == "admin" else "normal"
            connection.execute(
                """
                    INSERT INTO commands(
                        source_kind, source_plugin, command_key, runtime_key,
                        handler_identity, filter_signature, entry_type,
                        description, permission_level, delegation_policy,
                        history_mode, hidden, missing_plugin
                    ) VALUES ('runtime', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'command', ?, 0)
                    ON CONFLICT(runtime_key) WHERE runtime_key IS NOT NULL DO UPDATE SET
                        source_plugin = excluded.source_plugin,
                        command_key = excluded.command_key,
                        handler_identity = excluded.handler_identity,
                        filter_signature = excluded.filter_signature,
                        entry_type = excluded.entry_type,
                        description = excluded.description,
                        hidden = excluded.hidden,
                        missing_plugin = 0,
                        permission_level = CASE
                            WHEN commands.permission_level = 'admin' THEN 'admin'
                            ELSE excluded.permission_level END,
                        delegation_policy = CASE
                            WHEN excluded.permission_level = 'admin'
                                 AND commands.delegation_policy = 'normal' THEN 'sensitive'
                            WHEN commands.delegation_manual = 1 THEN commands.delegation_policy
                            ELSE commands.delegation_policy END,
                        history_mode = CASE
                            WHEN excluded.permission_level = 'admin'
                                 AND commands.history_mode = 'full' THEN 'command'
                            ELSE commands.history_mode END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                (
                    plugin,
                    command,
                    runtime_key,
                    self._signature_text(entry, "handler_identity", "handler_name"),
                    self._signature_text(
                        entry,
                        "filter_signature",
                        "pattern" if entry.get("pattern") is not None else "filters",
                    ),
                    str(entry.get("type") or "command"),
                    str(entry.get("description") or ""),
                    permission,
                    delegation,
                    int(bool(entry.get("hidden", False))),
                ),
            )
            command_id = int(
                connection.execute(
                    "SELECT id FROM commands WHERE runtime_key = ?", (runtime_key,)
                ).fetchone()[0]
            )
            for table, column, field in (
                ("command_aliases", "alias", "aliases"),
                ("command_examples", "example", "examples"),
                ("command_subcommands", "subcommand", "sub_commands"),
            ):
                values = self._string_values(entry, field)
                connection.execute(
                    f"DELETE FROM {table} WHERE command_id = ?", (command_id,)
                )
                connection.executemany(
                    f"INSERT INTO {table}(command_id, {column}, position) "
                    f"VALUES (?, ?, ?)",
                    (
                        (command_id, value, position)
                        for position, value in enumerate(values)
                    ),
                )
        return keys

    def list_commands(
        self,
        *,
        page: int,
        page_size: int,
        filter: Mapping[str, object] | str | None = None,
    ) -> dict[str, object]:
        """分页读取目录；结果可直接 JSON 序列化。"""
        if page < 1 or page_size < 1:
            raise ValueError("page 和 page_size 必须为正整数")
        clauses: list[str] = []
        parameters: list[object] = []
        if isinstance(filter, str) and filter:
            clauses.append("(command_key LIKE ? OR description LIKE ?)")
            parameters.extend((f"%{filter}%", f"%{filter}%"))
        elif isinstance(filter, Mapping):
            for field, column in (
                ("source_type", "source_kind"),
                ("plugin", "source_plugin"),
                ("permission_level", "permission_level"),
                ("delegation_policy", "delegation_policy"),
            ):
                if field in filter:
                    clauses.append(f"{column} = ?")
                    parameters.append(filter[field])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.catalog._connect() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM commands" + where, tuple(parameters)
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT * FROM commands"
                + where
                + " ORDER BY source_kind, source_plugin, command_key, id LIMIT ? OFFSET ?",
                (*parameters, page_size, (page - 1) * page_size),
            ).fetchall()
            related: dict[int, dict[str, list[str]]] = {}
            for row in rows:
                command_id = int(row["id"])
                related[command_id] = {}
                for field, table, column in (
                    ("aliases", "command_aliases", "alias"),
                    ("examples", "command_examples", "example"),
                    ("sub_commands", "command_subcommands", "subcommand"),
                ):
                    value_rows = connection.execute(
                        f"SELECT {column} FROM {table} WHERE command_id = ? "
                        "ORDER BY position, rowid",
                        (command_id,),
                    ).fetchall()
                    related[command_id][field] = [str(value[0]) for value in value_rows]
        items = [
            {
                "id": int(row["id"]),
                "source_type": str(row["source_kind"]),
                "plugin": row["source_plugin"],
                "command": str(row["command_key"]),
                "type": str(row["entry_type"]),
                "description": str(row["description"]),
                "permission_level": str(row["permission_level"]),
                "delegation_policy": str(row["delegation_policy"]),
                "history_mode": str(row["history_mode"]),
                "availability": "missing_plugin"
                if row["missing_plugin"]
                else "available",
                "aliases": related[int(row["id"])]["aliases"],
                "examples": related[int(row["id"])]["examples"],
                "sub_commands": related[int(row["id"])]["sub_commands"],
            }
            for row in rows
        ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}
