"""会话身份目录、解析、别名与隐私的行为测试。"""

from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

import pytest

from src.application.services.identity_service import IdentityService
from src.infrastructure.storage import CommandCatalog


class GroupEvent:
    """只实现身份服务所依赖的 AstrBot 公开事件边界。"""

    def __init__(
        self, *, user_id="10001", nickname="  Ｏｒａｎｇｅ  Gum  ", messages=None
    ):
        self.unified_msg_origin = "onebot:group:7788"
        self.message_obj = SimpleNamespace(
            sender=SimpleNamespace(user_id=user_id, nickname=nickname),
            message=list(messages or []),
            self_id="90000",
            group_id="7788",
        )
        self._extras = {}

    def is_private_chat(self):
        return False

    def get_platform_id(self):
        return "onebot-main"

    def get_group_id(self):
        return "7788"

    def get_message_type(self):
        return SimpleNamespace(name="GROUP_MESSAGE", value="GroupMessage")

    def get_sender_id(self):
        return str(self.message_obj.sender.user_id)

    def get_sender_name(self):
        return self.message_obj.sender.nickname

    def get_self_id(self):
        return self.message_obj.self_id

    def get_messages(self):
        return self.message_obj.message

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)


class LiveGroupEvent(GroupEvent):
    def __init__(self, *, members=None, group_error=None, **kwargs):
        super().__init__(**kwargs)
        self.live_members = list(members or [])
        self.group_error = group_error
        self.group_calls = 0

    async def get_group(self):
        self.group_calls += 1
        if self.group_error is not None:
            raise self.group_error
        return SimpleNamespace(
            members=[
                SimpleNamespace(user_id=uid, nickname=name)
                for uid, name in self.live_members
            ]
        )


class RawRosterEvent(GroupEvent):
    def __init__(self, members):
        super().__init__()
        self.members = members

    async def get_group(self):
        return SimpleNamespace(members=self.members)


class At:
    def __init__(self, qq, name=""):
        self.qq = qq
        self.name = name


class AtAll(At):
    def __init__(self):
        super().__init__("all", "全体成员")


class Reply:
    def __init__(self, sender_id, sender_nickname=""):
        self.sender_id = sender_id
        self.sender_nickname = sender_nickname


@pytest.mark.asyncio
async def test_observe_group_event_stores_only_normalized_identity_metadata(tmp_path):
    """群消息只留下会话身份元数据，名称按 NFKC/空白/casefold 规范化。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)

    observed = await service.observe_event(GroupEvent())

    assert observed == 1
    participants = service.list_session_participants(
        platform_id="onebot-main", session_id="onebot:group:7788"
    )
    assert participants == [
        {
            "user_id": "10001",
            "display_name": "  Ｏｒａｎｇｅ  Gum  ",
            "normalized_name": "orange gum",
            "source": "observed",
            "active": True,
        }
    ]


@pytest.mark.asyncio
async def test_observe_event_skips_nonhuman_events_and_records_real_at_targets(
    tmp_path,
):
    """私聊、synthetic、bot self、空 UID 与 AtAll 不得污染身份目录。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)

    private = GroupEvent()
    private.is_private_chat = lambda: True
    private.get_group_id = lambda: ""
    private.get_message_type = lambda: SimpleNamespace(
        name="FRIEND_MESSAGE", value="FriendMessage"
    )
    synthetic = GroupEvent()
    synthetic._extras["_helpinfo_synthetic_command"] = True
    bot_self = GroupEvent(user_id="90000")
    empty = GroupEvent(user_id="")
    real = GroupEvent(messages=[AtAll(), At("20002", "橡皮糖")])

    assert [
        await service.observe_event(event)
        for event in (private, synthetic, bot_self, empty, real)
    ] == [0, 0, 0, 0, 2]
    participants = service.list_session_participants(
        platform_id="onebot-main", session_id="onebot:group:7788"
    )
    assert [(item["user_id"], item["display_name"]) for item in participants] == [
        ("10001", "  Ｏｒａｎｇｅ  Gum  "),
        ("20002", "橡皮糖"),
    ]


