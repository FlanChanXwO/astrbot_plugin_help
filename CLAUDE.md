# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **`astrbot_plugin_help`** — an AstrBot plugin that provides:
- A visual help menu rendered as JPEG images (`/helps`)
- Command search with jieba tokenization and multi-dimensional scoring
- AI command execution tools (`execute_astrbot_command`, `search_astrbot_command`)
- Custom command groups managed via WebUI

It runs as an AstrBot "Star" plugin. AstrBot is a multi-platform LLM chatbot framework.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt   # rstr, jieba, pydantic, playwright

# Run tests
pytest                            # pytest with mocked AstrBot deps
python tests/run_tests.py         # Standalone regex example tests
python tests/run_tests.py -v      # Verbose output

# Clear persistent cache for fresh state
rm -f data/plugin_data/astrbot_plugin_helpinfo/cache/commands_cache.json
```

## Architecture

The plugin uses a **layered architecture** with DDD influences:

```
Domain          →  CommandEntry, RenderNode, PluginCommandSummary (pure dataclasses)
Application     →  HelpService orchestrates use cases
Infrastructure  →  CommandIndex, analyzers, HTMLHelpRenderer, CacheManager, ConfigManager
```

**Data Flow (Help Menu Rendering)**:
```
star_handlers_registry → CommandIndex._build_index() → _command_cache (dict)
                                                       → _plugin_cache (PluginCommandSummary)
                             ↓
                    _apply_custom_groups()  ←  custom_groups.json
                             ↓
              CommandAnalyzer.analyze_hierarchy()
                             ↓
              _build_plugin_command_tree()  →  RenderNode tree
                             ↓
              HTMLTemplateManager (Jinja2) + HTMLHelpRenderer (Playwright/t2i)
                             ↓
                        JPEG image → CacheManager
```

### Key Components

- **`CommandIndex`** (`src/infrastructure/analysis/command_index.py`): Scans AstrBot's `star_handlers_registry` to build a persistent JSON cache of all commands. Extracts `CommandFilter`, `CommandGroupFilter`, `RegexFilter`. Supports custom groups as virtual plugins (`_custom_group_<name>`). Cache invalidates on activated star count change or custom groups modification.

- **`CommandAnalyzer`** (`src/infrastructure/analysis/analyzers.py`): Converts flat command lists into `RenderNode` trees. Sorts output: normal commands → regex commands → command groups. Handles group aliases, single-command group flattening, and empty group placeholders.

- **`CommandExecutor`** (`src/infrastructure/analysis/executor.py`): Executes commands via the `execute_astrbot_command` LLM tool. Builds a synthetic `AstrMessageEvent`, runs it through AstrBot's `WakingCheckStage` + `ProcessStage`. For regex commands, derives the actual message text from cached examples or the pattern itself. Detects generic handlers, forwarding plugins, and custom group commands.

- **`HelpService`** (`src/application/services/help_service.py`): Central orchestrator bridging LLM tools, web APIs, and infrastructure.

### Singleton Pattern (Critical)

Nearly every component is a module-level singleton:
```python
get_help_service(), get_command_index(), get_command_analyzer()
get_command_executor(), get_html_renderer(), get_cache_manager()
```
Tests reset these via `reset_*()` functions. `init_plugin_service()` in `main.py` bootstraps all singletons in dependency order.

### Custom Command Groups

- User-defined via WebUI APIs, persisted to `data/plugin_data/astrbot_plugin_help/data/custom_groups.json`
- Applied as virtual plugins (`_custom_group_*`) in the command index
- Can merge with real plugins if names match
- Support both `command` and `regex` types with auto-generated examples via `rstr`
- Custom regex commands have `group_name=None` (flat display, not grouped as folders)

### AI Tool System

- `search_astrbot_command`: Command discovery with jieba tokenization, multi-dimensional scoring, permission filtering
- `execute_astrbot_command`: Safe execution with blacklist check, recursive call blocking, and forwarding plugin detection. Regex commands use example text as `message_str` so `RegexFilter` can match
- `list_all_plugins_and_commands`: Full command inventory for AI context

### Rendering Pipeline

- Jinja2 template at `templates/simple/help_template.html`
- CSS-based 3-column layout (`column-count: 3`)
- Two backends: Playwright (local, higher quality) vs AstrBot built-in t2i service
- Caches rendered JPEGs by config hash

## AstrBot Plugin Basics

Plugins are "Stars" that inherit from `Star` (`astrbot/core/star/base.py`):
- `initialize()` called on activation; `terminate()` on deactivation/reload
- Handlers registered via decorators: `@filter.command()`, `@filter.llm_tool()`, `@filter.on_message()`
- Config schema in `_conf_schema.json`
- Session config via `context.get_config(umo)` (`umo` = `platform:msg_type:session_id`)
- Store persistent data in `data/`, not the plugin source directory

**Tool Definition (v4.5.7+)** — prefer dataclass pattern:
```python
from pydantic.dataclasses import dataclass
from astrbot.core.agent.tool import FunctionTool

@dataclass
class MyTool(FunctionTool):
    name: str = "my_tool"
    description: str = "..."
    parameters: dict = {...}
    async def call(self, context, **kwargs) -> str:
        return "result"
# Register: self.context.add_llm_tools(MyTool())
```

**Two Hook Layers** (do not mix up):
- Plugin event hooks: decorators on Star methods (`@filter.command`, `@filter.on_*`)
- Agent runner hooks: `BaseAgentRunHooks` for intercepting agent execution

## Key File Paths

- `main.py` — Plugin entry point (`HelpPlugin` Star subclass), registers commands/LLM tools/Web APIs
- `_conf_schema.json` — Plugin configuration schema (AI blacklist, regex settings, rendering options)
- `src/application/services/help_service.py` — Main orchestrator
- `src/infrastructure/analysis/command_index.py` — Command indexing from `star_handlers_registry`
- `src/infrastructure/analysis/analyzers.py` — Command/event/filter analysis, tree building
- `src/infrastructure/analysis/executor.py` — AI command execution pipeline
- `src/infrastructure/rendering/html_renderer.py` — Playwright/t2i image rendering
- `src/infrastructure/config/config_manager.py` — Config + custom groups persistence
- `src/domain/entities/command.py` — `CommandEntry`, `MatchedHandlerInfo`
- `src/domain/entities/plugin.py` — `PluginCommandSummary`, `RenderNode`
- `templates/simple/help_template.html` — Jinja2 template for help images
- `tests/conftest.py` — Pytest fixtures with mocked AstrBot deps via `sys.modules`

## Conventions

- **Formatting**: Ruff only (`ruff check .` and `ruff format .` before PR)
- **Async**: Use `async def` for all handlers/hooks/tools. Use `aiohttp` or `httpx`, never `requests`
- **Type hints**: Add for public methods and hook signatures
- **Config**: Never hardcode secrets; expose in `_conf_schema.json`
- **Plugin size**: Keep under 32MB. Use CDN for large resources
- **Testing**: Tests mock AstrBot modules via `sys.modules` injection in `conftest.py`
