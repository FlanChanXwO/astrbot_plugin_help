"""v2 轻量命令代理的结构回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.application.services.help_service import HelpService
from tests.mocks import MockAstrMessageEvent


ROOT = Path(__file__).parent.parent


def test_image_help_commands_are_removed_from_entrypoint() -> None:
    """入口不应继续暴露图片菜单与图片缓存刷新命令。"""
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert '@filter.command("helps"' not in source
    assert '@filter.command("help_refresh"' not in source
    assert "def show_menu(" not in source
    assert "def refresh_cache(" not in source


def test_rendering_implementation_and_assets_are_removed() -> None:
    """发布物中不再携带浏览器渲染代码、模板或字体资源。"""
    removed_paths = (
        ROOT / "src/infrastructure/rendering",
        ROOT / "src/infrastructure/persistence/cache_manager.py",
        ROOT / "templates",
        ROOT / "resources/fonts",
    )

    assert all(not path.exists() for path in removed_paths)
    assert (
        "playwright"
        not in (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
    )


def test_rendering_configuration_is_no_longer_exposed() -> None:
    """已删除能力不能继续出现在 AstrBot 配置界面。"""
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    assert "rendering" not in schema
    assert "ignored_plugins" not in schema


def test_entrypoint_keeps_web_and_ai_registration() -> None:
    """轻量化不能误删目录 Web API 或 AI tool 注册。"""
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "def _register_web_apis(" in source
    assert "def _register_execute_command_tool(" in source
    assert "def _register_runtime_tools(" in source
    assert "def _register_custom_group_tools(" in source
    assert 'f"/{plugin_name}/custom-groups"' in source
    assert 'f"/{plugin_name}/commands"' in source


def test_runtime_never_uses_plugin_root_data_directory() -> None:
    """运行态只能写 AstrBot 提供的数据目录，不能回写插件根目录。"""
    assert not (ROOT / "data").exists()


@pytest.mark.asyncio
async def test_empty_search_with_preferences_off_rejects_large_catalog() -> None:
    """空关键词关闭偏好时应报错，不能退回输出完整大目录。"""
    service = HelpService.__new__(HelpService)
    event = MockAstrMessageEvent(user_id="10001")

    async def resolve_target(_event, _target_user):
        return ({"status": "resolved"}, "10001")

    service._resolve_target = resolve_target

    result = json.loads(
        await service.search_command(
            event,
            keyword="",
            permission_filter="normal",
            preference_mode="off",
        )
    )

    assert result["success"] is False
    assert "空关键词" in result["error"]
    assert not hasattr(service, "list_all_plugins_and_commands")
