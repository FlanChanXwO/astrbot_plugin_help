# AGENTS.md - astrbot_plugin_helpinfo

本文件只保留协作 agent 的入口规则。项目细节按需阅读 `docs/project/`，开发维护规则优先阅读 `docs/dev/maintenance.md`。

## 沟通语言

- 与用户沟通必须使用中文。

## 项目形态

- 这是一个 AstrBot 帮助信息插件，提供可视化帮助菜单、命令搜索和 AI 命令发现/执行能力。
- 插件入口是根目录 `main.py`；可复用逻辑按职责分层放在 `src/`。
- 渲染模板和 CSS 资源放在 `templates/`。

主要目录：

- `main.py`: 插件生命周期、命令入口、LLM tool 和 Web API 注册。
- `src/application/services/`: HelpService 编排用例。
- `src/infrastructure/analysis/`: CommandIndex、CommandAnalyzer、CommandExecutor。
- `src/infrastructure/rendering/`: HTMLHelpRenderer、HTMLTemplateManager、CacheManager。
- `src/infrastructure/config/`: ConfigManager 配置与自定义命令组持久化。
- `src/domain/entities/`: CommandEntry、RenderNode、PluginCommandSummary。
- `templates/`: Jinja2 帮助模板与 CSS。
- `tests/`: 测试入口，conftest.py 含 mock 注入。

## 阅读入口

- 任何改动前先看：`docs/dev/maintenance.md`
- 需要项目背景时看：`docs/project/overview.md`
- 修改架构、编排链路、安全边界或 AI tool 系统时看：`docs/project/architecture.md`
- 修改配置项时同步核对：`_conf_schema.json`、`README.md`、`docs/project/configuration.md`
- 修改测试、lint、贡献流程或工程约束时看：`docs/dev/testing.md`、`docs/dev/contributing.md`、`docs/dev/engineering-principles.md`

## 硬约束

- 不要把复杂索引、渲染组装或命令执行逻辑重新塞回 `main.py`。
- 不要在插件目录创建或依赖 `<plugin>/data` 作为运行态目录；插件数据目录使用 AstrBot 提供的 `StarTools.get_data_dir()`。
- 黑名单检查必须跳过通用处理器（`on_message` 等）。
- 递归调用 `execute_astrbot_command` 被阻止。
- 自定义命令组命令即使只匹配到通用处理器也不应被黑名单拦截。
- 本插件不提供插件管理功能、不修改 AstrBot 核心行为、不直接发送消息到平台。
- 其他架构细节、配置边界和维护规则不要写进本文件，放到 `docs/project/` 或 `docs/dev/` 对应章节。

## 文档纪律

- 文档不是可选收尾。行为、边界、入口、配置、流程、架构或维护约定变化时，必须同步更新对应 `docs/`。
- 命令行为、配置项、黑名单规则、渲染模板、安全边界、测试或 lint 流程变化时，通常需要更新文档。
- 如果修改 repo-wide 维护规则或 agent 入口约定，同步更新 `AGENTS.md` 和 `CLAUDE.md`。

## 测试与检查命令

从插件目录运行：

```bash
ruff check main.py src tests           # lint
ruff format --check main.py src tests  # 格式检查
python3 -m compileall main.py src tests  # 语法检查
pytest tests/ -v                       # pytest
python tests/run_tests.py -v           # 正则示例测试
```

本地集成验证通常需要运行上层 AstrBot 入口：

```bash
cd /Users/flanchan/Development/SourceCode/GithubProjects/AstrbotPluginDev
python main.py
```

## 更新策略

当架构、命令索引、黑名单规则、AI tool 语义、渲染流程或测试/lint 流程变化时，同步更新 `CLAUDE.md` 和 `AGENTS.md`。

## 篇幅约束

`AGENTS.md` 和 `CLAUDE.md` 均不得超过 100 行；内容过长时拆入 `docs/dev/` 或 `docs/project/`。
