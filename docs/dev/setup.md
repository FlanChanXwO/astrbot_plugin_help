# 开发环境与本地调试

## 前置要求

- Python 3.10+
- AstrBotPluginDev 本地开发环境
- 推荐使用 `uv`

## 可选渲染依赖

帮助图片运行时依赖 AstrBot 的 HTML/T2I 渲染能力或 Playwright。开发和排查模板问题时，建议准备本地 T2I 服务：

```text
http://localhost:8999/text2img
```

Playwright 不是插件运行时依赖（t2i 为推荐后端），但属于模板视觉回归和 issue 复现的辅助工具。

## 本地代码位置

插件通常位于：

```text
AstrbotPluginDev/data/plugins/astrbot_plugin_helpinfo
```

## 常用目录

```text
main.py                         # 插件入口（HelpPlugin Star 子类），注册命令/LLM tool/Web API
_conf_schema.json               # 配置 schema
src/application/services/       # HelpService 编排用例
src/infrastructure/analysis/    # CommandIndex、CommandAnalyzer、CommandExecutor
src/infrastructure/rendering/   # HTMLHelpRenderer、HTMLTemplateManager、CacheManager
src/infrastructure/config/      # ConfigManager 配置与自定义命令组持久化
src/domain/entities/            # CommandEntry、RenderNode、PluginCommandSummary
templates/simple/               # Jinja2 帮助模板与 CSS
tests/                          # 测试入口，conftest.py 含 mock 注入
docs/                           # 项目和开发文档
```

## 运行与调试原则

### 本地集成验证

项目运行通常启动 AstrBotPluginDev 顶层入口，而不是直接运行插件目录内的文件。

本地工作区常见入口：

```bash
python /Users/flanchan/Development/SourceCode/GithubProjects/AstrbotPluginDev/main.py
```

### 数据目录

不要把运行时数据写回插件仓库下的 `data/`。

本插件通过 AstrBot 提供的 `StarTools.get_data_dir(self.name)` 取得插件数据目录。自定义命令组持久化文件 `custom_groups.json` 和命令索引缓存 `commands_cache.json` 均存放在该目录。

### 启动结构

入口和编排链路见 [`../project/architecture.md`](../project/architecture.md)。本地调试时不要把复杂索引、渲染组装或命令执行逻辑重新塞回 `main.py`。

## AstrBot 插件基础

插件是 "Star"，继承自 `Star`（`astrbot/core/star/base.py`）：

- `initialize()` 在激活时调用；`terminate()` 在停用/重载时调用
- 处理器通过装饰器注册：`@filter.command()`、`@filter.llm_tool()`、`@filter.on_message()`
- 配置 schema 放在 `_conf_schema.json`
- 会话配置通过 `context.get_config(umo)` 获取（`umo` = `platform:msg_type:session_id`）
- 持久数据存放在 `data/`，不放在插件源码目录

### Tool 定义（v4.5.7+）

优先使用 dataclass 模式：

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
# 注册: self.context.add_llm_tools(MyTool())
```

### 两层 Hook（不要混淆）

- **插件事件 hook**：Star 方法上的装饰器（`@filter.command`、`@filter.on_*`）
- **Agent runner hook**：`BaseAgentRunHooks`，用于拦截 agent 执行

## 测试与检查命令

命令清单统一维护在 [`testing.md`](./testing.md)，本文件不重复列出。
