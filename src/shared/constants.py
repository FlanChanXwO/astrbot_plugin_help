"""Constants Definitions"""

from enum import Enum


class UserRole(Enum):
    """User roles"""

    MEMBER = "member"
    ADMIN = "admin"


class DefaultCFG:
    """Default configuration"""

    # Timeout settings
    TIMEOUT_ANALYSIS = 10.0

    # Limit settings
    LIMIT_TASK = 2
    LIMIT_GIANT = 1500

    # Command prefix
    COMMAND_PREFIX = "/"

    # Regex configuration
    REGEX_MAX_EXAMPLES = 10

    # Wait message delay
    DELAY_SEND = 5.0

    # Default ignored plugins
    IGNORED_PLUGINS = {
        "astrbot",
        "astrbot-web-searcher",
        "astrbot-python-interpreter",
        "session_controller",
        "builtin_commands",
        "astrbot-reminder",
        "astrbot_plugin_help",
    }

    # AI command blacklist
    AI_COMMAND_BLACKLIST = {
        "astrbot",
        "astrbot-web-searcher",
        "astrbot-python-interpreter",
        "session_controller",
        "builtin_commands",
        "astrbot-reminder",
    }

    # Default color configuration
    DEFAULT_COLORS = {
        "page_fill": "#f0f2f5",
        "c_text_primary": "#1a1a1a",
        "c_plugin_name": "#0d47a1",
        "c_plugin_id": "#546e7a",
        "c_group_title": "#6a1b9a",
        "c_group_bg": "#f3e5f5",
        "c_leaf_text": "#37474f",
        "c_desc_text": "#757575",
        "c_bullet": "#d81b60",
        "c_box_bg": "#f5f5f5",
        "c_box_stroke": "#e0e0e0",
        "c_ver_bg": "#e3f2fd",
        "c_ver_text": "#1565c0",
        "c_prio_bg": "#e8eaf6",
        "c_prio_text": "#283593",
        "c_tag_admin": "#c62828",
        "c_tag_event": "#f57c00",
        "c_tag_mcp": "#00695c",
        "c_highlight_bg": "#ffeb3b",
        "c_highlight_text": "#000000",
    }


class InternalCFG:
    """Internal configuration"""

    DELAY_SEND = 5.0

    EVENT_TYPE_MAP = {
        "OnMessage": "💬 Message Event (OnMessage)",
        "OnFriendMessage": "👤 Friend Message (OnFriendMessage)",
        "OnGroupMessage": "👥 Group Message (OnGroupMessage)",
        "OnAdminMessage": "🛡️ Admin Message (OnAdminMessage)",
        "OnCallLLM": "🤖 LLM Call (OnCallLLM)",
        "AfterLLMResponse": "✨ After LLM Response (AfterLLMResponse)",
        "OnDecoratingResult": "🎨 Decorating Result (OnDecoratingResult)",
        "OnPreProcess": "⚙️ Pre-processing (OnPreProcess)",
        "OnPostProcess": "📤 Post-processing (OnPostProcess)",
    }
