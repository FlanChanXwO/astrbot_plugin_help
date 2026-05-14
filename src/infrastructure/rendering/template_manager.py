"""HTML Template Manager

Manages HTML theme loading and rendering.
"""

from __future__ import annotations

import json
import threading

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..utils.logger import get_logger
from ..utils.paths import get_plugin_dir

logger = get_logger()


class HTMLTemplateManager:
    """HTML 模板管理器

    负责管理不同主题的 HTML 模板加载和渲染。
    每个主题是一个独立的目录，包含 help_template.html 和 style.css。
    """

    def __init__(self):
        """初始化模板管理器"""
        self.plugin_dir = get_plugin_dir()
        self.templates_dir = self.plugin_dir / "templates"
        self._envs: dict[str, Environment] = {}
        self._env_lock = threading.Lock()
        self._current_theme: str = "simple"

    def get_available_themes(self) -> list[str]:
        """获取所有可用主题列表

        Returns:
            主题名称列表（按字母排序）
        """
        if not self.templates_dir.exists():
            return []

        themes = []
        for item in self.templates_dir.iterdir():
            if item.is_dir() and not item.name.startswith("__"):
                # 检查是否包含必要的模板文件
                template_file = item / "help_template.html"
                if template_file.exists():
                    themes.append(item.name)

        return sorted(themes)

    def set_theme(self, theme_name: str) -> bool:
        """设置当前主题

        Args:
            theme_name: 主题名称

        Returns:
            是否设置成功
        """
        if theme_name not in self.get_available_themes():
            return False

        self._current_theme = theme_name
        return True

    def get_current_theme(self) -> str:
        """获取当前主题名称

        Returns:
            当前主题名称
        """
        return self._current_theme

    def _get_env(self) -> Environment:
        """获取当前主题的 Jinja2 环境

        Returns:
            Jinja2 Environment 实例
        """
        with self._env_lock:
            env = self._envs.get(self._current_theme)
            if env is not None:
                return env

        # 创建新的 Jinja2 环境
        theme_dir = self.templates_dir / self._current_theme
        env = Environment(
            loader=FileSystemLoader(str(theme_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        with self._env_lock:
            self._envs[self._current_theme] = env

        return env

    def render_template(self, template_name: str, **kwargs) -> str:
        """渲染指定模板

        Args:
            template_name: 模板文件名
            **kwargs: 模板变量

        Returns:
            渲染后的 HTML 字符串
        """
        env = self._get_env()
        template = env.get_template(template_name)
        return template.render(**kwargs)

    def render_help(
        self,
        plugins: list[dict],
        title: str = "Help Menu",
        prefixes: list[str] | None = None,
    ) -> str:
        """Render help menu HTML

        Args:
            plugins: Plugin list
            title: Page title
            prefixes: Command prefix list

        Returns:
            Rendered HTML string
        """
        logger.debug(f"Rendering help with {len(plugins)} plugins")
        if plugins:
            logger.debug(
                f"First plugin: {json.dumps(plugins[0], ensure_ascii=False, default=str)[:500]}"
            )
        return self.render_template(
            "help_template.html",
            plugins=plugins,
            title=title,
            prefixes=prefixes or ["/"],
        )

    def get_theme_css(self) -> str:
        """获取当前主题的 CSS 样式

        Returns:
            CSS 样式字符串
        """
        css_path = self.templates_dir / self._current_theme / "style.css"
        if css_path.exists():
            return css_path.read_text(encoding="utf-8")
        return ""
