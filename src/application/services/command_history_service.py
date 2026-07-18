"""命令执行历史、长期聚合与保守偏好排序。"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from src.infrastructure.storage import CommandCatalog


_RECORDABLE_STATES = {"completed", "accepted", "external_dispatched"}


class CommandHistoryService:
    """统一执行历史脱敏、隐私校验、保留期和偏好提升。"""

    def __init__(
        self,
        catalog: CommandCatalog,
        *,
        retention_days: int = 90,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days 必须为正整数")
        self.catalog = catalog
        # 90 天是已确认的产品明细保留策略，构造参数允许部署方调整。
        self.retention = timedelta(days=retention_days)
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        now = self._now_provider()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now_provider 必须返回带时区的 UTC datetime")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _check_viewer(
        *, target_user_id: str, requester_user_id: str, is_admin: bool
    ) -> None:
        if target_user_id != requester_user_id and not is_admin:
            raise PermissionError("不能查看其他用户的命令偏好明细")

    def record_execution(
        self,
        *,
        platform_id: str,
        target_user_id: str,
        command_id: int,
        command_key: str,
        command_text: str | None,
        execution_state: str,
    ) -> dict[str, object]:
        """按命令 history_mode 原子记录明细及目标用户长期聚合。"""
        if execution_state not in _RECORDABLE_STATES:
            raise ValueError(f"execution_state={execution_state} 不可记录")
        used_at = self._now().isoformat()
        with self.catalog._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            command = connection.execute(
                "SELECT command_key, history_mode, delegation_policy "
                "FROM commands WHERE id = ?",
                (command_id,),
            ).fetchone()
            if command is None:
                connection.rollback()
                raise KeyError(command_id)
            history_mode = str(command["history_mode"])
            if history_mode == "none":
                connection.rollback()
                return {"recorded": False, "reason": "history_disabled"}
            if history_mode == "full" and command["delegation_policy"] != "normal":
                connection.rollback()
                raise ValueError("敏感或禁止委托命令不能记录完整调用")
            stored_text = command_text if history_mode == "full" else None
            # command 模式必须使用目录中的 canonical trigger，不能信任调用方
            # 传入的文本，否则参数可能借 command_key 字段绕过脱敏。
            canonical_key = str(command["command_key"])
            connection.execute(
                """
                INSERT INTO command_history(
                    platform, target_user_id, command_id, command_key,
                    command_text, execution_state, used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform_id,
                    target_user_id,
                    command_id,
                    canonical_key,
                    stored_text,
                    execution_state,
                    used_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO command_usage_aggregates(
                    platform, target_user_id, command_key, use_count, last_used_at
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(platform, target_user_id, command_key) DO UPDATE SET
                    use_count = command_usage_aggregates.use_count + 1,
                    last_used_at = excluded.last_used_at
                """,
                (platform_id, target_user_id, canonical_key, used_at),
            )
            connection.commit()
        return {"recorded": True, "history_mode": history_mode}

    def list_recent(
        self,
        *,
        platform_id: str,
        target_user_id: str,
        requester_user_id: str,
        is_admin: bool = False,
    ) -> list[dict[str, object]]:
        """读取保留期内的明细；查询本身也应用 90 天 cutoff。"""
        self._check_viewer(
            target_user_id=target_user_id,
            requester_user_id=requester_user_id,
            is_admin=is_admin,
        )
        cutoff = (self._now() - self.retention).isoformat()
        with self.catalog._connect() as connection:
            rows = connection.execute(
                """
                SELECT command_id, command_key, command_text, execution_state, used_at
                FROM command_history
                WHERE platform = ? AND target_user_id = ? AND used_at >= ?
                ORDER BY used_at DESC, id DESC
                """,
                (platform_id, target_user_id, cutoff),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_frequent(
        self,
        *,
        platform_id: str,
        target_user_id: str,
        requester_user_id: str,
        is_admin: bool = False,
    ) -> list[dict[str, object]]:
        """读取长期聚合；仅本人或管理员可查看统计。"""
        self._check_viewer(
            target_user_id=target_user_id,
            requester_user_id=requester_user_id,
            is_admin=is_admin,
        )
        with self.catalog._connect() as connection:
            rows = connection.execute(
                """
                SELECT command_key, use_count, last_used_at
                FROM command_usage_aggregates
                WHERE platform = ? AND target_user_id = ?
                ORDER BY use_count DESC, last_used_at DESC, command_key
                """,
                (platform_id, target_user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def cleanup_expired(self) -> int:
        """删除超过产品保留期的明细，长期聚合保持不变。"""
        cutoff = (self._now() - self.retention).isoformat()
        with self.catalog._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM command_history WHERE used_at < ?", (cutoff,)
            )
        return int(cursor.rowcount)

    def clear_user_history(
        self,
        *,
        platform_id: str,
        target_user_id: str,
        requester_user_id: str,
        is_admin: bool = False,
    ) -> dict[str, int]:
        """原子清除用户明细及长期聚合。"""
        self._check_viewer(
            target_user_id=target_user_id,
            requester_user_id=requester_user_id,
            is_admin=is_admin,
        )
        with self.catalog._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            details = connection.execute(
                "DELETE FROM command_history WHERE platform = ? AND target_user_id = ?",
                (platform_id, target_user_id),
            ).rowcount
            aggregates = connection.execute(
                "DELETE FROM command_usage_aggregates "
                "WHERE platform = ? AND target_user_id = ?",
                (platform_id, target_user_id),
            ).rowcount
            connection.commit()
        return {
            "details_removed": int(details),
            "aggregates_removed": int(aggregates),
        }

    def apply_preference_boost(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        platform_id: str,
        target_user_id: str,
        requester_user_id: str,
        keyword: str | None,
        preference_mode: str = "auto",
        is_admin: bool = False,
    ) -> list[dict[str, Any]]:
        """盲应用目标偏好；结果不暴露目标用户的频率或历史统计。"""
        if preference_mode not in {"auto", "recent", "frequent", "off"}:
            raise ValueError(f"未知 preference_mode: {preference_mode}")
        normalized_keyword = "" if keyword is None else keyword.strip()
        if (
            not normalized_keyword
            and target_user_id != requester_user_id
            and not is_admin
        ):
            raise PermissionError("空关键词不能查询其他用户的偏好")

        prepared: list[tuple[dict[str, Any], float]] = []
        bucket_maximums: dict[tuple[bool, bool], float] = {}
        for candidate in candidates:
            row = dict(candidate)
            if "relevance_score" not in row:
                raise ValueError("candidate 缺少 relevance_score")
            try:
                base_score = float(row["relevance_score"])
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "candidate relevance_score 必须是有限非负数"
                ) from error
            if not math.isfinite(base_score) or base_score < 0:
                raise ValueError("candidate relevance_score 必须是有限非负数")
            bucket = (
                bool(row.get("permission_allowed", True)),
                bool(row.get("exact", False)),
            )
            bucket_maximums[bucket] = max(bucket_maximums.get(bucket, 0.0), base_score)
            prepared.append((row, base_score))

        recent_scores: dict[str, float] = {}
        frequent_scores: dict[str, float] = {}
        if preference_mode != "off":
            now = self._now()
            cutoff = (now - self.retention).isoformat()
            with self.catalog._connect() as connection:
                recent_rows = connection.execute(
                    """
                    SELECT command_key, MAX(used_at) AS last_used_at
                    FROM command_history
                    WHERE platform = ? AND target_user_id = ? AND used_at >= ?
                    GROUP BY command_key
                    """,
                    (platform_id, target_user_id, cutoff),
                ).fetchall()
                frequent_rows = connection.execute(
                    """
                    SELECT command_key, use_count
                    FROM command_usage_aggregates
                    WHERE platform = ? AND target_user_id = ?
                    """,
                    (platform_id, target_user_id),
                ).fetchall()
            for row in recent_rows:
                last_used = datetime.fromisoformat(str(row["last_used_at"]))
                if last_used.tzinfo is None:
                    last_used = last_used.replace(tzinfo=timezone.utc)
                age_fraction = (
                    now - last_used.astimezone(timezone.utc)
                ) / self.retention
                recent_scores[str(row["command_key"])] = max(
                    0.0, min(1.0, 1.0 - age_fraction)
                )
            maximum_count = max(
                (int(row["use_count"]) for row in frequent_rows), default=0
            )
            if maximum_count:
                frequent_scores = {
                    str(row["command_key"]): int(row["use_count"]) / maximum_count
                    for row in frequent_rows
                }

        ranked: list[tuple[bool, bool, float, int, dict[str, Any]]] = []
        for position, (row, base_score) in enumerate(prepared):
            command_key = str(row.get("command_key") or row.get("command") or "")
            if preference_mode == "recent":
                preference = recent_scores.get(command_key, 0.0)
            elif preference_mode == "frequent":
                preference = frequent_scores.get(command_key, 0.0)
            elif preference_mode == "auto":
                preference = max(
                    recent_scores.get(command_key, 0.0),
                    frequent_scores.get(command_key, 0.0),
                )
            else:
                preference = 0.0
            permission_allowed = bool(row.get("permission_allowed", True))
            exact = bool(row.get("exact", False))
            maximum = bucket_maximums[(permission_allowed, exact)]
            normalized_base = base_score / maximum if maximum else 0.0
            # 80/20 是已确认的产品排序上限。真实 relevance_score 仅在
            # permission/exact 桶内归一化；combined 只作私有排序键，
            # 不能写回结果让第三方反推出目标用户偏好统计。
            combined = normalized_base * 0.8 + preference * 0.2
            ranked.append((permission_allowed, exact, combined, -position, row))
        ranked.sort(
            key=lambda item: (item[0], item[1], item[2], item[3]),
            reverse=True,
        )
        return [item[4] for item in ranked]
