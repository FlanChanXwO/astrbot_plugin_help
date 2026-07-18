"""执行回执与重复抑制的行为测试。"""

from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.application.services.execution_receipt_service import (
    ExecutionReceiptService,
)
from src.infrastructure.storage import CommandCatalog


def test_concurrent_reserve_allows_one_dispatch_and_returns_original_receipt(tmp_path):
    """同一去重键的并发 reserve 只有一次可调度，其余复用原回执。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = ExecutionReceiptService(
        catalog,
        now_provider=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    def reserve():
        return service.reserve(
            platform_id="onebot",
            session_id="group-1",
            requester_user_id="requester",
            target_user_id="target",
            command="  打卡   今日  ",
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: reserve(), range(24)))

    assert sum(result["reserved"] for result in results) == 1
    assert {result["receipt_id"] for result in results} == {
        next(result["receipt_id"] for result in results if result["reserved"])
    }
    duplicates = [result for result in results if not result["reserved"]]
    first = next(result for result in results if result["reserved"])
    assert first["execution_state"] == "reserved"
    assert first["success"] is False
    assert first["pending"] is True
    assert {result["execution_state"] for result in duplicates} == {
        "duplicate_suppressed"
    }
    assert all(result["retryable"] is False for result in duplicates)
    assert all(result["success"] is False for result in duplicates)
    assert all(result["pending"] is True for result in duplicates)
    assert all(
        result["original_receipt"]["execution_state"] == "reserved"
        for result in duplicates
    )


def test_receipt_result_is_persisted_and_window_expiry_allows_new_reserve(tmp_path):
    """回执保存完整 JSON 结果；已确认 60 秒窗口结束后可再次调度。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = ExecutionReceiptService(catalog, now_provider=lambda: clock[0])
    first = service.reserve(
        platform_id="onebot",
        session_id="group-1",
        requester_user_id="requester",
        target_user_id="target",
        command="打卡",
    )

    saved = service.save_result(
        first["receipt_id"],
        execution_state="external_dispatched",
        dispatched=True,
        output_complete=False,
        retryable=False,
        messages=[{"type": "plain", "text": "已转发"}],
        error=None,
        result={"router": "gscore", "accepted": True},
    )
    loaded = service.get_receipt(first["receipt_id"])
    clock[0] += timedelta(seconds=60)
    second = service.reserve(
        platform_id="onebot",
        session_id="group-1",
        requester_user_id="requester",
        target_user_id="target",
        command="打卡",
    )

    assert saved == loaded
    assert loaded["success"] is True
    assert loaded["execution_state"] == "external_dispatched"
    assert loaded["result"] == {"router": "gscore", "accepted": True}
    assert loaded["messages"] == [{"type": "plain", "text": "已转发"}]
    assert loaded["target"] == {}
    assert second["reserved"] is True
    assert second["receipt_id"] != first["receipt_id"]


def test_receipt_public_target_whitelists_identity_and_never_serializes_uid(tmp_path):
    """公开回执及 duplicate original 都不能从 target_json 泄露 UID。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = ExecutionReceiptService(catalog)
    arguments = {
        "platform_id": "onebot",
        "session_id": "group-1",
        "requester_user_id": "requester",
        "target_user_id": "raw-target-uid",
        "command": "打卡",
        "target": {
            "user_id": "raw-target-uid",
            "display_name": "橡皮糖",
            "masked_user_id": "ra***id",
            "target_ref": "usr_opaque",
            "private_note": "never persist",
        },
    }

    first = service.reserve(**arguments)
    duplicate = service.reserve(**arguments)

    expected = {
        "display_name": "橡皮糖",
        "masked_user_id": "ra***id",
        "target_ref": "usr_opaque",
    }
    assert first["target"] == expected
    assert duplicate["target"] == expected
    assert duplicate["original_receipt"]["target"] == expected
    assert "raw-target-uid" not in json.dumps(duplicate, ensure_ascii=False)


def test_fail_safe_releases_reserved_when_failure_cannot_be_persisted(
    tmp_path, monkeypatch
):
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = ExecutionReceiptService(catalog)
    receipt = service.reserve(
        platform_id="onebot",
        session_id="group",
        requester_user_id="requester",
        target_user_id="target",
        command="打卡",
    )
    monkeypatch.setattr(
        service,
        "save_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("db write failed")
        ),
    )

    with pytest.raises(RuntimeError, match="reserved_released=True"):
        service.fail_or_release(receipt["receipt_id"], "executor failed")

    retry = service.reserve(
        platform_id="onebot",
        session_id="group",
        requester_user_id="requester",
        target_user_id="target",
        command="打卡",
    )
    assert retry["reserved"] is True


def test_fail_safe_preserves_reserved_when_dispatch_already_happened(
    tmp_path, monkeypatch
):
    """已派发调用即使最终回执写入失败，也不能释放去重占位触发二次执行。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = ExecutionReceiptService(catalog)
    arguments = {
        "platform_id": "onebot",
        "session_id": "group",
        "requester_user_id": "requester",
        "target_user_id": "target",
        "command": "打卡",
    }
    receipt = service.reserve(**arguments)
    monkeypatch.setattr(
        service,
        "save_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("db write failed")
        ),
    )

    with pytest.raises(RuntimeError, match="reserved_preserved=True"):
        service.fail_or_release(
            receipt["receipt_id"], "handler failed", dispatched=True
        )

    duplicate = service.reserve(**arguments)
    assert duplicate["execution_state"] == "duplicate_suppressed"
    assert duplicate["receipt_id"] == receipt["receipt_id"]


