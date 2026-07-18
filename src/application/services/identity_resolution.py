"""会话身份解析管线。"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from src.domain.entities.identity import (
    IdentityCandidate,
    IdentityResolution,
    mask_user_id,
    normalize_identity_name,
)
from src.infrastructure.storage import CommandCatalog


class IdentityResolutionPipeline:
    """封装身份信号优先级、实时 roster 与统一结果构造。"""

    def __init__(
        self,
        catalog: CommandCatalog,
        *,
        retention_days: int,
        now_provider: Callable[[], datetime],
        settings_getter: Callable[[str, str], dict[str, bool]],
    ) -> None:
        self.catalog = catalog
        self.retention_days = retention_days
        self._now_provider = now_provider
        self._settings_getter = settings_getter

    @staticmethod
    def _scope(event: Any) -> tuple[str, str]:
        return str(event.get_platform_id()), str(event.unified_msg_origin)

    def _resolved(
        self,
        *,
        event: Any,
        user_id: str,
        display_name: str,
        source: str,
        freshness: str,
        warnings: list[str] | None = None,
    ) -> dict[str, object]:
        platform_id, session_id = self._scope(event)
        if not self._settings_getter(platform_id, user_id)["allow_llm_operation"]:
            return IdentityResolution(
                status="unavailable",
                display_name=display_name,
                masked_user_id=mask_user_id(user_id),
                source=source,
                identity_freshness=freshness,
                total_matches=1,
                warnings=tuple([*(warnings or []), "目标用户已关闭全部 AI 命令代操作"]),
            ).to_dict()
        target_ref = self.catalog.get_or_create_identity_reference(
            platform_id=platform_id,
            session_id=session_id,
            user_id=user_id,
            candidate_ref=f"usr_{secrets.token_urlsafe(24)}",
        )
        return IdentityResolution(
            status="resolved",
            display_name=display_name,
            masked_user_id=mask_user_id(user_id),
            target_ref=target_ref,
            source=source,
            identity_freshness=freshness,
            operable=True,
            total_matches=1,
            warnings=tuple(warnings or []),
        ).to_dict()

    def _candidate(
        self,
        *,
        event: Any,
        user_id: str,
        display_name: str,
        source: str,
        freshness: str,
    ) -> IdentityCandidate:
        platform_id, session_id = self._scope(event)
        operable = self._settings_getter(platform_id, user_id)["allow_llm_operation"]
        target_ref = None
        if operable:
            target_ref = self.catalog.get_or_create_identity_reference(
                platform_id=platform_id,
                session_id=session_id,
                user_id=user_id,
                candidate_ref=f"usr_{secrets.token_urlsafe(24)}",
            )
        return IdentityCandidate(
            display_name=display_name,
            masked_user_id=mask_user_id(user_id),
            target_ref=target_ref,
            source=source,
            identity_freshness=freshness,
            operable=operable,
        )

    def _ambiguous(
        self,
        *,
        event: Any,
        matches: list[Any],
        source: str,
        freshness: str,
        warnings: list[str],
    ) -> dict[str, object]:
        return IdentityResolution(
            status="ambiguous",
            source=source,
            identity_freshness=freshness,
            candidates=tuple(
                self._candidate(
                    event=event,
                    user_id=str(row["user_id"]),
                    display_name=str(row["display_name"]),
                    source=source,
                    freshness=freshness,
                )
                for row in matches[:10]
            ),
            total_matches=len(matches),
            warnings=tuple(warnings),
        ).to_dict()

    def _match_rows(
        self,
        *,
        event: Any,
        rows: list[Any],
        normalized_reference: str,
        source: str,
        freshness: str,
        warnings: list[str],
    ) -> dict[str, object] | None:
        exact = [
            row for row in rows if str(row["normalized_name"]) == normalized_reference
        ]
        if len(exact) == 1:
            row = exact[0]
            return self._resolved(
                event=event,
                user_id=str(row["user_id"]),
                display_name=str(row["display_name"]),
                source=source,
                freshness=freshness,
                warnings=warnings,
            )
        if len(exact) > 1:
            return self._ambiguous(
                event=event,
                matches=exact,
                source=source,
                freshness=freshness,
                warnings=warnings,
            )
        fuzzy = [
            row
            for row in rows
            if normalized_reference
            and normalized_reference in str(row["normalized_name"])
        ]
        if fuzzy:
            return self._ambiguous(
                event=event,
                matches=fuzzy,
                source=source,
                freshness=freshness,
                warnings=warnings,
            )
        return None

    async def _live_rows(
        self,
        *,
        event: Any,
        platform_id: str,
        session_id: str,
        seen_at: datetime,
    ) -> tuple[list[Any] | None, list[str]]:
        get_group = getattr(event, "get_group", None)
        if not callable(get_group):
            return None, []
        try:
            group = await get_group()
        except Exception as error:
            return None, [f"实时群成员查询失败: {error}"]
        if group is None:
            return None, ["实时群成员查询未返回当前群资料"]

        members = getattr(group, "members", None)
        participants: dict[str, tuple[str, str, str]] = {}
        valid = isinstance(members, list) and bool(members)
        for member in members or []:
            user_id = str(getattr(member, "user_id", "") or "").strip()
            display_name = str(getattr(member, "nickname", "") or "").strip()
            if not user_id or not display_name:
                valid = False
                break
            if user_id != str(event.get_self_id() or ""):
                participants[user_id] = (
                    user_id,
                    display_name,
                    normalize_identity_name(display_name),
                )
        if not valid or not participants:
            return None, ["实时群成员快照不可用: members 为空或字段不完整"]

        self.catalog.replace_live_roster(
            platform_id=platform_id,
            session_id=session_id,
            participants=list(participants.values()),
            seen_at=seen_at,
        )
        return (
            self.catalog.list_session_participants(
                platform_id=platform_id, session_id=session_id
            ),
            [],
        )

    async def resolve(
        self, event: Any, reference: object, *, requester_id: str
    ) -> dict[str, object]:
        """按强信号、别名、实时 roster、观察目录的固定顺序解析。"""
        raw = str(reference or "").strip()
        normalized = normalize_identity_name(raw.removeprefix("@"))
        platform_id, session_id = self._scope(event)
        now = self._now_provider()
        cutoff = now - timedelta(days=self.retention_days)

        if raw.startswith("usr_"):
            user_id = self.catalog.find_identity_reference(
                platform_id=platform_id, session_id=session_id, target_ref=raw
            )
            if user_id is not None:
                row = self.catalog.get_session_participant(
                    platform_id=platform_id,
                    session_id=session_id,
                    user_id=user_id,
                    newer_than=cutoff,
                )
                if row is not None:
                    return self._resolved(
                        event=event,
                        user_id=user_id,
                        display_name=str(row["display_name"]),
                        source="target_ref",
                        freshness=str(row["identity_source"]),
                    )

        if raw.casefold().startswith("uid:"):
            user_id = raw.partition(":")[2].strip()
            if user_id:
                row = self.catalog.get_session_participant(
                    platform_id=platform_id,
                    session_id=session_id,
                    user_id=user_id,
                )
                return self._resolved(
                    event=event,
                    user_id=user_id,
                    display_name=str(row["display_name"]) if row else user_id,
                    source="uid",
                    freshness="explicit",
                )

        known = self.catalog.get_session_participant(
            platform_id=platform_id,
            session_id=session_id,
            user_id=raw,
            newer_than=cutoff,
        )
        if known is not None:
            return self._resolved(
                event=event,
                user_id=raw,
                display_name=str(known["display_name"]),
                source="uid",
                freshness=str(known["identity_source"]),
            )

        at_by_id: dict[str, tuple[str, str]] = {}
        replies: list[tuple[str, str]] = []
        for component in event.get_messages():
            names = {cls.__name__ for cls in type(component).__mro__}
            if "AtAll" not in names and "At" in names:
                user_id = str(getattr(component, "qq", "") or "").strip()
                if user_id and user_id.casefold() != "all":
                    at_by_id[user_id] = (
                        user_id,
                        str(getattr(component, "name", "") or user_id),
                    )
            if "Reply" in names:
                user_id = str(getattr(component, "sender_id", "") or "").strip()
                if user_id:
                    replies.append(
                        (
                            user_id,
                            str(getattr(component, "sender_nickname", "") or user_id),
                        )
                    )
        at_matches = [
            item
            for item in at_by_id.values()
            if normalized
            in {
                normalize_identity_name(item[0]),
                normalize_identity_name(item[1]),
            }
        ]
        for user_id, display_name in at_matches:
            self.catalog.upsert_session_participant(
                platform_id=platform_id,
                session_id=session_id,
                user_id=user_id,
                display_name=display_name,
                normalized_name=normalize_identity_name(display_name),
                source="observed",
                seen_at=now,
            )
        if len(at_matches) == 1:
            user_id, display_name = at_matches[0]
            return self._resolved(
                event=event,
                user_id=user_id,
                display_name=display_name,
                source="at",
                freshness="current_event",
            )
        if len(at_matches) > 1:
            return self._ambiguous(
                event=event,
                matches=[
                    {"user_id": user_id, "display_name": display_name}
                    for user_id, display_name in at_matches
                ],
                source="at",
                freshness="current_event",
                warnings=[],
            )

        if raw.isdigit():
            return self._resolved(
                event=event,
                user_id=raw,
                display_name=raw,
                source="uid",
                freshness="explicit",
            )

        if normalized in {"reply_target", "他", "她", "这个人"} and len(replies) == 1:
            user_id, display_name = replies[0]
            self.catalog.upsert_session_participant(
                platform_id=platform_id,
                session_id=session_id,
                user_id=user_id,
                display_name=display_name,
                normalized_name=normalize_identity_name(display_name),
                source="observed",
                seen_at=now,
            )
            return self._resolved(
                event=event,
                user_id=user_id,
                display_name=display_name,
                source="reply",
                freshness="current_event",
            )

        alias_target = self.catalog.get_personal_alias_target(
            platform_id=platform_id,
            session_id=session_id,
            requester_id=str(requester_id),
            normalized_alias=normalized,
        )
        if alias_target is not None:
            row = self.catalog.get_session_participant(
                platform_id=platform_id,
                session_id=session_id,
                user_id=alias_target,
                newer_than=cutoff,
            )
            if row is not None:
                return self._resolved(
                    event=event,
                    user_id=alias_target,
                    display_name=str(row["display_name"]),
                    source="alias",
                    freshness=str(row["identity_source"]),
                )

        live_rows, warnings = await self._live_rows(
            event=event,
            platform_id=platform_id,
            session_id=session_id,
            seen_at=now,
        )
        if live_rows is not None:
            result = self._match_rows(
                event=event,
                rows=live_rows,
                normalized_reference=normalized,
                source="live",
                freshness="live",
                warnings=warnings,
            )
            if result is not None:
                return result

        observed = self.catalog.list_session_participants(
            platform_id=platform_id,
            session_id=session_id,
            newer_than=cutoff,
        )
        result = self._match_rows(
            event=event,
            rows=observed,
            normalized_reference=normalized,
            source="observed",
            freshness="observed",
            warnings=warnings,
        )
        if result is not None:
            return result
        return IdentityResolution(
            status="not_found", warnings=tuple(warnings)
        ).to_dict()
