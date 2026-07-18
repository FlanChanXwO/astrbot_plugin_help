"""身份解析值对象；只表达可序列化结果，不依赖 AstrBot 或存储。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field


def normalize_identity_name(value: object) -> str:
    """使用 NFKC、边界/连续空白与 casefold 形成身份比较键。"""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized.strip()).casefold()


def mask_user_id(user_id: str) -> str:
    """隐藏 UID 中段，候选展示不暴露完整平台标识。"""
    if len(user_id) <= 4:
        return "***"
    return f"{user_id[:2]}***{user_id[-2:]}"


@dataclass(frozen=True)
class IdentityCandidate:
    """歧义结果中的安全候选摘要。"""

    display_name: str
    masked_user_id: str
    target_ref: str | None
    source: str
    identity_freshness: str
    operable: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IdentityResolution:
    """统一身份解析结果，可直接转换成 LLM tool JSON。"""

    status: str
    display_name: str | None = None
    masked_user_id: str | None = None
    target_ref: str | None = None
    source: str | None = None
    identity_freshness: str | None = None
    operable: bool = False
    candidates: tuple[IdentityCandidate, ...] = ()
    total_matches: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        result["warnings"] = list(self.warnings)
        return result
