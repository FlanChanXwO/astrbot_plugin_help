"""隐私及个人别名聊天命令行为。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.application.services.command_runtime_service import CommandRuntimeService
from src.infrastructure.config.datamodels import HelpPluginConfig
from tests.mocks import MockAstrMessageEvent
from tests.test_custom_group_management import _load_entry_module


class _ChatEvent(MockAstrMessageEvent):
    def plain_result(self, text: str) -> str:
        return text


async def _collect(generator) -> list[str]:
    return [item async for item in generator]


def _plugin(tmp_path, monkeypatch):
    runtime = CommandRuntimeService(
        data_dir=tmp_path,
        config=HelpPluginConfig(),
        context=None,
        command_index=None,
        command_executor=None,
    )
    runtime.initialize()
    service = SimpleNamespace(command_runtime=runtime)
    module = _load_entry_module()
    monkeypatch.setattr(module, "get_help_service", lambda: service)
    return object.__new__(module.HelpPlugin), runtime


@pytest.mark.asyncio
async def test_privacy_self_modes_and_normal_user_cannot_set_other(
    tmp_path, monkeypatch
) -> None:
    plugin, runtime = _plugin(tmp_path, monkeypatch)
    user = _ChatEvent(user_id="user", group_id="group")

    denied = await _collect(plugin.ai_command_privacy(user, "deny_all"))
    allowed = await _collect(plugin.ai_command_privacy(user, "allow"))
    escalation = await _collect(
        plugin.ai_command_privacy(user, "set", "uid:target", "allow")
    )

    assert json.loads(denied[0])["allow_llm_operation"] is False
    assert json.loads(allowed[0])["allow_sensitive_delegation"] is True
    assert escalation == ["只有管理员可以修改其他用户的隐私设置"]
    assert (
        runtime.identity_service.get_user_settings(user.get_platform_id(), "target")[
            "allow_llm_operation"
        ]
        is True
    )


@pytest.mark.asyncio
async def test_admin_can_restore_deny_all_target(tmp_path, monkeypatch) -> None:
    plugin, runtime = _plugin(tmp_path, monkeypatch)
    admin = _ChatEvent(user_id="admin", group_id="group", is_admin_flag=True)
    runtime.identity_service.set_user_settings(
        admin.get_platform_id(),
        "target",
        allow_llm_operation=False,
        allow_sensitive_delegation=False,
    )

    response = await _collect(
        plugin.ai_command_privacy(admin, "set", "uid:target", "allow")
    )

    assert json.loads(response[0])["allow_llm_operation"] is True
    assert (
        runtime.identity_service.get_user_settings(admin.get_platform_id(), "target")[
            "allow_sensitive_delegation"
        ]
        is True
    )
    assert "target_ref" not in response[0]


@pytest.mark.asyncio
async def test_alias_crud_and_invalid_target_returns_explicit_error(
    tmp_path, monkeypatch
) -> None:
    plugin, runtime = _plugin(tmp_path, monkeypatch)
    event = _ChatEvent(user_id="user", group_id="group")
    scope = {
        "platform_id": event.get_platform_id(),
        "session_id": event.unified_msg_origin,
        "source": "observed",
        "seen_at": datetime.now(UTC),
    }
    for user_id, name in (("target", "橡皮糖"), ("other", "同名"), ("third", "同名")):
        runtime.catalog.upsert_session_participant(
            user_id=user_id,
            display_name=name,
            normalized_name=runtime.identity_service.normalize_name(name),
            **scope,
        )

    created = await _collect(plugin.ai_command_alias(event, "set", "糖", "target"))
    listed = await _collect(plugin.ai_command_alias(event, "list"))
    deleted = await _collect(plugin.ai_command_alias(event, "delete", "糖"))
    await _collect(plugin.ai_command_alias(event, "set", "糖", "target"))
    cleared = await _collect(plugin.ai_command_alias(event, "clear"))
    ambiguous = await _collect(plugin.ai_command_alias(event, "set", "坏别名", "同名"))
    runtime.identity_service.set_user_settings(
        event.get_platform_id(),
        "target",
        allow_llm_operation=False,
        allow_sensitive_delegation=False,
    )
    unavailable = await _collect(
        plugin.ai_command_alias(event, "set", "不可用", "target")
    )

    assert json.loads(created[0])["status"] == "resolved"
    assert json.loads(listed[0])[0]["alias"] == "糖"
    assert json.loads(deleted[0])["deleted"] is True
    assert json.loads(cleared[0])["deleted"] == 1
    assert json.loads(ambiguous[0])["success"] is False
    assert "唯一解析" in json.loads(ambiguous[0])["error"]
    assert json.loads(unavailable[0])["success"] is False


@pytest.mark.asyncio
async def test_history_clear_self_returns_removed_counts(tmp_path, monkeypatch) -> None:
    """用户可清除自己的明细和聚合，结果明确返回计数。"""
    plugin, runtime = _plugin(tmp_path, monkeypatch)
    event = _ChatEvent(user_id="user", group_id="group")
    calls: list[dict[str, object]] = []

    def clear_user_history(**kwargs):
        calls.append(kwargs)
        return {"details_removed": 2, "aggregates_removed": 1}

    monkeypatch.setattr(
        runtime.history_service, "clear_user_history", clear_user_history
    )

    response = await _collect(plugin.ai_command_history(event, "clear"))

    assert json.loads(response[0]) == {
        "success": True,
        "target": {"source": "requester"},
        "details_removed": 2,
        "aggregates_removed": 1,
    }
    assert calls == [
        {
            "platform_id": event.get_platform_id(),
            "target_user_id": "user",
            "requester_user_id": "user",
            "is_admin": False,
        }
    ]


@pytest.mark.asyncio
async def test_history_clear_target_requires_admin_without_deleting(
    tmp_path, monkeypatch
) -> None:
    """普通用户指定目标时必须在解析和删除前拒绝。"""
    plugin, runtime = _plugin(tmp_path, monkeypatch)
    event = _ChatEvent(user_id="user", group_id="group")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime.history_service,
        "clear_user_history",
        lambda **kwargs: calls.append(kwargs),
    )

    response = await _collect(plugin.ai_command_history(event, "clear", "uid:target"))

    assert response == ["只有管理员可以清除其他用户的命令历史"]
    assert calls == []


@pytest.mark.asyncio
async def test_admin_history_clear_requires_resolved_target(
    tmp_path, monkeypatch
) -> None:
    """管理员的目标解析失败时返回身份结果且不删除。"""
    plugin, runtime = _plugin(tmp_path, monkeypatch)
    event = _ChatEvent(user_id="admin", group_id="group", is_admin_flag=True)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        runtime.history_service,
        "clear_user_history",
        lambda **kwargs: calls.append(kwargs),
    )

    response = await _collect(plugin.ai_command_history(event, "clear", "不存在"))
    payload = json.loads(response[0])

    assert payload["success"] is False
    assert payload["identity"]["status"] == "not_found"
    assert calls == []


@pytest.mark.asyncio
async def test_admin_history_clear_resolved_target_returns_counts(
    tmp_path, monkeypatch
) -> None:
    """管理员指定唯一目标时以管理解析结果执行清除。"""
    plugin, runtime = _plugin(tmp_path, monkeypatch)
    event = _ChatEvent(user_id="admin", group_id="group", is_admin_flag=True)
    calls: list[dict[str, object]] = []

    def clear_user_history(**kwargs):
        calls.append(kwargs)
        return {"details_removed": 3, "aggregates_removed": 2}

    monkeypatch.setattr(
        runtime.history_service, "clear_user_history", clear_user_history
    )

    response = await _collect(plugin.ai_command_history(event, "clear", "uid:target"))
    payload = json.loads(response[0])

    assert payload["success"] is True
    assert payload["details_removed"] == 3
    assert payload["aggregates_removed"] == 2
    assert "target_ref" not in payload["target"]
    assert calls[0]["target_user_id"] == "target"
    assert calls[0]["is_admin"] is True