@pytest.mark.asyncio
async def test_resolve_prefers_current_at_and_unique_reply_with_opaque_stable_ref(
    tmp_path,
):
    """当前 @ 与唯一引用可直接解析，引用不泄漏 UID 且在当前会话稳定。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    at_event = GroupEvent(messages=[At("20002", "橡皮糖")])

    first = await service.resolve(at_event, "橡皮糖", requester_id="10001")
    second = await service.resolve(at_event, "20002", requester_id="10001")

    assert first["status"] == second["status"] == "resolved"
    assert first["source"] == "at"
    assert first["target_ref"] == second["target_ref"]
    assert "20002" not in first["target_ref"]
    assert first["masked_user_id"] == "20***02"

    reply_event = GroupEvent(messages=[Reply("30003", "薄荷糖")])
    reply = await service.resolve(reply_event, "这个人", requester_id="10001")
    assert reply["status"] == "resolved"
    assert reply["display_name"] == "薄荷糖"
    assert reply["source"] == "reply"


@pytest.mark.asyncio
async def test_privacy_defaults_allow_and_deny_all_removes_operable_reference(tmp_path):
    """隐私默认允许；关闭全部后仍能辨认目标，但不泄漏可调用引用。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    event = GroupEvent(messages=[At("20002", "橡皮糖")])

    assert service.get_user_settings("onebot-main", "20002") == {
        "allow_llm_operation": True,
        "allow_sensitive_delegation": True,
    }
    service.set_user_settings(
        "onebot-main",
        "20002",
        allow_llm_operation=False,
        allow_sensitive_delegation=False,
    )
    result = await service.resolve(event, "橡皮糖", requester_id="10001")

    assert result["status"] == "unavailable"
    assert result["display_name"] == "橡皮糖"
    assert result["masked_user_id"] == "20***02"
    assert result["operable"] is False
    assert result["target_ref"] is None


@pytest.mark.asyncio
async def test_personal_alias_is_requester_and_session_scoped_with_full_crud(tmp_path):
    """个人别名只影响创建者的当前会话，并支持列出、删除与清空。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    await service.observe_event(GroupEvent(user_id="20002", nickname="橡皮糖"))
    requester_event = GroupEvent(user_id="10001", nickname="主人")

    created = await service.set_alias(
        requester_event,
        requester_id="10001",
        alias="  糖糖  ",
        target_reference="橡皮糖",
    )
    resolved = await service.resolve(requester_event, "糖糖", requester_id="10001")

    assert created["status"] == "resolved"
    assert resolved["source"] == "alias"
    assert service.list_aliases(requester_event, requester_id="10001") == [
        {
            "alias": "糖糖",
            "display_name": "橡皮糖",
            "masked_user_id": "20***02",
            "target_ref": resolved["target_ref"],
            "operable": True,
        }
    ]
    assert (await service.resolve(requester_event, "糖糖", requester_id="other"))[
        "status"
    ] == "not_found"
    assert (
        service.delete_alias(requester_event, requester_id="10001", alias="糖糖")
        is True
    )
    await service.set_alias(
        requester_event,
        requester_id="10001",
        alias="软糖",
        target_reference="橡皮糖",
    )
    assert service.clear_aliases(requester_event, requester_id="10001") == 1


@pytest.mark.asyncio
async def test_live_roster_is_queried_once_and_returns_bounded_ambiguous_candidates(
    tmp_path,
):
    """纯昵称按需查询一次成员表；重名不猜测，候选有展示上限和真实总数。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    await service.observe_event(GroupEvent(user_id="49999", nickname="已离群"))
    members = [(f"2{index:04d}", "橡皮糖") for index in range(12)]
    event = LiveGroupEvent(members=members)

    result = await service.resolve(event, "橡皮糖", requester_id="10001")

    assert event.group_calls == 1
    assert result["status"] == "ambiguous"
    assert result["total_matches"] == 12
    assert len(result["candidates"]) == 10
    assert all(candidate["source"] == "live" for candidate in result["candidates"])
    assert all("target_ref" in candidate for candidate in result["candidates"])
    active_ids = {
        item["user_id"]
        for item in service.list_session_participants(
            platform_id="onebot-main", session_id="onebot:group:7788"
        )
    }
    assert "49999" not in active_ids


