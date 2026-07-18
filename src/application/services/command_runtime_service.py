"""AI 命令目录、身份、回执与历史的运行时编排。"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.infrastructure.storage import CommandCatalog

from .command_catalog_service import CommandCatalogService
from .command_history_service import CommandHistoryService
from .execution_receipt_service import ExecutionReceiptService
from .identity_service import IdentityService


class CommandRuntimeService:
    """把四类 SQLite 服务组合成插件生命周期可消费的稳定入口。"""

    def __init__(
        self,
        *,
        data_dir: Path,
        config: Any,
        context: Any,
        command_index: Any,
        command_executor: Any,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.config = config
        self.context = context
        self.command_index = command_index
        self.command_executor = command_executor
        self.catalog = CommandCatalog(self.data_dir / "command_catalog.db")
        self.catalog_service = CommandCatalogService(self.catalog)
        retention_days = int(config.command_history_retention_days)
        self.identity_service = IdentityService(
            self.catalog, retention_days=retention_days
        )
        self.history_service = CommandHistoryService(
            self.catalog, retention_days=retention_days
        )
        self.receipt_service = ExecutionReceiptService(
            self.catalog,
            dedupe_seconds=float(config.ai_command_dedupe_window_seconds),
        )
        self.initialized = False

    def initialize(self) -> dict[str, object]:
        """建库、迁移及清理必须全部成功后才发布 initialized。"""
        self.initialized = False
        report = self.catalog.initialize()
        legacy_report = None
        # v2 新布局与历史 data/ 布局都可读取；只导入第一个存在的权威文件。
        for path in (
            self.data_dir / "custom_groups.json",
            self.data_dir / "data" / "custom_groups.json",
        ):
            if path.is_file():
                legacy_report = self.catalog.import_legacy_custom_groups(path)
                break
        identities_removed = self.identity_service.cleanup_observed_identities()
        histories_removed = self.history_service.cleanup_expired()
        self.initialized = True
        return {
            "schema_version": report.schema_version,
            "legacy_import": legacy_report.to_dict() if legacy_report else None,
            "identities_removed": identities_removed,
            "histories_removed": histories_removed,
        }

    def terminate(self) -> None:
        """SQLite 按操作短连接，无常驻连接；终止时撤销可用状态。"""
        self.initialized = False

    def reconfigure(self, config: Any) -> None:
        """热更新所有已确认的运行时窗口，避免服务继续使用旧配置。"""
        from datetime import timedelta

        self.config = config
        retention_days = int(config.command_history_retention_days)
        dedupe_seconds = float(config.ai_command_dedupe_window_seconds)
        self.receipt_service.dedupe_window = timedelta(seconds=dedupe_seconds)
        self.history_service.retention = timedelta(days=retention_days)
        self.identity_service.retention_days = retention_days
        self.identity_service._resolver.retention_days = retention_days

    @staticmethod
    def _plugin_name(star: Any) -> str:
        return str(
            getattr(star, "name", "") or getattr(star, "root_dir_name", "") or ""
        ).strip()

    def _runtime_entries(self, plugin: str | None = None) -> list[dict[str, Any]]:
        """从 CommandIndex 快照提取目录数据，不把扫描细节塞入 main.py。"""
        if self.command_index is None:
            return []
        raw = self.command_index.get_all_commands() or {}
        entries: list[dict[str, Any]] = []
        for command_key, value in raw.items():
            row = dict(value)
            if row.get("inactive", False):
                continue
            source_plugin = str(row.get("plugin") or "")
            if not source_plugin.startswith("_custom_group_") and (
                plugin is None or source_plugin == plugin
            ):
                entries.append(
                    {
                        "plugin": source_plugin,
                        "command": row.get("command") or command_key,
                        "pattern": row.get("pattern"),
                        "type": row.get("type", "command"),
                        "description": row.get("description", ""),
                        "is_admin": row.get("tag") == "admin",
                        "hidden": row.get("hidden", False),
                        "aliases": row.get("aliases", []),
                        "examples": row.get("examples", []),
                        "sub_commands": row.get("sub_commands", []),
                        "handler_identity": row.get("handler_name", ""),
                        "filter_signature": row.get("pattern")
                        or row.get("command")
                        or command_key,
                    }
                )
        return entries

    def sync_all(self, active_stars: list[Any] | None = None) -> dict[str, int]:
        """以当前 activated 插件和 CommandIndex 为权威完整同步。"""
        stars = active_stars
        if stars is None:
            stars = list(self.context.get_all_stars()) if self.context else []
        plugins = {
            name
            for star in stars
            if getattr(star, "activated", True)
            if (name := self._plugin_name(star))
        }
        return self.catalog_service.sync_all_runtime(
            self._runtime_entries(), active_plugins=plugins
        )

    def sync_plugin(self, plugin_metadata: Any) -> dict[str, int]:
        """插件加载时只同步该插件，并恢复其自定义关联。"""
        plugin = self._plugin_name(plugin_metadata)
        if not plugin:
            raise ValueError("插件 metadata 缺少 name/root_dir_name")
        return self.catalog_service.sync_plugin_runtime(
            plugin, self._runtime_entries(plugin)
        )

    def unload_plugin(self, plugin_metadata: Any) -> dict[str, int]:
        """卸载只删除 runtime，并把关联 custom 标为 missing。"""
        plugin = self._plugin_name(plugin_metadata)
        if not plugin:
            raise ValueError("插件 metadata 缺少 name/root_dir_name")
        return self.catalog_service.on_plugin_unloaded(plugin)

    def cleanup(self) -> dict[str, int]:
        """在生命周期边界清理 90 天观察身份和明细历史。"""
        return {
            "identities_removed": self.identity_service.cleanup_observed_identities(),
            "histories_removed": self.history_service.cleanup_expired(),
        }

    def find_command(self, command_text: str) -> dict[str, Any] | None:
        """普通触发优先；regex 多匹配时按最严格安全策略合成。"""
        normalized = command_text.strip()
        while normalized.startswith("/"):
            normalized = normalized[1:]
        normalized = normalized.casefold()
        with self.catalog._connect() as connection:
            row = connection.execute(
                """
                SELECT DISTINCT commands.* FROM commands
                LEFT JOIN command_aliases ON command_aliases.command_id = commands.id
                WHERE missing_plugin = 0 AND entry_type = 'command' AND (
                    ? = lower(ltrim(command_key, '/'))
                    OR ? LIKE lower(ltrim(command_key, '/')) || ' %'
                    OR ? = lower(ltrim(command_aliases.alias, '/'))
                    OR ? LIKE lower(ltrim(command_aliases.alias, '/')) || ' %'
                )
                ORDER BY length(command_key) DESC,
                    CASE source_kind WHEN 'custom' THEN 0 ELSE 1 END, id
                LIMIT 1
                """,
                (normalized, normalized, normalized, normalized),
            ).fetchone()
            if row is not None:
                return dict(row)
            regex_rows = connection.execute(
                "SELECT * FROM commands WHERE missing_plugin = 0 "
                "AND entry_type = 'regex' ORDER BY id"
            ).fetchall()
        runtime_text = command_text.lower().strip()
        matches = []
        for regex_row in regex_rows:
            try:
                if re.search(
                    str(regex_row["command_key"]), runtime_text, re.IGNORECASE
                ):
                    matches.append(dict(regex_row))
            except re.error:
                # 与既有自定义 RegexFilter 辅助匹配一致：失效旧正则不参与匹配。
                continue
        if not matches:
            return None
        delegation_rank = {"normal": 0, "sensitive": 1, "forbidden": 2}
        permission_rank = {"normal": 0, "admin": 1}
        history_rank = {"full": 0, "command": 1, "none": 2}
        strict_history = max(matches, key=lambda row: history_rank[row["history_mode"]])
        combined = dict(strict_history)
        combined["delegation_policy"] = max(
            (row["delegation_policy"] for row in matches),
            key=delegation_rank.__getitem__,
        )
        combined["permission_level"] = max(
            (row["permission_level"] for row in matches),
            key=permission_rank.__getitem__,
        )
        combined["history_mode"] = strict_history["history_mode"]
        combined["matched_command_ids"] = [int(row["id"]) for row in matches]
        return combined
