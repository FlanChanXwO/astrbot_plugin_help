# AGENTS.md - astrbot_plugin_helpinfo

本文件只保留协作 agent 的入口规则。项目细节按需阅读 `docs/project/`，开发维护规则优先阅读 `docs/dev/maintenance.md`。

## 沟通语言

- 与用户沟通必须使用中文。

## 项目形态

- 这是 AstrBot 智能命令代理，提供命令目录、AI 搜索/委托执行、身份解析、偏好和自定义目录。
- 插件入口是根目录 `main.py`；可复用逻辑按职责分层放在 `src/`。

主要目录：

- `main.py`: 插件生命周期、命令入口、LLM tool 和 Web API 注册。
- `src/application/services/`: HelpService 编排用例。
- `src/infrastructure/analysis/`: CommandIndex、CommandExecutor。
- `src/infrastructure/storage/`: SQLite catalog 与旧 JSON 迁移。
- `src/infrastructure/config/`: 类型安全配置。
- `pages/dashboard/`: 分页命令目录与策略管理。
- `skills/`: 插件内置 Agent 使用指南。
- `tests/`: 测试入口，conftest.py 含 mock 注入。

## 阅读入口

- 任何改动前先看：`docs/dev/maintenance.md`
- 需要项目背景时看：`docs/project/overview.md`
- 修改架构、编排链路、安全边界或 AI tool 系统时看：`docs/project/architecture.md`
- 修改配置项时同步核对：`_conf_schema.json`、`README.md`、`docs/project/configuration.md`
- 修改测试、lint、贡献流程或工程约束时看：`docs/dev/testing.md`、`docs/dev/contributing.md`、`docs/dev/engineering-principles.md`

## 硬约束

- 不要把复杂目录、身份、历史或命令执行逻辑塞回 `main.py`。
- 不要在插件目录创建或依赖 `<plugin>/data` 作为运行态目录；插件数据目录使用 AstrBot 提供的 `StarTools.get_data_dir()`。
- 黑名单检查必须跳过通用处理器（`on_message` 等）。
- 递归调用 `execute_astrbot_command` 被阻止。
- 自定义命令组命令即使只匹配到通用处理器也不应被黑名单拦截。
- `CustomGroupService` 统一 Web API 与 AI 的 8 项目录工具；配置重载或插件初始化必须重置它，使删除预览 token 失效。
- `execute_astrbot_command` 的默认结果监听窗口由 `ai_command_auto_wait_seconds` 控制（默认 3 秒）；窗口结束不取消后台命令。
- 本插件不提供插件管理功能、不修改 AstrBot 核心行为、不直接发送消息到平台。
- 其他架构细节、配置边界和维护规则不要写进本文件，放到 `docs/project/` 或 `docs/dev/` 对应章节。

## 文档纪律

- 文档不是可选收尾。行为、边界、入口、配置、流程、架构或维护约定变化时，必须同步更新对应 `docs/`。
- 命令行为、配置项、数据库、安全边界、WebUI、测试或 lint 流程变化时更新文档。
- 如果修改 repo-wide 维护规则或 agent 入口约定，同步更新 `AGENTS.md` 和 `CLAUDE.md`。

## 测试与检查命令

从插件目录运行：

```bash
ruff check main.py src tests scripts           # lint
ruff format --check main.py src tests scripts  # 格式检查
python3 -m compileall main.py src tests scripts  # 语法检查
pytest tests/ -v                       # pytest
python3 tests/run_tests.py -v           # 正则示例测试
```

本地集成验证通常需要运行上层 AstrBot 入口：

```bash
cd <AstrBotPluginDev>
python main.py
```

## 更新策略

当架构、命令目录、黑名单、AI tool、SQLite 或测试/lint 流程变化时，同步更新 `CLAUDE.md` 和 `AGENTS.md`。

## 篇幅约束

`AGENTS.md` 和 `CLAUDE.md` 均不得超过 100 行；内容过长时拆入 `docs/dev/` 或 `docs/project/`。