@pytest.mark.parametrize(
    ("state", "dispatched", "output_complete", "retryable"),
    [
        ("failed", False, True, True),
        ("rejected", False, True, False),
    ],
)
def test_failed_or_rejected_receipt_never_dedupes_retry_as_success(
    tmp_path, state, dispatched, output_complete, retryable
):
    """失败和拒绝不参与窗口去重，最终回执保持失败语义并允许新 reserve。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = ExecutionReceiptService(catalog)
    arguments = {
        "platform_id": "onebot",
        "session_id": "group-1",
        "requester_user_id": "requester",
        "target_user_id": "target",
        "command": "打卡",
    }
    first = service.reserve(**arguments)
    finalized = service.save_result(
        first["receipt_id"],
        execution_state=state,
        dispatched=dispatched,
        output_complete=output_complete,
        retryable=retryable,
        messages=[],
        error="真实失败",
        result={},
    )
    second = service.reserve(**arguments)

    assert finalized["success"] is False
    assert finalized["execution_state"] == state
    assert second["reserved"] is True
    assert second["receipt_id"] != first["receipt_id"]


def test_failed_after_dispatch_is_not_retryable_and_dedupes_immediate_retry(tmp_path):
    """handler 已启动后才失败时，保留失败语义但禁止重复调度。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = ExecutionReceiptService(catalog)
    arguments = {
        "platform_id": "onebot",
        "session_id": "group-1",
        "requester_user_id": "requester",
        "target_user_id": "target",
        "command": "打卡",
    }
    first = service.reserve(**arguments)

    finalized = service.save_result(
        first["receipt_id"],
        execution_state="failed",
        dispatched=True,
        output_complete=True,
        retryable=False,
        messages=[],
        error="handler dispatched then failed",
        result={"failure_stage": "handler"},
    )
    duplicate = service.reserve(**arguments)

    assert finalized["success"] is False
    assert finalized["execution_state"] == "failed"
    assert finalized["dispatched"] is True
    assert finalized["retryable"] is False
    assert duplicate["execution_state"] == "duplicate_suppressed"
    assert duplicate["receipt_id"] == first["receipt_id"]
    assert duplicate["original_receipt"] == finalized


def test_dispatched_failure_dedupes_concurrently_until_exact_window_boundary(tmp_path):
    """已派发失败在窗口内并发复用，精确到达 60 秒边界后恢复调度权。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = ExecutionReceiptService(catalog, now_provider=lambda: clock[0])
    arguments = {
        "platform_id": "onebot",
        "session_id": "group-1",
        "requester_user_id": "requester",
        "target_user_id": "target",
        "command": "打卡",
    }
    first = service.reserve(**arguments)
    service.save_result(
        first["receipt_id"],
        execution_state="failed",
        dispatched=True,
        output_complete=True,
        retryable=False,
        messages=[],
        error="handler failed after dispatch",
        result={},
    )
    clock[0] += timedelta(seconds=59, milliseconds=999)

    with ThreadPoolExecutor(max_workers=12) as pool:
        duplicates = list(pool.map(lambda _: service.reserve(**arguments), range(24)))

    assert {item["execution_state"] for item in duplicates} == {"duplicate_suppressed"}
    assert {item["receipt_id"] for item in duplicates} == {first["receipt_id"]}

    clock[0] += timedelta(milliseconds=1)
    boundary = service.reserve(**arguments)

    assert boundary["reserved"] is True
    assert boundary["receipt_id"] != first["receipt_id"]


def test_invalid_state_combination_and_nan_result_leave_receipt_reserved(tmp_path):
    """非法状态组合或非有限 JSON 数值必须显露错误，且不能部分 finalize。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = ExecutionReceiptService(catalog)
    receipt = service.reserve(
        platform_id="onebot",
        session_id="group-1",
        requester_user_id="requester",
        target_user_id="target",
        command="打卡",
    )

    with pytest.raises(ValueError, match="状态组合"):
        service.save_result(
            receipt["receipt_id"],
            execution_state="accepted",
            dispatched=False,
            output_complete=False,
            retryable=False,
            messages=[],
            error=None,
            result={},
        )
    with pytest.raises(ValueError, match="JSON"):
        service.save_result(
            receipt["receipt_id"],
            execution_state="completed",
            dispatched=True,
            output_complete=True,
            retryable=False,
            messages=[],
            error=None,
            result={"duration": float("nan")},
        )

    assert service.get_receipt(receipt["receipt_id"])["execution_state"] == "reserved"
