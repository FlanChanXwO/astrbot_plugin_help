"""Mock HTML Renderer for testing."""

from __future__ import annotations

from pathlib import Path


class MockHtmlRenderer:
    """模拟 HTML 渲染器."""

    def __init__(self):
        self.render_calls: list[dict] = []
        self.theme = "simple"
        self._closed = False

    async def render(
        self,
        plugins: list[dict],
        output_path: Path,
        title: str = "Help Menu",
        prefixes: list[str] | None = None,
    ) -> list[str]:
        """模拟渲染."""
        self.render_calls.append(
            {
                "plugins": plugins,
                "output_path": output_path,
                "title": title,
                "prefixes": prefixes,
            }
        )
        # 返回模拟的图片路径
        return [str(output_path)] if output_path else ["/tmp/test_help.jpg"]

    def set_theme(self, theme: str) -> None:
        """设置主题."""
        self.theme = theme

    async def close(self) -> None:
        """关闭渲染器."""
        self._closed = True

    def get_render_calls(self) -> list[dict]:
        """获取渲染调用记录."""
        return self.render_calls

    def was_called(self) -> bool:
        """检查是否被调用过."""
        return len(self.render_calls) > 0