@pytest.mark.asyncio
async def test_live_roster_failure_exposes_warning_then_uses_observed_identity(
    tmp_path,
):
    """实时查询失败可回退观察目录，但必须显露原因和 observed 新鲜度。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    await service.observe_event(GroupEvent(user_id="20002", nickname="橡皮糖"))
    event = LiveGroupEvent(group_error=RuntimeError("OneBot roster unavailable"))

    result = await service.resolve(event, "橡皮糖", requester_id="10001")

    assert result["status"] == "resolved"
    assert result["source"] == "observed"
    assert result["identity_freshness"] == "observed"
    assert result["warnings"] == ["实时群成员查询失败: OneBot roster unavailable"]


@pytest.mark.asyncio
async def test_unique_leading_display_name_is_an_exact_observed_alias(tmp_path):
    """群名片带附加信息时，用户口中的唯一首段昵称仍可精确解析。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    await service.observe_event(
        GroupEvent(user_id="20002", nickname="橡皮糖 1001331196513 萌新")
    )

    result = await service.resolve(GroupEvent(), "橡皮糖", requester_id="10001")

    assert result["status"] == "resolved"
    assert result["display_name"] == "橡皮糖 1001331196513 萌新"
    assert result["source"] == "observed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "members",
    [
        None,
        [],
        [SimpleNamespace(user_id="20002")],
        [SimpleNamespace(nickname="橡皮糖")],
    ],
)
async def test_invalid_live_roster_never_deactivates_observed_members(
    tmp_path, members
):
    """空或字段不完整的 roster 不构成全量快照，只能警告并回退 observed。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    await service.observe_event(GroupEvent(user_id="20002", nickname="橡皮糖"))

    result = await service.resolve(
        RawRosterEvent(members), "橡皮糖", requester_id="10001"
    )

    assert result["status"] == "resolved"
    assert result["source"] == "observed"
    assert result["warnings"] == ["实时群成员快照不可用: members 为空或字段不完整"]
    assert (
        service.list_session_participants(
            platform_id="onebot-main", session_id="onebot:group:7788"
        )[0]["user_id"]
        == "20002"
    )


@pytest.mark.asyncio
async def test_cleanup_expires_observed_identity_but_preserves_explicit_alias(tmp_path):
    """90 天窗口清理身份与 ref，用户显式别名保留并显示为不可操作。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    current = datetime(2026, 1, 1, tzinfo=UTC)
    service = IdentityService(catalog, now_provider=lambda: current)
    await service.observe_event(GroupEvent(user_id="20002", nickname="橡皮糖"))
    requester = GroupEvent(user_id="10001", nickname="主人")
    await service.set_alias(
        requester,
        requester_id="10001",
        alias="糖糖",
        target_reference="橡皮糖",
    )
    initial = await service.resolve(requester, "糖糖", requester_id="10001")

    current += timedelta(days=91)
    assert service.cleanup_observed_identities() == 1
    expired_ref = await service.resolve(
        requester, initial["target_ref"], requester_id="10001"
    )

    assert expired_ref["status"] == "not_found"
    aliases = service.list_aliases(requester, requester_id="10001")
    assert aliases[0]["alias"] == "糖糖"
    assert aliases[0]["operable"] is False
    assert aliases[0]["target_ref"] is None


@pytest.mark.asyncio
async def test_duplicate_current_at_is_ambiguous_and_privacy_hides_candidate_ref(
    tmp_path,
):
    """当前消息同名 @ 也不得猜目标，deny_all 候选不提供可执行引用。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    service.set_user_settings(
        "onebot-main",
        "20003",
        allow_llm_operation=False,
        allow_sensitive_delegation=False,
    )
    event = GroupEvent(messages=[At("20002", "橡皮糖"), At("20003", "橡皮糖")])

    result = await service.resolve(event, "橡皮糖", requester_id="10001")

    assert result["status"] == "ambiguous"
    assert result["source"] == "at"
    assert result["total_matches"] == 2
    private_candidate = next(
        item for item in result["candidates"] if item["masked_user_id"] == "20***03"
    )
    assert private_candidate["operable"] is False
    assert private_candidate["target_ref"] is None


@pytest.mark.asyncio
async def test_duplicate_at_for_same_uid_is_still_one_resolved_target(tmp_path):
    """同一用户被重复 @ 只是重复信号，不得误报为重名歧义。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    event = GroupEvent(messages=[At("20002", "橡皮糖"), At("20002", "橡皮糖")])

    result = await service.resolve(event, "橡皮糖", requester_id="10001")

    assert result["status"] == "resolved"
    assert result["source"] == "at"
    assert result["total_matches"] == 1


