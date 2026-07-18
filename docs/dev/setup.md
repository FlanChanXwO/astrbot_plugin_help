# 开发环境与本地调试

## 前置要求

- Python 3.10+
- AstrBotPluginDev 本地环境
- 本机可用 `ruff` 与 `pre-commit`

本插件不需要浏览器、字体或 T2I 服务。

## 目录

```text
main.py                         插件入口、hooks、聊天命令、LLM tools、Web API
src/application/services/       目录、身份、委托、历史与回执用例
src/infrastructure/analysis/    CommandIndex、CommandExecutor
src/infrastructure/storage/     SQLite catalog 与旧 JSON 迁移
src/infrastructure/config/      类型安全配置
pages/dashboard/                WebUI 管理页面
skills/                         插件内置 Agent Skill
scripts/                        独立迁移 CLI
tests/                          pytest 与 AstrBot mocks
```

本地集成验证从上层 AstrBotPluginDev 启动：

```bash
cd <AstrBotPluginDev>
python main.py
```

运行态目录由 `StarTools.get_data_dir(plugin_name)` 提供。`command_catalog.db`、旧 `custom_groups.json`、迁移备份和命令索引缓存都位于该数据目录，绝不能写到插件仓库的 `data/`。

旧目录可先 dry-run：

```bash
python scripts/migrate_custom_groups.py --source /path/custom_groups.json --database /path/command_catalog.db --dry-run
```

去掉 `--dry-run` 才正式导入；失败时 CLI 返回非零并报告具体错误，数据库事务回滚。
