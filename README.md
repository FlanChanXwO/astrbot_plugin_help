# AstrBot 智能命令代理

`astrbot_plugin_helpinfo` v2 是轻量化的 AI 命令目录、身份解析与委托执行插件。v2 已移除图片帮助菜单，不再依赖浏览器或 T2I 渲染。

## 能力

- 从 AstrBot registry 同步运行时命令，插件加载/卸载时增量更新目录。
- 使用 SQLite `command_catalog.db` 保存自定义目录、权限/委托/历史策略、身份映射、回执和偏好。
- Agent 可搜索并执行命令，也可理解昵称、@、引用、个人别名和会话限定 `target_ref`。
- 3 秒默认监听快速结果；长任务返回 `accepted`，外部路由返回 `external_dispatched`，60 秒内重复调用返回 `duplicate_suppressed`。
- WebUI 和 8 个目录 LLM tools 共用完整分组/条目 CRUD；整组删除必须 preview→confirm。
- 记录目标用户近期/常用命令偏好；默认只记录命令标识，不保存参数。

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/FlanChanXwO/astrbot_plugin_helpinfo.git
cd astrbot_plugin_helpinfo
pip install -r requirements.txt
```

重启 AstrBot 或重载插件。运行态数据始终位于 `StarTools.get_data_dir()` 返回的目录，不写入插件仓库。

## 聊天入口

- `/ai_command_privacy status|allow|deny_sensitive|deny_all`
- 管理员：`/ai_command_privacy set <目标> <allow|deny_sensitive|deny_all>`
- `/ai_command_alias list|set|delete|clear`
- `/ai_command_history clear [目标]`：本人清除自己的明细与聚合；指定目标仅管理员。

v2 删除了 `/helps`、`/help_refresh` 及其中文别名。命令发现改用 LLM tools 或 WebUI 分页目录。

## LLM tools

核心 tools：

- `search_astrbot_command(keyword, permission_filter, target_user, preference_mode)`
- `execute_astrbot_command(command, actor, result_mode, wait_seconds, target_user)`
- `resolve_astrbot_user(reference)`
- `set_astrbot_user_alias`、`list_astrbot_user_aliases`、`delete_astrbot_user_alias`
- 自定义目录 8 项 CRUD tools

`execute_astrbot_command.command` 和 `target_user` 均必填。即使目标命令无额外参数也要传完整触发文本；为请求者本人执行时传 `target_user="requester"`，为他人执行时传昵称、UID、@ 或解析得到的 `target_ref`。`accepted`、`external_dispatched` 和 `duplicate_suppressed` 都表示不得重复调度；只有 `retryable=true` 的 `failed` 才可重试。若 handler 已调度后才失败，回执保持 `failed`，但会返回 `dispatched=true`、`retryable=false` 并参与 60 秒重复抑制。插件内置 Agent 指南见 [`skills/astrbot-command-assistant/SKILL.md`](skills/astrbot-command-assistant/SKILL.md)。

若模型在“@机器人 + 唯一第三方 @”的当前消息中漏传目标，插件会从强身份信号恢复；纯昵称可解析会话内唯一的完整显示名或首段昵称。明确出现第三方委托语义却仍漏参时会拒绝执行，重名或多个目标也不会猜测。

## 配置

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_ai_command_notify` | `true` | 调度前通知当前聊天 |
| `enable_ai_command_result` | `true` | 返回调度状态提示 |
| `enable_ai_self_command` | `false` | 允许 `actor=self`，不自动提权 |
| `ai_command_auto_wait_seconds` | `3` | `auto` 监听窗口，不取消长任务 |
| `ai_command_max_wait_seconds` | `60` | `custom` 最大监听窗口 |
| `enable_sensitive_delegation` | `false` | 全局允许管理员执行敏感委托 |
| `allow_admin_target_override` | `false` | 管理员绕过目标隐私设置 |
| `ai_command_dedupe_window_seconds` | `60` | 重复调度抑制窗口 |
| `command_history_retention_days` | `90` | 历史明细和观察身份保留期 |
| `ai_command_blacklist` | 见 schema | 禁止 AI 调用的插件前缀 |
| `regex.max_examples` | `10` | 正则命令示例生成数量 |

完整语义见 [`docs/project/configuration.md`](docs/project/configuration.md)。

## 数据迁移

启动时会检测旧 `custom_groups.json`，先生成字节级备份，再在事务中严格导入 SQLite；校验失败整批回滚，不跳过坏条目。每个数据库只接受一次旧目录导入：已有 `legacy_imports` 记录后，自动迁移和正式 CLI 都不会因来源或校验和变化而再次导入。dry-run 可验证新来源；正式导入必须使用新建或尚未迁移的数据库：

```bash
python scripts/migrate_custom_groups.py --source /path/custom_groups.json --database /path/command_catalog.db --dry-run
python scripts/migrate_custom_groups.py --source /path/custom_groups.json --database /path/command_catalog.db
```

详细说明见 [`MIGRATION_SUMMARY.md`](MIGRATION_SUMMARY.md)。

## 开发

```bash
ruff check main.py src tests scripts
ruff format --check main.py src tests scripts
python3 -m compileall main.py src tests scripts
pytest tests/ -v
python3 tests/run_tests.py -v
pre-commit run --all-files
```

架构、测试和维护规则位于 [`docs/`](docs/README.md)。项目采用 [MIT](LICENSE) 许可证。
