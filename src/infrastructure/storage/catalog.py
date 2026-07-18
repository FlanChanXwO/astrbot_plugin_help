"""命令目录 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .legacy import (
    LegacyImportReport,
    build_legacy_import_plan,
    create_legacy_backup,
)


_INITIAL_SCHEMA = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE commands (
    id INTEGER PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('runtime', 'custom')),
    source_plugin TEXT,
    command_key TEXT NOT NULL,
    runtime_key TEXT,
    handler_identity TEXT NOT NULL DEFAULT '',
    filter_signature TEXT NOT NULL DEFAULT '',
    entry_type TEXT NOT NULL DEFAULT 'command'
        CHECK(entry_type IN ('command', 'regex')),
    description TEXT NOT NULL DEFAULT '',
    permission_level TEXT NOT NULL DEFAULT 'normal'
        CHECK(permission_level IN ('normal', 'admin')),
    delegation_policy TEXT NOT NULL DEFAULT 'normal'
        CHECK(delegation_policy IN ('normal', 'sensitive', 'forbidden')),
    history_mode TEXT NOT NULL DEFAULT 'command'
        CHECK(history_mode IN ('none', 'command', 'full')),
    hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0, 1)),
    missing_plugin INTEGER NOT NULL DEFAULT 0 CHECK(missing_plugin IN (0, 1)),
    permission_manual INTEGER NOT NULL DEFAULT 0 CHECK(permission_manual IN (0, 1)),
    delegation_manual INTEGER NOT NULL DEFAULT 0 CHECK(delegation_manual IN (0, 1)),
    history_manual INTEGER NOT NULL DEFAULT 0 CHECK(history_manual IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(NOT (
        delegation_policy IN ('sensitive', 'forbidden')
        AND history_mode = 'full'
    )),
    CHECK(permission_level != 'admin' OR delegation_policy != 'normal')
);

CREATE UNIQUE INDEX commands_runtime_key_idx
ON commands(runtime_key) WHERE runtime_key IS NOT NULL;

CREATE TABLE command_aliases (
    command_id INTEGER NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(command_id, alias)
);

CREATE TABLE command_examples (
    command_id INTEGER NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    example TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(command_id, example)
);

CREATE TABLE command_subcommands (
    command_id INTEGER NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    subcommand TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(command_id, subcommand)
);

CREATE TABLE custom_groups (
    id INTEGER PRIMARY KEY,
    group_name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0, 1))
);

CREATE TABLE custom_group_commands (
    group_id INTEGER NOT NULL REFERENCES custom_groups(id) ON DELETE CASCADE,
    command_id INTEGER NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(group_id, command_id)
);

CREATE TABLE user_privacy_settings (
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    privacy_mode TEXT NOT NULL DEFAULT 'allow'
        CHECK(privacy_mode IN ('allow', 'deny_sensitive', 'deny_all')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(platform, user_id)
);

CREATE TABLE session_participants (
    platform TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    identity_source TEXT NOT NULL DEFAULT 'observed'
        CHECK(identity_source IN ('observed', 'live')),
    PRIMARY KEY(platform, session_id, user_id)
);

CREATE INDEX session_participants_name_idx
ON session_participants(platform, session_id, normalized_name);

CREATE TABLE personal_aliases (
    platform TEXT NOT NULL,
    session_id TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    target_user_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(platform, session_id, requester_id, normalized_alias)
);

CREATE TABLE identity_references (
    target_ref TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, session_id, user_id)
);

CREATE TABLE execution_receipts (
    receipt_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    session_id TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    target_user_id TEXT NOT NULL,
    normalized_command TEXT NOT NULL,
    execution_state TEXT NOT NULL CHECK(execution_state IN (
        'reserved', 'completed', 'accepted', 'external_dispatched',
        'duplicate_suppressed', 'rejected', 'failed'
    )),
    dispatched INTEGER NOT NULL DEFAULT 0 CHECK(dispatched IN (0, 1)),
    output_complete INTEGER NOT NULL DEFAULT 0 CHECK(output_complete IN (0, 1)),
    retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0, 1)),
    target_json TEXT NOT NULL DEFAULT '{}',
    messages_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX execution_receipts_dedup_idx
ON execution_receipts(
    platform, session_id, requester_id, target_user_id,
    normalized_command, created_at
);

CREATE TABLE command_history (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    target_user_id TEXT NOT NULL,
    command_id INTEGER REFERENCES commands(id) ON DELETE SET NULL,
    command_key TEXT NOT NULL,
    command_text TEXT,
    execution_state TEXT NOT NULL CHECK(execution_state IN (
        'completed', 'accepted', 'external_dispatched'
    )),
    used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX command_history_retention_idx
ON command_history(used_at);

CREATE TABLE command_usage_aggregates (
    platform TEXT NOT NULL,
    target_user_id TEXT NOT NULL,
    command_key TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0 CHECK(use_count >= 0),
    last_used_at TEXT NOT NULL,
    PRIMARY KEY(platform, target_user_id, command_key)
);

CREATE TABLE legacy_imports (
    checksum TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    backup_path TEXT NOT NULL,
    group_count INTEGER NOT NULL,
    command_count INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class InitializationReport:
    """数据库初始化结果。"""

    schema_version: int


@dataclass(frozen=True)
class CatalogHealth:
    """供启动检查和迁移诊断使用的数据库状态。"""

    schema_version: int
    journal_mode: str
    foreign_keys_enabled: bool
    tables: frozenset[str]


@dataclass(frozen=True)
class CatalogCommand:
    """可持久化的命令目录条目。"""

    source_kind: str
    command_key: str
    source_plugin: str | None = None
    entry_type: str = "command"
    description: str = ""
    permission_level: str = "normal"
    delegation_policy: str = "normal"
    history_mode: str = "command"
    hidden: bool = False
    missing_plugin: bool = False
    id: int | None = None


class CommandCatalog:
    """隐藏连接和迁移细节的命令目录入口。"""

    CURRENT_SCHEMA_VERSION = 1

    def __init__(
        self, database_path: str | Path, *, lock_wait_seconds: float = 5.0
    ) -> None:
        self.database_path = Path(database_path)
        if not math.isfinite(lock_wait_seconds) or lock_wait_seconds < 0:
            raise ValueError("lock_wait_seconds 必须是非负有限数")
        self.lock_wait_seconds = lock_wait_seconds

    def _connect(self) -> sqlite3.Connection:
        # 5 秒是 sqlite3.connect 的平台默认锁等待；显式暴露后，部署方可按
        # 存储性能调整。它只等待真实数据库写锁，不是业务重试或任务超时。
        connection = sqlite3.connect(
            self.database_path,
            isolation_level=None,
            timeout=self.lock_wait_seconds,
        )
        connection.row_factory = sqlite3.Row
        lock_wait_milliseconds = round(self.lock_wait_seconds * 1000)
        connection.execute(f"PRAGMA busy_timeout = {lock_wait_milliseconds}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _execute_schema(connection: sqlite3.Connection, script: str) -> None:
        """逐条执行 schema，使 DDL 保持在调用方已经开启的事务内。"""
        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ""
        if statement.strip():
            raise RuntimeError("schema migration 包含不完整 SQL")

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        """启用并验证 WAL；竞争时由 SQLite 锁等待协调。"""
        try:
            mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).casefold():
                raise
            # journal_mode 切换本身可能立即返回 BUSY。BEGIN EXCLUSIVE 会使用
            # busy_timeout 确定性等待持锁者提交或异常退出；等待失败原样抛出。
            # 获锁后不再存在先前竞争者，再执行一次状态转换并严格验证结果。
            connection.execute("BEGIN EXCLUSIVE")
            connection.rollback()
            mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
        if mode.casefold() != "wal":
            raise RuntimeError(f"无法启用 SQLite WAL，当前模式为 {mode}")

    def initialize(self) -> InitializationReport:
        """创建数据库并在同一事务中登记初始 schema 版本。"""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            try:
                # 先取得 SQLite 写锁再读取版本，保证并发初始化不会同时判断 v0。
                connection.execute("BEGIN IMMEDIATE")
                current_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if current_version == 0:
                    # DDL 与版本登记原子完成，避免中断后留下“半个 schema”。
                    self._execute_schema(connection, _INITIAL_SCHEMA)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)",
                        (self.CURRENT_SCHEMA_VERSION,),
                    )
                    connection.execute(
                        f"PRAGMA user_version = {self.CURRENT_SCHEMA_VERSION}"
                    )
                    current_version = self.CURRENT_SCHEMA_VERSION
                elif current_version > self.CURRENT_SCHEMA_VERSION:
                    raise RuntimeError(
                        f"数据库 schema 版本 {current_version} 高于当前支持版本 "
                        f"{self.CURRENT_SCHEMA_VERSION}"
                    )
                connection.commit()
                self._enable_wal(connection)
            except Exception:
                connection.rollback()
                raise
        return InitializationReport(schema_version=current_version)

    def get_schema_version(self) -> int:
        """返回当前数据库的 schema 版本。"""
        with self._connect() as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def get_health(self) -> CatalogHealth:
        """读取 schema 覆盖及当前连接的 SQLite 安全设置。"""
        with self._connect() as connection:
            journal_mode = str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()
            foreign_keys_enabled = bool(
                connection.execute("PRAGMA foreign_keys").fetchone()[0]
            )
            tables = frozenset(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            )
        return CatalogHealth(
            schema_version=self.get_schema_version(),
            journal_mode=journal_mode,
            foreign_keys_enabled=foreign_keys_enabled,
            tables=tables,
        )

    def upsert_session_participant(
        self,
        *,
        platform_id: str,
        session_id: str,
        user_id: str,
        display_name: str,
        normalized_name: str,
        source: str,
        seen_at: datetime,
    ) -> None:
        """新增或刷新会话参与者，不接触消息正文。"""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO session_participants(
                    platform, session_id, user_id, display_name,
                    normalized_name, last_seen_at, active, identity_source
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(platform, session_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    normalized_name = excluded.normalized_name,
                    last_seen_at = excluded.last_seen_at,
                    active = 1,
                    identity_source = excluded.identity_source
                """,
                (
                    platform_id,
                    session_id,
                    user_id,
                    display_name,
                    normalized_name,
                    seen_at.isoformat(),
                    source,
                ),
            )

    def list_session_participants(
        self,
        *,
        platform_id: str,
        session_id: str,
        active_only: bool = True,
        newer_than: datetime | None = None,
    ) -> list[sqlite3.Row]:
        """按稳定 UID 顺序读取会话身份快照。"""
        active_clause = " AND active = 1" if active_only else ""
        freshness_clause = " AND last_seen_at >= ?" if newer_than else ""
        parameters: tuple[object, ...] = (platform_id, session_id)
        if newer_than is not None:
            parameters += (newer_than.isoformat(),)
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM session_participants
                WHERE platform = ? AND session_id = ?
                """
                + active_clause
                + freshness_clause
                + " ORDER BY user_id",
                parameters,
            ).fetchall()

    def get_or_create_identity_reference(
        self,
        *,
        platform_id: str,
        session_id: str,
        user_id: str,
        candidate_ref: str,
    ) -> str:
        """取得会话限定的随机引用；并发创建由唯一约束收敛。"""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT target_ref FROM identity_references
                WHERE platform = ? AND session_id = ? AND user_id = ?
                """,
                (platform_id, session_id, user_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO identity_references(
                        target_ref, platform, session_id, user_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (candidate_ref, platform_id, session_id, user_id),
                )
                connection.commit()
                return candidate_ref
            connection.commit()
            return str(row["target_ref"])

    def find_identity_reference(
        self, *, platform_id: str, session_id: str, target_ref: str
    ) -> str | None:
        """仅在引用所属会话内解析随机引用。"""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id FROM identity_references
                WHERE platform = ? AND session_id = ? AND target_ref = ?
                """,
                (platform_id, session_id, target_ref),
            ).fetchone()
        return None if row is None else str(row["user_id"])

    def get_privacy_mode(self, *, platform_id: str, user_id: str) -> str:
        """读取用户隐私模式；不存在的用户使用 schema 的 allow 默认语义。"""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT privacy_mode FROM user_privacy_settings
                WHERE platform = ? AND user_id = ?
                """,
                (platform_id, user_id),
            ).fetchone()
        return "allow" if row is None else str(row["privacy_mode"])

    def set_privacy_mode(
        self, *, platform_id: str, user_id: str, privacy_mode: str
    ) -> None:
        """原子写入用户隐私模式。"""
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO user_privacy_settings(platform, user_id, privacy_mode)
                    VALUES (?, ?, ?)
                    ON CONFLICT(platform, user_id) DO UPDATE SET
                        privacy_mode = excluded.privacy_mode,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (platform_id, user_id, privacy_mode),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"隐私设置违反目录约束: {error}") from error

    def get_session_participant(
        self,
        *,
        platform_id: str,
        session_id: str,
        user_id: str,
        active_only: bool = True,
        newer_than: datetime | None = None,
    ) -> sqlite3.Row | None:
        """按 UID 读取一个会话参与者。"""
        active_clause = " AND active = 1" if active_only else ""
        freshness_clause = " AND last_seen_at >= ?" if newer_than else ""
        parameters: tuple[object, ...] = (platform_id, session_id, user_id)
        if newer_than is not None:
            parameters += (newer_than.isoformat(),)
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM session_participants
                WHERE platform = ? AND session_id = ? AND user_id = ?
                """
                + active_clause
                + freshness_clause,
                parameters,
            ).fetchone()

    def get_personal_alias_target(
        self,
        *,
        platform_id: str,
        session_id: str,
        requester_id: str,
        normalized_alias: str,
    ) -> str | None:
        """读取请求者在当前会话的个人别名目标。"""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT target_user_id FROM personal_aliases
                WHERE platform = ? AND session_id = ?
                  AND requester_id = ? AND normalized_alias = ?
                """,
                (platform_id, session_id, requester_id, normalized_alias),
            ).fetchone()
        return None if row is None else str(row["target_user_id"])

    def save_personal_alias(
        self,
        *,
        platform_id: str,
        session_id: str,
        requester_id: str,
        alias: str,
        normalized_alias: str,
        target_user_id: str,
    ) -> None:
        """保存或替换请求者的会话内个人别名。"""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO personal_aliases(
                    platform, session_id, requester_id, alias,
                    normalized_alias, target_user_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, session_id, requester_id, normalized_alias)
                DO UPDATE SET
                    alias = excluded.alias,
                    target_user_id = excluded.target_user_id,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    platform_id,
                    session_id,
                    requester_id,
                    alias,
                    normalized_alias,
                    target_user_id,
                ),
            )

    def list_personal_aliases(
        self, *, platform_id: str, session_id: str, requester_id: str
    ) -> list[sqlite3.Row]:
        """列出请求者的会话内个人别名及当前目标状态。"""
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT personal_aliases.*, session_participants.display_name,
                       session_participants.active,
                       session_participants.last_seen_at
                FROM personal_aliases
                LEFT JOIN session_participants ON
                    session_participants.platform = personal_aliases.platform
                    AND session_participants.session_id = personal_aliases.session_id
                    AND session_participants.user_id = personal_aliases.target_user_id
                WHERE personal_aliases.platform = ?
                  AND personal_aliases.session_id = ?
                  AND personal_aliases.requester_id = ?
                ORDER BY personal_aliases.normalized_alias
                """,
                (platform_id, session_id, requester_id),
            ).fetchall()

    def delete_personal_alias(
        self,
        *,
        platform_id: str,
        session_id: str,
        requester_id: str,
        normalized_alias: str | None = None,
    ) -> int:
        """删除一个别名；normalized_alias 为空时清空请求者当前会话别名。"""
        with self._connect() as connection:
            if normalized_alias is None:
                cursor = connection.execute(
                    """
                    DELETE FROM personal_aliases
                    WHERE platform = ? AND session_id = ? AND requester_id = ?
                    """,
                    (platform_id, session_id, requester_id),
                )
            else:
                cursor = connection.execute(
                    """
                    DELETE FROM personal_aliases
                    WHERE platform = ? AND session_id = ? AND requester_id = ?
                      AND normalized_alias = ?
                    """,
                    (platform_id, session_id, requester_id, normalized_alias),
                )
        return int(cursor.rowcount)

    def replace_live_roster(
        self,
        *,
        platform_id: str,
        session_id: str,
        participants: list[tuple[str, str, str]],
        seen_at: datetime,
    ) -> None:
        """用一次成功的实时成员表原子替换会话 active 状态。"""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE session_participants SET active = 0
                WHERE platform = ? AND session_id = ?
                """,
                (platform_id, session_id),
            )
            connection.executemany(
                """
                INSERT INTO session_participants(
                    platform, session_id, user_id, display_name,
                    normalized_name, last_seen_at, active, identity_source
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'live')
                ON CONFLICT(platform, session_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    normalized_name = excluded.normalized_name,
                    last_seen_at = excluded.last_seen_at,
                    active = 1,
                    identity_source = 'live'
                """,
                (
                    (
                        platform_id,
                        session_id,
                        user_id,
                        display_name,
                        normalized_name,
                        seen_at.isoformat(),
                    )
                    for user_id, display_name, normalized_name in participants
                ),
            )
            connection.commit()

    def cleanup_session_participants(self, *, older_than: datetime) -> int:
        """清理过期观察身份及其可调用 ref，显式 personal_aliases 不删除。"""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired = connection.execute(
                """
                SELECT platform, session_id, user_id
                FROM session_participants WHERE last_seen_at < ?
                """,
                (older_than.isoformat(),),
            ).fetchall()
            connection.executemany(
                """
                DELETE FROM identity_references
                WHERE platform = ? AND session_id = ? AND user_id = ?
                """,
                (
                    (row["platform"], row["session_id"], row["user_id"])
                    for row in expired
                ),
            )
            connection.execute(
                "DELETE FROM session_participants WHERE last_seen_at < ?",
                (older_than.isoformat(),),
            )
            connection.commit()
        return len(expired)

    def save_command(self, command: CatalogCommand) -> int:
        """保存一个命令条目并返回其数据库 ID。"""
        if (
            command.permission_level == "admin"
            and command.delegation_policy == "normal"
        ):
            raise ValueError("admin command delegation_policy 至少为 sensitive")
        if (
            command.delegation_policy in {"sensitive", "forbidden"}
            and command.history_mode == "full"
        ):
            # 双层约束让调用者先收到清晰错误，同时由 SQLite 防止绕过。
            raise ValueError("sensitive/forbidden command cannot use history_mode=full")
        if not command.command_key.strip():
            raise ValueError("command_key 不能为空")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO commands(
                        source_kind, source_plugin, command_key, entry_type,
                        description, permission_level, delegation_policy,
                        history_mode, hidden, missing_plugin
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        command.source_kind,
                        command.source_plugin,
                        command.command_key,
                        command.entry_type,
                        command.description,
                        command.permission_level,
                        command.delegation_policy,
                        command.history_mode,
                        int(command.hidden),
                        int(command.missing_plugin),
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            raise ValueError(f"命令字段违反目录约束: {error}") from error

    def get_command(self, command_id: int) -> CatalogCommand:
        """按 ID 读取命令条目。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM commands WHERE id = ?", (command_id,)
            ).fetchone()
        if row is None:
            raise KeyError(command_id)
        return CatalogCommand(
            id=int(row["id"]),
            source_kind=str(row["source_kind"]),
            source_plugin=row["source_plugin"],
            command_key=str(row["command_key"]),
            entry_type=str(row["entry_type"]),
            description=str(row["description"]),
            permission_level=str(row["permission_level"]),
            delegation_policy=str(row["delegation_policy"]),
            history_mode=str(row["history_mode"]),
            hidden=bool(row["hidden"]),
            missing_plugin=bool(row["missing_plugin"]),
        )

    def import_legacy_custom_groups(
        self, source_path: str | Path, *, dry_run: bool = False
    ) -> LegacyImportReport:
        """严格验证并事务导入旧自定义分组。"""
        if dry_run:
            plan = build_legacy_import_plan(source_path)
            return LegacyImportReport(
                status="validated",
                source_path=plan.source_path,
                checksum=plan.checksum,
                group_count=len(plan.groups),
                command_count=plan.command_count,
                dry_run=True,
            )

        self.initialize()
        backup_path: Path | None = None
        with self._connect() as connection:
            try:
                # legacy JSON 只是一份一次性历史输入。取得写锁后先判断是否曾
                # 完成过任意导入，避免源文件后来变化又被追加到权威数据库。
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT source_path, checksum, backup_path,
                           group_count, command_count
                    FROM legacy_imports ORDER BY imported_at, rowid LIMIT 1
                    """
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return LegacyImportReport(
                        status="already_migrated",
                        source_path=Path(existing["source_path"]),
                        checksum=str(existing["checksum"]),
                        group_count=int(existing["group_count"]),
                        command_count=int(existing["command_count"]),
                        backup_path=Path(existing["backup_path"]),
                    )

                # 只有确认数据库从未导入后才解析和备份当前源文件。
                plan = build_legacy_import_plan(source_path)
                backup_path = create_legacy_backup(plan)
                for group in plan.groups:
                    group_cursor = connection.execute(
                        """
                        INSERT INTO custom_groups(
                            group_name, description, priority, hidden
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            group.group_name,
                            group.description,
                            group.priority,
                            int(group.hidden),
                        ),
                    )
                    group_id = int(group_cursor.lastrowid)
                    for position, command in enumerate(group.commands):
                        command_cursor = connection.execute(
                            """
                            INSERT INTO commands(
                                source_kind, command_key, entry_type, description,
                                permission_level, delegation_policy,
                                history_mode, hidden
                            ) VALUES ('custom', ?, ?, ?, ?, ?, 'command', ?)
                            """,
                            (
                                command.command_key,
                                command.entry_type,
                                command.description,
                                command.permission_level,
                                command.delegation_policy,
                                int(command.hidden),
                            ),
                        )
                        command_id = int(command_cursor.lastrowid)
                        connection.execute(
                            """
                            INSERT INTO custom_group_commands(
                                group_id, command_id, position
                            ) VALUES (?, ?, ?)
                            """,
                            (group_id, command_id, position),
                        )
                        for table, column, values in (
                            ("command_aliases", "alias", command.aliases),
                            ("command_examples", "example", command.examples),
                            (
                                "command_subcommands",
                                "subcommand",
                                command.subcommands,
                            ),
                        ):
                            connection.executemany(
                                f"INSERT INTO {table}(command_id, {column}, position) "
                                f"VALUES (?, ?, ?)",
                                (
                                    (command_id, value, value_position)
                                    for value_position, value in enumerate(values)
                                ),
                            )
                connection.execute(
                    """
                    INSERT INTO legacy_imports(
                        source_path, checksum, backup_path,
                        group_count, command_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(plan.source_path),
                        plan.checksum,
                        str(backup_path),
                        len(plan.groups),
                        plan.command_count,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return LegacyImportReport(
            status="imported",
            source_path=plan.source_path,
            checksum=plan.checksum,
            group_count=len(plan.groups),
            command_count=plan.command_count,
            backup_path=backup_path,
        )

    @staticmethod
    def _custom_entry_values(entry: dict[str, object]) -> dict[str, object]:
        """严格归一化一个自定义条目，供所有仓储写入口复用。"""
        entry_type = entry.get("type", "command")
        if entry_type not in {"command", "regex"}:
            raise ValueError("type 必须为 command 或 regex")
        primary_field = "pattern" if entry_type == "regex" else "command"
        primary = entry.get(primary_field, "")
        if not isinstance(primary, str):
            raise ValueError(f"{primary_field} 必须是字符串")

        def string_list(field: str, *, nonblank: bool = False) -> list[str]:
            value = entry.get(field, [])
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError(f"{field} 必须是字符串列表")
            if nonblank and any(not item.strip() for item in value):
                raise ValueError(f"{field} 不能包含空触发式")
            return list(value)

        aliases = string_list("aliases", nonblank=True)
        if entry_type == "regex":
            if primary == "":
                raise ValueError("pattern 必须是非空字符串")
            command_key = primary
        else:
            command_key = primary.strip()
            if not command_key and not aliases:
                raise ValueError("普通 command 与 aliases 不能同时为空")
        permission = entry.get("permission_level")
        legacy_admin = entry.get("is_admin")
        if permission is None:
            if type(legacy_admin) is not bool:
                legacy_admin = False
            permission = "admin" if legacy_admin else "normal"
        if permission not in {"normal", "admin"}:
            raise ValueError("permission_level 必须为 normal 或 admin")
        if legacy_admin is not None and type(legacy_admin) is not bool:
            raise ValueError("is_admin 必须是布尔值")
        if legacy_admin is not None and bool(legacy_admin) != (permission == "admin"):
            raise ValueError("is_admin 与 permission_level 不一致")
        delegation = entry.get("delegation_policy")
        if delegation is None:
            delegation = "sensitive" if permission == "admin" else "normal"
        if delegation not in {"normal", "sensitive", "forbidden"}:
            raise ValueError("delegation_policy 值无效")
        if permission == "admin" and delegation == "normal":
            raise ValueError("admin command delegation_policy 至少为 sensitive")
        history = entry.get("history_mode", "command")
        if history not in {"none", "command", "full"}:
            raise ValueError("history_mode 值无效")
        if delegation in {"sensitive", "forbidden"} and history == "full":
            raise ValueError("sensitive/forbidden command cannot use history_mode=full")

        linked_plugin = entry.get("linked_plugin")
        if linked_plugin is not None and (
            not isinstance(linked_plugin, str) or not linked_plugin.strip()
        ):
            raise ValueError("linked_plugin 必须是非空字符串或 null")
        availability = entry.get("availability", "available")
        if availability not in {"available", "missing_plugin"}:
            raise ValueError("availability 值无效")
        description = entry.get("description", "")
        if not isinstance(description, str):
            raise ValueError("description 必须是字符串")
        hidden = entry.get("hidden", False)
        if type(hidden) is not bool:
            raise ValueError("hidden 必须是布尔值")
        return {
            "entry_type": entry_type,
            "command_key": command_key,
            "description": description,
            "permission_level": permission,
            "delegation_policy": delegation,
            "history_mode": history,
            "hidden": hidden,
            "source_plugin": linked_plugin.strip() if linked_plugin else None,
            "missing_plugin": availability == "missing_plugin",
            "aliases": aliases,
            "examples": string_list("examples"),
            "sub_commands": string_list("sub_commands"),
        }

    def _insert_custom_entry(
        self,
        connection: sqlite3.Connection,
        *,
        group_id: int,
        entry: dict[str, object],
        position: int,
    ) -> int:
        values = self._custom_entry_values(entry)
        cursor = connection.execute(
            """
            INSERT INTO commands(
                source_kind, source_plugin, command_key, entry_type,
                description, permission_level, delegation_policy,
                history_mode, hidden, missing_plugin
            ) VALUES ('custom', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["source_plugin"],
                values["command_key"],
                values["entry_type"],
                values["description"],
                values["permission_level"],
                values["delegation_policy"],
                values["history_mode"],
                int(values["hidden"]),
                int(values["missing_plugin"]),
            ),
        )
        command_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO custom_group_commands(group_id, command_id, position) "
            "VALUES (?, ?, ?)",
            (group_id, command_id, position),
        )
        for table, column, field in (
            ("command_aliases", "alias", "aliases"),
            ("command_examples", "example", "examples"),
            ("command_subcommands", "subcommand", "sub_commands"),
        ):
            connection.executemany(
                f"INSERT INTO {table}(command_id, {column}, position) VALUES (?, ?, ?)",
                (
                    (command_id, value, item_position)
                    for item_position, value in enumerate(values[field])
                ),
            )
        return command_id

    @staticmethod
    def _custom_group_values(group: dict[str, object]) -> tuple[str, str, int, bool]:
        name = group.get("group_name")
        description = group.get("description", "")
        priority = group.get("priority", 0)
        hidden = group.get("hidden", False)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("group_name 必须是非空字符串")
        if not isinstance(description, str):
            raise ValueError("description 必须是字符串")
        if type(priority) is not int:
            raise ValueError("priority 必须是整数")
        if type(hidden) is not bool:
            raise ValueError("hidden 必须是布尔值")
        commands = group.get("commands", [])
        if not isinstance(commands, list) or any(
            not isinstance(command, dict) for command in commands
        ):
            raise ValueError("commands 必须是对象列表")
        return name.strip(), description, priority, hidden

    def _insert_custom_group(
        self, connection: sqlite3.Connection, group: dict[str, object]
    ) -> int:
        name, description, priority, hidden = self._custom_group_values(group)
        cursor = connection.execute(
            "INSERT INTO custom_groups(group_name, description, priority, hidden) "
            "VALUES (?, ?, ?, ?)",
            (name, description, priority, int(hidden)),
        )
        group_id = int(cursor.lastrowid)
        for position, entry in enumerate(group.get("commands", [])):
            self._insert_custom_entry(
                connection, group_id=group_id, entry=entry, position=position
            )
        return group_id

    @staticmethod
    def _group_command_ids(
        connection: sqlite3.Connection, group_names: list[str] | None = None
    ) -> list[int]:
        where = ""
        parameters: tuple[object, ...] = ()
        if group_names is not None:
            if not group_names:
                return []
            placeholders = ",".join("?" for _ in group_names)
            where = f" WHERE cg.group_name IN ({placeholders})"
            parameters = tuple(group_names)
        rows = connection.execute(
            """
            SELECT DISTINCT cgc.command_id
            FROM custom_group_commands cgc
            JOIN custom_groups cg ON cg.id = cgc.group_id
            """
            + where,
            parameters,
        ).fetchall()
        return [int(row[0]) for row in rows]

    @staticmethod
    def _delete_unreferenced_custom_commands(
        connection: sqlite3.Connection, command_ids: list[int]
    ) -> int:
        """只删除候选集合中已失去全部分组引用的 custom 命令。"""
        if not command_ids:
            return 0
        placeholders = ",".join("?" for _ in command_ids)
        cursor = connection.execute(
            f"""
            DELETE FROM commands
            WHERE source_kind = 'custom' AND id IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1 FROM custom_group_commands cgc
                  WHERE cgc.command_id = commands.id
              )
            """,
            tuple(command_ids),
        )
        return int(cursor.rowcount)

    def create_custom_group(self, group: dict[str, object]) -> None:
        """原子创建一个完整自定义组。"""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._insert_custom_group(connection, group)
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError(f"自定义目录违反数据库约束: {error}") from error

    def replace_custom_group(
        self, current_group_name: str, group: dict[str, object]
    ) -> None:
        """在一个事务中删除旧组并写入完整替换。"""
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                old_command_ids = self._group_command_ids(
                    connection, [current_group_name]
                )
                cursor = connection.execute(
                    "DELETE FROM custom_groups WHERE group_name = ?",
                    (current_group_name,),
                )
                if cursor.rowcount != 1:
                    raise KeyError(current_group_name)
                self._insert_custom_group(connection, group)
                self._delete_unreferenced_custom_commands(connection, old_command_ids)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def replace_all_custom_groups(self, groups: list[dict[str, object]]) -> None:
        """原子替换全部自定义目录，供应用服务一次提交候选快照。"""
        if not isinstance(groups, list):
            raise ValueError("groups 必须是对象列表")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                old_command_ids = self._group_command_ids(connection)
                connection.execute("DELETE FROM custom_groups")
                for group in groups:
                    if not isinstance(group, dict):
                        raise ValueError("groups 必须是对象列表")
                    self._insert_custom_group(connection, group)
                self._delete_unreferenced_custom_commands(connection, old_command_ids)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def delete_custom_group(self, group_name: str) -> None:
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                old_command_ids = self._group_command_ids(connection, [group_name])
                cursor = connection.execute(
                    "DELETE FROM custom_groups WHERE group_name = ?", (group_name,)
                )
                if cursor.rowcount != 1:
                    raise KeyError(group_name)
                self._delete_unreferenced_custom_commands(connection, old_command_ids)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def create_custom_entry(self, group_name: str, entry: dict[str, object]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM custom_groups WHERE group_name = ?", (group_name,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(group_name)
            position = int(
                connection.execute(
                    "SELECT COUNT(*) FROM custom_group_commands WHERE group_id = ?",
                    (row["id"],),
                ).fetchone()[0]
            )
            self._insert_custom_entry(
                connection, group_id=int(row["id"]), entry=entry, position=position
            )
            connection.commit()

    def replace_custom_entry(
        self,
        group_name: str,
        entry_type: str,
        trigger: str,
        entry: dict[str, object],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT cgc.group_id, cgc.command_id, cgc.position
                FROM custom_group_commands cgc
                JOIN custom_groups cg ON cg.id = cgc.group_id
                JOIN commands c ON c.id = cgc.command_id
                WHERE cg.group_name = ? AND c.entry_type = ? AND c.command_key = ?
                """,
                (group_name, entry_type, trigger),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError((group_name, entry_type, trigger))
            connection.execute(
                "DELETE FROM commands WHERE id = ?", (row["command_id"],)
            )
            self._insert_custom_entry(
                connection,
                group_id=int(row["group_id"]),
                entry=entry,
                position=int(row["position"]),
            )
            connection.commit()

    def delete_custom_entry(
        self, group_name: str, entry_type: str, trigger: str
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM commands WHERE id IN (
                    SELECT c.id FROM commands c
                    JOIN custom_group_commands cgc ON cgc.command_id = c.id
                    JOIN custom_groups cg ON cg.id = cgc.group_id
                    WHERE cg.group_name = ? AND c.entry_type = ? AND c.command_key = ?
                )
                """,
                (group_name, entry_type, trigger),
            )
        if cursor.rowcount != 1:
            raise KeyError((group_name, entry_type, trigger))

    def list_custom_groups(self) -> list[dict[str, object]]:
        """按目录顺序返回自定义分组及完整命令元数据。"""
        with self._connect() as connection:
            group_rows = connection.execute(
                "SELECT * FROM custom_groups ORDER BY id"
            ).fetchall()
            result: list[dict[str, object]] = []
            for group_row in group_rows:
                command_rows = connection.execute(
                    """
                    SELECT commands.*
                    FROM custom_group_commands
                    JOIN commands ON commands.id = custom_group_commands.command_id
                    WHERE custom_group_commands.group_id = ?
                    ORDER BY custom_group_commands.position, commands.id
                    """,
                    (group_row["id"],),
                ).fetchall()
                commands: list[dict[str, object]] = []
                for command_row in command_rows:
                    command_id = int(command_row["id"])

                    def values(table: str, column: str) -> list[str]:
                        rows = connection.execute(
                            f"SELECT {column} FROM {table} WHERE command_id = ? "
                            f"ORDER BY position, rowid",
                            (command_id,),
                        ).fetchall()
                        return [str(row[0]) for row in rows]

                    commands.append(
                        {
                            "type": str(command_row["entry_type"]),
                            # 旧 JSON 对 regex 也把自然键放在 command；继续保留，
                            # 同时提供明确的 pattern 字段供新客户端使用。
                            "command": str(command_row["command_key"]),
                            "pattern": str(command_row["command_key"])
                            if command_row["entry_type"] == "regex"
                            else "",
                            "description": str(command_row["description"]),
                            "permission_level": str(command_row["permission_level"]),
                            "is_admin": command_row["permission_level"] == "admin",
                            "delegation_policy": str(command_row["delegation_policy"]),
                            "history_mode": str(command_row["history_mode"]),
                            "hidden": bool(command_row["hidden"]),
                            "linked_plugin": command_row["source_plugin"],
                            "availability": "missing_plugin"
                            if command_row["missing_plugin"]
                            else "available",
                            "aliases": values("command_aliases", "alias"),
                            "examples": values("command_examples", "example"),
                            "sub_commands": values("command_subcommands", "subcommand"),
                        }
                    )
                result.append(
                    {
                        "group_name": str(group_row["group_name"]),
                        "description": str(group_row["description"]),
                        "priority": int(group_row["priority"]),
                        "hidden": bool(group_row["hidden"]),
                        "commands": commands,
                    }
                )
        return result
