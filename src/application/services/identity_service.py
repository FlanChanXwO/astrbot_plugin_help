"""会话身份目录应用服务。"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from src.domain.entities.identity import mask_user_id, normalize_identity_name
from src.infrastructure.storage import CommandCatalog

from .identity_resolution import IdentityResolutionPipeline


class IdentityService:
    """负责身份观察、隐私、别名，并以 Facade 暴露解析管线。"""

    def __init__(
        self,
        catalog: CommandCatalog,
        *,
        retention_days: int = 90,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.catalog = catalog
        if retention_days < 0:
            raise ValueError("retention_days 不能为负数")
        # 90 天来自产品确定的身份观察/历史保留窗口；允许部署配置覆盖。
        self.retention_days = retention_days
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._resolver = IdentityResolutionPipeline(
            catalog,
            retention_days=retention_days,
            now_provider=self._now_provider,
            settings_getter=self.get_user_settings,
        )

    @staticmethod
    def normalize_name(value: object) -> str:
        """统一昵称及别名的等价比较形式。"""
        return normalize_identity_name(value)

    @staticmethod
    def mask_user_id(user_id: str) -> str:
        """隐藏 UID 中段，候选展示不暴露完整平台标识。"""
        return mask_user_id(user_id)

    @staticmethod
    def _event_scope(event: Any) -> tuple[str, str]:
        return str(event.get_platform_id()), str(event.unified_msg_origin)

    def _identity_cutoff(self) -> datetime:
        return self._now_provider() - timedelta(days=self.retention_days)

    async def observe_event(self, event: Any) -> int:
        """只观察 AstrBot 明确标记为 GROUP_MESSAGE 的真实消息。"""
        get_message_type = getattr(event, "get_message_type", None)
        message_type = get_message_type() if callable(get_message_type) else None
        type_name = str(getattr(message_type, "name", ""))
        type_value = str(getattr(message_type, "value", message_type or ""))
        if message_type is None or not (
            type_name == "GROUP_MESSAGE"
            or type_value in {"GroupMessage", "GROUP_MESSAGE"}
        ):
            return 0
        if event.get_extra("_helpinfo_synthetic_command", False):
            return 0
        user_id = str(event.get_sender_id() or "").strip()
        self_id = str(event.get_self_id() or "")
        if not user_id or user_id == self_id:
            return 0

        identities = [(user_id, str(event.get_sender_name() or user_id))]
        for component in event.get_messages():
            component_names = {cls.__name__ for cls in type(component).__mro__}
            target_id = str(getattr(component, "qq", "") or "").strip()
            if "AtAll" in component_names or target_id.casefold() == "all":
                continue
            if "At" not in component_names or not target_id or target_id == self_id:
                continue
            identities.append(
                (target_id, str(getattr(component, "name", "") or target_id))
            )

        platform_id, session_id = self._event_scope(event)
        now = self._now_provider()
        for observed_id, display_name in identities:
            self.catalog.upsert_session_participant(
                platform_id=platform_id,
                session_id=session_id,
                user_id=observed_id,
                display_name=display_name,
                normalized_name=self.normalize_name(display_name),
                source="observed",
                seen_at=now,
            )
        return len(identities)

    def list_session_participants(
        self, *, platform_id: str, session_id: str
    ) -> list[dict[str, object]]:
        """提供保留窗口内且不含消息内容的身份快照。"""
        return [
            {
                "user_id": str(row["user_id"]),
                "display_name": str(row["display_name"]),
                "normalized_name": str(row["normalized_name"]),
                "source": str(row["identity_source"]),
                "active": bool(row["active"]),
            }
            for row in self.catalog.list_session_participants(
                platform_id=platform_id,
                session_id=session_id,
                newer_than=self._identity_cutoff(),
            )
        ]

    def get_user_settings(self, platform_id: str, user_id: str) -> dict[str, bool]:
        """返回可 JSON 化的用户隐私设置。"""
        mode = self.catalog.get_privacy_mode(
            platform_id=str(platform_id), user_id=str(user_id)
        )
        return {
            "allow_llm_operation": mode != "deny_all",
            "allow_sensitive_delegation": mode == "allow",
        }

    def set_user_settings(
        self,
        platform_id: str,
        user_id: str,
        *,
        allow_llm_operation: bool,
        allow_sensitive_delegation: bool,
    ) -> dict[str, bool]:
        """设置隐私能力；关闭全部时敏感委托也必须关闭。"""
        if not allow_llm_operation and allow_sensitive_delegation:
            raise ValueError("关闭全部 AI 代操作时不能允许敏感委托")
        mode = (
            "allow"
            if allow_sensitive_delegation
            else "deny_sensitive"
            if allow_llm_operation
            else "deny_all"
        )
        self.catalog.set_privacy_mode(
            platform_id=str(platform_id), user_id=str(user_id), privacy_mode=mode
        )
        return self.get_user_settings(platform_id, user_id)

    async def resolve(
        self, event: Any, reference: object, *, requester_id: str
    ) -> dict[str, object]:
        """通过独立解析管线解析会话目标。"""
        return await self._resolver.resolve(event, reference, requester_id=requester_id)

    async def resolve_for_management(
        self, event: Any, reference: object, *, requester_id: str
    ) -> tuple[dict[str, object], str | None]:
        """供管理员聊天命令解析目标，忽略 operable 但不公开可执行引用。"""
        resolver = IdentityResolutionPipeline(
            self.catalog,
            retention_days=self.retention_days,
            now_provider=self._now_provider,
            settings_getter=lambda _platform, _user: {
                "allow_llm_operation": True,
                "allow_sensitive_delegation": True,
            },
        )
        resolved = await resolver.resolve(
            event, reference, requester_id=str(requester_id)
        )
        target_id = None
        target_ref = resolved.get("target_ref")
        if resolved.get("status") == "resolved" and target_ref:
            platform_id, session_id = self._event_scope(event)
            target_id = self.catalog.find_identity_reference(
                platform_id=platform_id,
                session_id=session_id,
                target_ref=str(target_ref),
            )
        public = dict(resolved)
        public.pop("target_ref", None)
        public["candidates"] = [
            {
                key: value
                for key, value in dict(candidate).items()
                if key != "target_ref"
            }
            for candidate in resolved.get("candidates", [])
        ]
        return public, target_id

    async def set_alias(
        self,
        event: Any,
        *,
        requester_id: str,
        alias: object,
        target_reference: object,
    ) -> dict[str, object]:
        """将唯一可操作目标绑定为请求者当前会话的个人别名。"""
        alias_text = str(alias or "").strip()
        normalized_alias = self.normalize_name(alias_text)
        if not normalized_alias:
            raise ValueError("alias 不能为空")
        resolved = await self.resolve(
            event, target_reference, requester_id=requester_id
        )
        if resolved["status"] != "resolved":
            raise ValueError("别名目标必须已唯一解析且允许 AI 代操作")
        platform_id, session_id = self._event_scope(event)
        target_user_id = self.catalog.find_identity_reference(
            platform_id=platform_id,
            session_id=session_id,
            target_ref=str(resolved["target_ref"]),
        )
        if target_user_id is None:
            raise RuntimeError("已解析目标缺少有效的会话身份引用")
        self.catalog.save_personal_alias(
            platform_id=platform_id,
            session_id=session_id,
            requester_id=str(requester_id),
            alias=alias_text,
            normalized_alias=normalized_alias,
            target_user_id=target_user_id,
        )
        return resolved

    def list_aliases(self, event: Any, *, requester_id: str) -> list[dict[str, object]]:
        """列出个人别名；失效目标保留记录但不返回可调用引用。"""
        platform_id, session_id = self._event_scope(event)
        aliases: list[dict[str, object]] = []
        for row in self.catalog.list_personal_aliases(
            platform_id=platform_id,
            session_id=session_id,
            requester_id=str(requester_id),
        ):
            user_id = str(row["target_user_id"])
            last_seen_at = row["last_seen_at"]
            fresh = bool(
                last_seen_at
                and datetime.fromisoformat(str(last_seen_at)) >= self._identity_cutoff()
            )
            operable = (
                bool(row["active"])
                and fresh
                and self.get_user_settings(platform_id, user_id)["allow_llm_operation"]
            )
            target_ref = None
            if operable:
                target_ref = self.catalog.get_or_create_identity_reference(
                    platform_id=platform_id,
                    session_id=session_id,
                    user_id=user_id,
                    candidate_ref=f"usr_{secrets.token_urlsafe(24)}",
                )
            aliases.append(
                {
                    "alias": str(row["alias"]),
                    "display_name": str(row["display_name"] or user_id),
                    "masked_user_id": self.mask_user_id(user_id),
                    "target_ref": target_ref,
                    "operable": operable,
                }
            )
        return aliases

    def delete_alias(self, event: Any, *, requester_id: str, alias: object) -> bool:
        """删除请求者当前会话中的一个个人别名。"""
        platform_id, session_id = self._event_scope(event)
        return bool(
            self.catalog.delete_personal_alias(
                platform_id=platform_id,
                session_id=session_id,
                requester_id=str(requester_id),
                normalized_alias=self.normalize_name(alias),
            )
        )

    def clear_aliases(self, event: Any, *, requester_id: str) -> int:
        """清空请求者当前会话中的个人别名。"""
        platform_id, session_id = self._event_scope(event)
        return self.catalog.delete_personal_alias(
            platform_id=platform_id,
            session_id=session_id,
            requester_id=str(requester_id),
        )

    def cleanup_observed_identities(self) -> int:
        """按配置窗口清理观察身份；用户显式创建的别名不随之删除。"""
        return self.catalog.cleanup_session_participants(
            older_than=self._identity_cutoff()
        )
