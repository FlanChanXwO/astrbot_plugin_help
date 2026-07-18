"""命令执行历史与偏好的行为测试。"""

from datetime import datetime, timedelta, timezone

import pytest

from src.application.services.command_catalog_service import CommandCatalogService
from src.application.services.command_history_service import CommandHistoryService
from src.infrastructure.storage import CommandCatalog


def test_record_execution_obeys_history_mode_and_updates_target_aggregate(tmp_path):
    """历史归目标用户；command 脱敏参数，full 保存调用，none 完全不记。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    catalog_service = CommandCatalogService(catalog)
    catalog_service.sync_all_runtime(
        [
            {"plugin": "daily", "command": command, "handler_name": command}
            for command in ("打卡", "天气", "帮助")
        ],
        active_plugins={"daily"},
    )
    ids = {
        row["command"]: row["id"]
        for row in catalog_service.list_commands(page=1, page_size=20)["items"]
    }
    command_id = ids["打卡"]
    full_id = ids["天气"]
    none_id = ids["帮助"]
    catalog_service.update_command_policy(full_id, history_mode="full")
    catalog_service.update_command_policy(none_id, history_mode="none")
    history = CommandHistoryService(catalog)

    history.record_execution(
        platform_id="onebot",
        target_user_id="target",
        command_id=command_id,
        command_key="打卡",
        command_text="打卡 secret-token",
        execution_state="completed",
    )
    history.record_execution(
        platform_id="onebot",
        target_user_id="target",
        command_id=full_id,
        command_key="天气",
        command_text="天气 上海",
        execution_state="accepted",
    )
    skipped = history.record_execution(
        platform_id="onebot",
        target_user_id="target",
        command_id=none_id,
        command_key="帮助",
        command_text="帮助 private",
        execution_state="external_dispatched",
    )

    recent = history.list_recent(
        platform_id="onebot",
        target_user_id="target",
        requester_user_id="target",
    )
    frequent = history.list_frequent(
        platform_id="onebot",
        target_user_id="target",
        requester_user_id="target",
    )
    assert skipped == {"recorded": False, "reason": "history_disabled"}
    assert {row["command_key"] for row in recent} == {"打卡", "天气"}
    assert (
        next(row for row in recent if row["command_key"] == "打卡")["command_text"]
        is None
    )
    assert (
        next(row for row in recent if row["command_key"] == "天气")["command_text"]
        == "天气 上海"
    )
    assert {row["command_key"] for row in frequent} == {"打卡", "天气"}

    with pytest.raises(ValueError, match="不可记录"):
        history.record_execution(
            platform_id="onebot",
            target_user_id="target",
            command_id=command_id,
            command_key="打卡",
            command_text="打卡",
            execution_state="failed",
        )


def test_retention_filters_and_cleans_details_but_clear_removes_aggregates(tmp_path):
    """90 天 cutoff 同时用于查询和清理，清理明细不影响长期聚合。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    catalog_service = CommandCatalogService(catalog)
    catalog_service.sync_all_runtime(
        [{"plugin": "daily", "command": "打卡", "handler_name": "check_in"}],
        active_plugins={"daily"},
    )
    command_id = catalog_service.list_commands(page=1, page_size=10)["items"][0]["id"]
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    history = CommandHistoryService(catalog, now_provider=lambda: clock[0])
    history.record_execution(
        platform_id="onebot",
        target_user_id="target",
        command_id=command_id,
        command_key="打卡",
        command_text="打卡",
        execution_state="completed",
    )

    clock[0] += timedelta(days=91)
    assert (
        history.list_recent(
            platform_id="onebot",
            target_user_id="target",
            requester_user_id="target",
        )
        == []
    )
    assert history.cleanup_expired() == 1
    assert (
        history.list_frequent(
            platform_id="onebot",
            target_user_id="target",
            requester_user_id="target",
        )[0]["use_count"]
        == 1
    )
    assert history.clear_user_history(
        platform_id="onebot",
        target_user_id="target",
        requester_user_id="target",
    ) == {"details_removed": 0, "aggregates_removed": 1}


