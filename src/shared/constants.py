"""Constants Definitions"""

from enum import Enum


class UserRole(Enum):
    """User roles"""

    MEMBER = "member"
    ADMIN = "admin"


class DefaultCFG:
    """Default configuration"""

    # Command prefix
    COMMAND_PREFIX = "/"

    # Regex configuration
    REGEX_MAX_EXAMPLES = 10

    # AI command blacklist
    AI_COMMAND_BLACKLIST = {
        "astrbot",
        "astrbot-web-searcher",
        "astrbot-python-interpreter",
        "session_controller",
        "builtin_commands",
        "astrbot-reminder",
    }