@pytest.mark.asyncio
async def test_observe_event_rejects_other_message_even_when_not_private(tmp_path):
    """消息类型优先，OTHER_MESSAGE 即使携带 group_id 也不能进入目录。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    event = GroupEvent()
    event.is_private_chat = lambda: False
    event.get_message_type = lambda: SimpleNamespace(
        name="OTHER_MESSAGE", value="OtherMessage"
    )

    assert await service.observe_event(event) == 0
    assert (
        service.list_session_participants(
            platform_id="onebot-main", session_id="onebot:group:7788"
        )
        == []
    )


@pytest.mark.asyncio
async def test_observe_event_requires_authoritative_group_message_type(tmp_path):
    """AstrBot 4.25.2 缺失消息类型时不能凭 group_id 猜测为群消息。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    event = GroupEvent()
    event.get_message_type = None

    assert await service.observe_event(event) == 0


@pytest.mark.asyncio
async def test_observe_event_does_not_record_at_bot_self(tmp_path):
    """消息中的 @bot 不是目标用户，不得污染参与者目录。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)

    assert (
        await service.observe_event(GroupEvent(messages=[At("90000", "机器人")])) == 1
    )
    participants = service.list_session_participants(
        platform_id="onebot-main", session_id="onebot:group:7788"
    )
    assert [item["user_id"] for item in participants] == ["10001"]


@pytest.mark.asyncio
async def test_resolve_supports_cross_platform_uid_without_guessing_unknown_text(
    tmp_path,
):
    """已知字母 UID、uid: 显式形式及 @数字可用，未知普通文本不当成 UID。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    service = IdentityService(catalog)
    await service.observe_event(
        GroupEvent(user_id="user-550e8400-e29b", nickname="跨平台用户")
    )
    event = GroupEvent(messages=[At("20002", "橡皮糖")])

    known = await service.resolve(event, "user-550e8400-e29b", requester_id="10001")
    explicit = await service.resolve(event, "uid:external-user", requester_id="10001")
    numeric_at = await service.resolve(event, "@20002", requester_id="10001")
    unknown = await service.resolve(event, "unknown-user", requester_id="10001")

    assert known["status"] == "resolved"
    assert known["source"] == "uid"
    assert explicit["status"] == "resolved"
    assert explicit["source"] == "uid"
    assert numeric_at["status"] == "resolved"
    assert numeric_at["source"] == "at"
    assert unknown["status"] == "not_found"


@pytest.mark.asyncio
async def test_resolve_excludes_expired_identity_without_waiting_for_cleanup(tmp_path):
    """解析查询自身执行 90 天边界，过期昵称、已知 UID、ref 与 alias 均失效。"""
    catalog = CommandCatalog(tmp_path / "command_catalog.db")
    catalog.initialize()
    current = datetime(2026, 1, 1, tzinfo=UTC)
    service = IdentityService(catalog, now_provider=lambda: current)
    target_id = "user-550e8400-e29b"
    await service.observe_event(GroupEvent(user_id=target_id, nickname="橡皮糖"))
    requester = GroupEvent()
    await service.set_alias(
        requester,
        requester_id="10001",
        alias="糖糖",
        target_reference=target_id,
    )
    target_ref = (await service.resolve(requester, target_id, requester_id="10001"))[
        "target_ref"
    ]

    current += timedelta(days=91)

    for reference in ("橡皮糖", target_id, target_ref, "糖糖"):
        result = await service.resolve(requester, reference, requester_id="10001")
        assert result["status"] == "not_found"
    alias = service.list_aliases(requester, requester_id="10001")[0]
    assert alias["operable"] is False
    assert alias["target_ref"] is None
