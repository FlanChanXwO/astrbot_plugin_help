"""执行回执持久化与并发重复抑制。"""

from __future__ import annotations

import json
import math
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from src.infrastructure.storage import CommandCatalog


_PERSISTED_STATES = {
    "completed",
    "accepted",
    "external_dispatched",
    "rejected",
    "failed",
}

_STATE_INVARIANTS = {
    "completed": {(True, True, False)},
    "accepted": {(True, False, False)},
    "external_dispatched": {(True, False, False)},
    "rejected": {(False, True, False)},
    # 调度前失败允许重试；handler 已获调度权后才失败则保留 failed，
    # 但必须禁止重试，避免真实副作用被重复执行。
    "failed": {(False, True, True), (True, True, False)},
}


class ExecutionReceiptService:
    """以 SQLite 写事务保证同一去重键最多一次获准调度。"""

    def __init__(
        self,
        catalog: CommandCatalog,
        *,
        dedupe_seconds: float = 60.0,
        now_provider: Callable[[], datetime] | None = None,
        receipt_id_provider: Callable[[], str] | None = None,
    ) -> None:
        if not math.isfinite(dedupe_seconds) or dedupe_seconds < 0:
            raise ValueError("dedupe_seconds 必须是非负有限数")
        self.catalog = catalog
        # 60 秒是已确认的产品重复抑制窗口；构造参数可用于配置和确定性测试。
        self.dedupe_window = timedelta(seconds=dedupe_seconds)
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._receipt_id_provider = receipt_id_provider or (lambda: uuid.uuid4().hex)

    def _now(self) -> datetime:
        now = self._now_provider()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now_provider 必须返回带时区的 UTC datetime")
        return now.astimezone(timezone.utc)

    @staticmethod
    def normalize_command(command: str) -> str:
        """规范化去重文本，不截断合法命令。"""
        normalized = unicodedata.normalize("NFKC", command)
        return " ".join(normalized.split()).casefold()

    def reserve(
        self,
        *,
        platform_id: str,
        session_id: str,
        requester_user_id: str,
        target_user_id: str,
        command: str,
        target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """保留一次调度权；重复调用复用原 receipt，不创建新记录。"""
        normalized_command = self.normalize_command(command)
        if not normalized_command:
            raise ValueError("command 不能为空")
        now = self._now()
        cutoff = (now - self.dedupe_window).isoformat()
        with self.catalog._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM execution_receipts
                WHERE platform = ? AND session_id = ? AND requester_id = ?
                  AND target_user_id = ? AND normalized_command = ?
                  AND (
                      execution_state IN (
                          'reserved', 'completed', 'accepted',
                          'external_dispatched'
                      )
                      OR (
                          execution_state = 'failed'
                          AND dispatched = 1 AND retryable = 0
                      )
                  )
                  AND created_at > ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    platform_id,
                    session_id,
                    requester_user_id,
                    target_user_id,
                    normalized_command,
                    cutoff,
                ),
            ).fetchone()
            if existing is not None:
                connection.commit()
                original = self._row_to_result(existing)
                return {
                    "success": original["success"],
                    "execution_state": "duplicate_suppressed",
                    "receipt_id": original["receipt_id"],
                    "target": original["target"],
                    "dispatched": original["dispatched"],
                    "output_complete": original["output_complete"],
                    "retryable": False,
                    "messages": original["messages"],
                    "error": original["error"],
                    "result": original["result"],
                    "pending": original["pending"],
                    "reserved": False,
                    "original_receipt": original,
                }
            receipt_id = self._receipt_id_provider()
            public_target = self._public_target(target, target_user_id=target_user_id)
            connection.execute(
                """
                INSERT INTO execution_receipts(
                    receipt_id, platform, session_id, requester_id,
                    target_user_id, normalized_command, execution_state,
                    dispatched, output_complete, retryable, target_json,
                    messages_json, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'reserved', 0, 0, 0, ?, '[]', '{}', ?)
                """,
                (
                    receipt_id,
                    platform_id,
                    session_id,
                    requester_user_id,
                    target_user_id,
                    normalized_command,
                    json.dumps(public_target, ensure_ascii=False, allow_nan=False),
                    now.isoformat(),
                ),
            )
            connection.commit()
        return {
            "success": False,
            "execution_state": "reserved",
            "receipt_id": receipt_id,
            "target": public_target,
            "dispatched": False,
            "output_complete": False,
            "retryable": False,
            "messages": [],
            "error": None,
            "result": {},
            "pending": True,
            "reserved": True,
        }

    @staticmethod
    def _public_target(
        target: Mapping[str, Any] | None, *, target_user_id: str
    ) -> dict[str, Any]:
        """只持久化可公开身份字段，UID 永远只存在独立去重列。"""
        if target is None:
            return {}
        allowed = {
            "status",
            "display_name",
            "masked_user_id",
            "target_ref",
            "source",
            "identity_freshness",
            "operable",
        }
        return {
            key: target[key]
            for key in allowed
            if key in target and str(target[key]) != str(target_user_id)
        }

    def save_result(
        self,
        receipt_id: str,
        *,
        execution_state: str,
        dispatched: bool,
        output_complete: bool,
        retryable: bool,
        messages: Sequence[Mapping[str, Any]],
        error: str | None,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """持久化标准执行状态和完整 JSON 化结果。"""
        if execution_state not in _PERSISTED_STATES:
            raise ValueError(f"execution_state={execution_state} 不能持久化")
        expected = _STATE_INVARIANTS[execution_state]
        actual = (dispatched, output_complete, retryable)
        if actual not in expected:
            expected_text = " 或 ".join(
                "dispatched="
                f"{combination[0]}, output_complete={combination[1]}, "
                f"retryable={combination[2]}"
                for combination in sorted(expected)
            )
            raise ValueError(
                f"execution_state={execution_state} 状态组合应为 {expected_text}"
            )
        if execution_state in {"failed", "rejected"} and not error:
            raise ValueError(f"execution_state={execution_state} 必须包含 error")
        if execution_state not in {"failed", "rejected"} and error is not None:
            raise ValueError(f"execution_state={execution_state} 不能包含 error")
        try:
            messages_json = json.dumps(
                [dict(message) for message in messages],
                ensure_ascii=False,
                allow_nan=False,
            )
            result_json = json.dumps(dict(result), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"回执 JSON 不是有限且可序列化的数据: {exc}") from exc
        with self.catalog._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT execution_state FROM execution_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if existing is None:
                connection.rollback()
                raise KeyError(receipt_id)
            if existing["execution_state"] != "reserved":
                connection.rollback()
                raise ValueError("只有 reserved 回执可以 finalize")
            connection.execute(
                """
                UPDATE execution_receipts SET
                    execution_state = ?, dispatched = ?, output_complete = ?,
                    retryable = ?, messages_json = ?, result_json = ?, error = ?
                WHERE receipt_id = ? AND execution_state = 'reserved'
                """,
                (
                    execution_state,
                    int(dispatched),
                    int(output_complete),
                    int(retryable),
                    messages_json,
                    result_json,
                    error,
                    receipt_id,
                ),
            )
            connection.commit()
        return self.get_receipt(receipt_id)

    def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        """读取可直接返回给 LLM tool 的标准回执。"""
        with self.catalog._connect() as connection:
            row = connection.execute(
                "SELECT * FROM execution_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
        if row is None:
            raise KeyError(receipt_id)
        return self._row_to_result(row)

    def release_reserved(self, receipt_id: str) -> bool:
        """删除尚未确认调度的占位，使真实失败可以立即重试。"""
        with self.catalog._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM execution_receipts "
                "WHERE receipt_id = ? AND execution_state = 'reserved'",
                (receipt_id,),
            )
        return bool(cursor.rowcount)

    def fail_or_release(
        self,
        receipt_id: str,
        error: BaseException | str,
        *,
        dispatched: bool = False,
    ) -> dict[str, Any]:
        """优先持久化 failed；仅未调度调用可在写入失败后释放占位。"""
        detail = str(error) or type(error).__name__
        try:
            return self.save_result(
                receipt_id,
                execution_state="failed",
                dispatched=dispatched,
                output_complete=True,
                retryable=not dispatched,
                messages=[],
                error=detail,
                result={"failure_stage": "dispatch_or_receipt"},
            )
        except Exception as persist_error:
            if dispatched:
                # 已执行 handler 的副作用状态未知，保留 reserved 直到产品去重窗口
                # 自然到期，宁可短时抑制也不能因回执故障重复执行命令。
                raise RuntimeError(
                    f"回执失败无法持久化，reserved_preserved=True: {persist_error}"
                ) from persist_error
            try:
                released = self.release_reserved(receipt_id)
            except Exception as release_error:
                raise RuntimeError(
                    f"回执失败无法持久化且无法释放: {persist_error}; {release_error}"
                ) from persist_error
            raise RuntimeError(
                f"回执失败无法持久化，reserved_released={released}: {persist_error}"
            ) from persist_error

    @staticmethod
    def _row_to_result(row: Mapping[str, Any]) -> dict[str, Any]:
        state = str(row["execution_state"])
        return {
            "success": state in {"completed", "accepted", "external_dispatched"},
            "execution_state": state,
            "receipt_id": str(row["receipt_id"]),
            "target": json.loads(str(row["target_json"])),
            "dispatched": bool(row["dispatched"]),
            "output_complete": bool(row["output_complete"]),
            "retryable": bool(row["retryable"]),
            "messages": json.loads(str(row["messages_json"])),
            "error": row["error"],
            "result": json.loads(str(row["result_json"])),
            "pending": state == "reserved",
        }
