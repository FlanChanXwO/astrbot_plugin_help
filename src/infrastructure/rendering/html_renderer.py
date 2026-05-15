"""HTML + Playwright/T2I Help Image Renderer - Singleton Pattern

使用 Jinja2 模板引擎生成 HTML，然后通过 Playwright 或 AstrBot 内置 t2i 服务渲染为图片。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ...infrastructure.config import get_config
from ..utils.logger import get_logger
from ..utils.paths import get_plugin_dir

logger = get_logger()

# Configuration constants for max_concurrent_tasks validation
MIN_CONCURRENT_TASKS = 1  # Minimum to prevent blocking
MAX_CONCURRENT_TASKS = 20  # Maximum to prevent overload


class HTMLHelpRenderer:
    """HTML 帮助渲染器

    支持两种渲染方式：
    1. Playwright 本地渲染（需要安装 playwright）
    2. AstrBot 内置 t2i 服务渲染（无需额外依赖）
    """

    def __init__(self):
        """初始化 HTML 渲染器"""
        from .template_manager import HTMLTemplateManager

        self.config = get_config()
        self.plugin_dir = get_plugin_dir()
        self.template_manager = HTMLTemplateManager()

        # 渲染信号量：支持并发渲染以提高性能
        # 验证并限制并发数，防止配置错误导致系统过载
        max_concurrent = self.config.rendering.max_concurrent_tasks
        if max_concurrent < MIN_CONCURRENT_TASKS:
            logger.warning(
                f"max_concurrent_tasks is {max_concurrent}, clamping to {MIN_CONCURRENT_TASKS} to prevent blocking"
            )
            max_concurrent = MIN_CONCURRENT_TASKS
        elif max_concurrent > MAX_CONCURRENT_TASKS:
            logger.warning(
                f"max_concurrent_tasks is {max_concurrent}, clamping to {MAX_CONCURRENT_TASKS} to prevent overload"
            )
            max_concurrent = MAX_CONCURRENT_TASKS

        self._render_semaphore = asyncio.Semaphore(max_concurrent)

        # Playwright 浏览器实例（延迟初始化）
        self._browser = None
        self._playwright = None

    def use_t2i(self) -> bool:
        """是否使用 t2i 服务"""
        return self.config.rendering.use_t2i

    async def _get_browser(self):
        """获取或创建 Playwright 浏览器实例

        注意：当 use_t2i=True 时，此方法返回 None，不会启动 Playwright
        """
        if self.use_t2i():
            logger.debug("t2i 模式已启用，跳过 Playwright 浏览器初始化")
            return None

        if self._browser is None:
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch()
                logger.debug("Playwright 浏览器已启动")
            except ImportError as e:
                from ...domain.exceptions import RenderError

                raise RenderError(
                    "Playwright 未安装，无法使用浏览器渲染。"
                    "请运行 'pip install playwright' 和 'playwright install' 安装，"
                    "或在插件配置中开启 use_t2i 使用内置渲染服务"
                ) from e
            except Exception as e:
                from ...domain.exceptions import RenderError

                raise RenderError(
                    f"Playwright 浏览器启动失败: {str(e)}。"
                    f"请运行 'playwright install' 安装浏览器，"
                    f"或在插件配置中开启 use_t2i 使用内置渲染服务"
                ) from e

        return self._browser

    async def close(self):
        """关闭浏览器资源"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
            logger.info("Playwright 浏览器已关闭")

    async def render(
        self,
        plugins: list[dict],
        output_path: Path,
        title: str = "帮助菜单",
        prefixes: list[str] | None = None,
    ) -> list[str]:
        """渲染帮助图片

        Args:
            plugins: 插件数据列表
            output_path: 输出图片路径
            title: 页面标题
            prefixes: 命令前缀列表

        Returns:
            生成的图片路径列表
        """
        async with self._render_semaphore:
            try:
                # 1. 渲染 HTML 模板
                html_content = self.template_manager.render_help(
                    plugins=plugins,
                    title=title,
                    prefixes=prefixes,
                )

                # 2. 选择渲染方式
                if self.use_t2i():
                    logger.debug("使用 AstrBot 内置 t2i 服务渲染")
                    image_data = await self._render_with_t2i(html_content)
                else:
                    logger.debug("使用 Playwright 本地渲染")
                    image_data = await self._render_html_to_jpeg(html_content)

                # 3. 保存图片
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(image_data)

                return [str(output_path)]

            except Exception as e:
                logger.error(f"HTML 渲染失败: {e}", exc_info=True)
                from ...domain.exceptions import RenderError

                raise RenderError(f"HTML 渲染失败: {str(e)}")

    async def _render_with_t2i(self, html_content: str) -> bytes:
        """使用 AstrBot t2i 服务渲染 HTML 为图片

        Args:
            html_content: HTML 内容（已渲染的完整 HTML）

        Returns:
            图片二进制数据
        """
        try:
            from astrbot.core import html_renderer
        except ImportError:
            from ...domain.exceptions import RenderError

            raise RenderError(
                "无法导入 AstrBot t2i 服务。请确保使用 AstrBot v4.5.0+ 版本，"
                "或在插件配置中关闭 use_t2i 使用 Playwright 渲染"
            )

        try:
            # 使用 render_custom_template 渲染完整的 HTML
            # html_content 已经是渲染好的 HTML，直接作为模板传递，数据为空
            result_path = await html_renderer.render_custom_template(
                tmpl_str=html_content,
                tmpl_data={},  # HTML 已渲染，不需要额外数据
                return_url=False,  # 返回文件路径而不是 URL
                options={
                    "full_page": True,
                    "type": "jpeg",
                    "quality": self.config.rendering.jpeg_quality,
                },
            )

            # result_path 是本地文件路径
            if isinstance(result_path, str):
                return Path(result_path).read_bytes()
            else:
                from ...domain.exceptions import RenderError

                raise RenderError(f"t2i 服务返回未知类型: {type(result_path)}")

        except Exception as e:
            logger.error(f"t2i 渲染失败: {e}", exc_info=True)
            from ...domain.exceptions import RenderError

            raise RenderError(
                f"t2i 渲染失败: {str(e)}\n"
                f"请确保 AstrBot 已正确启动并启用 t2i 服务，\n"
                f"或在插件配置中关闭 use_t2i 使用 Playwright 渲染"
            ) from e

    async def _render_html_to_jpeg(self, html_content: str) -> bytes:
        """使用 Playwright 将 HTML 渲染为 JPEG 图片

        Args:
            html_content: HTML 内容

        Returns:
            JPEG 图片二进制数据
        """
        browser = await self._get_browser()

        # 如果浏览器为 None（说明 t2i 模式已启用），不应调用此方法
        if browser is None:
            from ...domain.exceptions import RenderError

            raise RenderError(
                "Playwright 渲染不可用：当前配置已开启 use_t2i，"
                "请确保 t2i 服务可用，或关闭 use_t2i 配置以使用 Playwright 渲染"
            )

        # 创建新页面
        page = await browser.new_page()

        try:
            # 设置页面内容
            await page.set_content(html_content, wait_until="networkidle")

            # 等待字体和图片加载：使用 DOM 信号而不是固定延迟，减少在慢环境中的不稳定性
            # 这比固定延迟更可靠，能够根据实际资源加载情况动态调整
            await page.wait_for_function(
                """
                async () => {
                    try {
                        // 等待字体加载（如果浏览器支持 document.fonts）
                        if (document.fonts && document.fonts.ready) {
                            await document.fonts.ready;
                        }
                    } catch (e) {
                        // 忽略字体等待失败，避免整个渲染流程崩溃
                    }

                    const images = Array.from(document.images || []);
                    if (images.length === 0) {
                        return true;
                    }

                    // 等待所有图片加载完成或失败，并对单张图片设置超时时间
                    await Promise.all(
                        images.map((img) => {
                            if (img.complete) {
                                return Promise.resolve();
                            }
                            return new Promise((resolve) => {
                                const done = () => {
                                    img.removeEventListener('load', done);
                                    img.removeEventListener('error', done);
                                    resolve();
                                };
                                img.addEventListener('load', done, { once: true });
                                img.addEventListener('error', done, { once: true });

                                // 防止极端情况下一直不返回
                                setTimeout(done, """
                + str(self.config.rendering.render_image_timeout)
                + """);
                            });
                        })
                    );

                    return true;
                }
                """,
                timeout=self.config.rendering.render_wait_timeout,
            )

            # 获取页面实际高度
            page_height = await page.evaluate("document.body.scrollHeight")
            page_width = await page.evaluate("document.body.scrollWidth")

            # 设置视口大小
            await page.set_viewport_size(
                {
                    "width": page_width,
                    "height": page_height,
                }
            )

            # 截图
            screenshot_bytes = await page.screenshot(
                type="jpeg",
                quality=self.config.rendering.jpeg_quality,
                full_page=True,
            )

            return screenshot_bytes

        finally:
            await page.close()

    def set_theme(self, theme_name: str) -> bool:
        """设置主题"""
        return self.template_manager.set_theme(theme_name)

    def get_current_theme(self) -> str:
        """获取当前主题"""
        return self.template_manager.get_current_theme()

    def get_available_themes(self) -> list[str]:
        """获取可用主题列表"""
        return self.template_manager.get_available_themes()


# 单例实例
_renderer_instance: HTMLHelpRenderer | None = None


def get_html_renderer() -> HTMLHelpRenderer:
    """获取 HTML 渲染器单例。

    Returns:
        HTMLHelpRenderer 实例
    """
    global _renderer_instance
    if _renderer_instance is None:
        _renderer_instance = HTMLHelpRenderer()
    return _renderer_instance


def reset_html_renderer() -> None:
    """重置 HTML 渲染器（用于测试）。"""
    global _renderer_instance
    _renderer_instance = None