def test_clear_user_history_rejects_third_party_without_deleting_data(tmp_path):
    """普通第三方不能清除目标历史，拒绝后明细和聚合保持不变。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    catalog_service = CommandCatalogService(catalog)
    catalog_service.sync_all_runtime(
        [{"plugin": "daily", "command": "打卡", "handler_name": "check_in"}],
        active_plugins={"daily"},
    )
    command_id = catalog_service.list_commands(page=1, page_size=10)["items"][0]["id"]
    history = CommandHistoryService(catalog)
    history.record_execution(
        platform_id="onebot",
        target_user_id="target",
        command_id=command_id,
        command_key="打卡",
        command_text="打卡",
        execution_state="completed",
    )

    with pytest.raises(PermissionError, match="其他用户"):
        history.clear_user_history(
            platform_id="onebot",
            target_user_id="target",
            requester_user_id="other",
        )

    assert (
        len(
            history.list_recent(
                platform_id="onebot",
                target_user_id="target",
                requester_user_id="target",
            )
        )
        == 1
    )
    assert (
        history.list_frequent(
            platform_id="onebot",
            target_user_id="target",
            requester_user_id="target",
        )[0]["use_count"]
        == 1
    )


def test_preference_boost_is_capped_and_cannot_outrank_exact_or_permission(tmp_path):
    """偏好最多贡献 20%，且不能翻转精确匹配或权限结果。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    catalog_service = CommandCatalogService(catalog)
    catalog_service.sync_all_runtime(
        [
            {"plugin": "daily", "command": "打卡", "handler_name": "check_in"},
            {"plugin": "daily", "command": "签到", "handler_name": "sign_in"},
        ],
        active_plugins={"daily"},
    )
    rows = catalog_service.list_commands(page=1, page_size=20)["items"]
    ids = {row["command"]: row["id"] for row in rows}
    history = CommandHistoryService(catalog)
    for _ in range(5):
        history.record_execution(
            platform_id="onebot",
            target_user_id="target",
            command_id=ids["签到"],
            command_key="签到",
            command_text="签到",
            execution_state="completed",
        )

    ranked = history.apply_preference_boost(
        [
            {
                "command_key": "签到",
                "relevance_score": 80,
                "exact": False,
                "permission_allowed": True,
            },
            {
                "command_key": "查询",
                "relevance_score": 100,
                "exact": False,
                "permission_allowed": True,
            },
            {
                "command_key": "打卡",
                "relevance_score": 10,
                "exact": True,
                "permission_allowed": True,
            },
            {
                "command_key": "隐藏管理",
                "relevance_score": 10000,
                "exact": True,
                "permission_allowed": False,
            },
        ],
        platform_id="onebot",
        target_user_id="target",
        requester_user_id="other",
        keyword="打卡",
        preference_mode="frequent",
    )

    assert [row["command_key"] for row in ranked] == [
        "打卡",
        "签到",
        "查询",
        "隐藏管理",
    ]
    assert ranked[1]["relevance_score"] == 80
    assert "use_count" not in ranked[1]
    assert "score" not in ranked[1]
    assert "combined_score" not in ranked[1]
    assert "preference_score" not in ranked[1]
    with pytest.raises(PermissionError, match="其他用户"):
        history.apply_preference_boost(
            [],
            platform_id="onebot",
            target_user_id="target",
            requester_user_id="other",
            keyword="",
        )


@pytest.mark.parametrize("keyword", [None, "", "  \t\n  "])
def test_third_party_empty_keyword_is_normalized_before_preference_lookup(
    tmp_path, keyword
):
    """None 或去空白后为空都先拒绝，不能成为第三方偏好查询旁路。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    history = CommandHistoryService(catalog)

    with pytest.raises(PermissionError, match="其他用户"):
        history.apply_preference_boost(
            [{"command_key": "打卡", "relevance_score": 100}],
            platform_id="onebot",
            target_user_id="target",
            requester_user_id="other",
            keyword=keyword,
        )


def test_preference_boost_requires_real_relevance_score(tmp_path):
    """候选缺少真实搜索相关度时必须报错，不能以零分静默参与排序。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    history = CommandHistoryService(catalog)

    with pytest.raises(ValueError, match="relevance_score"):
        history.apply_preference_boost(
            [{"command_key": "打卡", "exact": True}],
            platform_id="onebot",
            target_user_id="target",
            requester_user_id="target",
            keyword="打卡",
            preference_mode="off",
        )
