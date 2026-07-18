"""插件内置 Agent Skill 的发现形状与真实工具契约。"""

from pathlib import Path


def test_plugin_skill_has_discoverable_frontmatter_and_real_tools():
    skill_path = (
        Path(__file__).parent.parent
        / "skills"
        / "astrbot-command-assistant"
        / "SKILL.md"
    )
    content = skill_path.read_text(encoding="utf-8")

    assert content.startswith("---\nname: astrbot-command-assistant\n")
    assert "description:" in content.split("---", 2)[1]
    for tool in (
        "search_astrbot_command",
        "resolve_astrbot_user",
        "execute_astrbot_command",
        "set_astrbot_user_alias",
        "list_astrbot_user_aliases",
        "delete_astrbot_user_alias",
        "preview_delete_custom_group",
        "confirm_delete_custom_group",
    ):
        assert f"`{tool}`" in content
    for state in (
        "completed",
        "accepted",
        "external_dispatched",
        "duplicate_suppressed",
        "rejected",
        "failed",
    ):
        assert f"`{state}`" in content
    assert content.count("绝不重试") >= 3
