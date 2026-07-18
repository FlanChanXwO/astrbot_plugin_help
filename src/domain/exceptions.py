"""领域异常定义"""


class HelpPluginError(Exception):
    """插件基础异常"""

    pass


class ConfigNotInitializedError(HelpPluginError):
    """配置未初始化异常"""

    pass


class ContextNotInitializedError(HelpPluginError):
    """Context 未初始化异常"""

    pass


class CommandNotFoundError(HelpPluginError):
    """命令未找到异常"""

    pass
