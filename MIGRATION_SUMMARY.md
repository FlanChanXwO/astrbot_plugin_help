# v2 SQLite 迁移说明

## 自动迁移

v2 首次启动会在 AstrBot 插件数据目录创建 `command_catalog.db`，然后检查旧 `custom_groups.json`：

1. 读取并严格校验完整源文件；
2. 在同目录写入带时间戳的字节级备份；
3. 在一个事务内导入分组、命令、别名、示例和策略；
4. 记录源文件 SHA-256 校验和与导入报告；
5. 任一条目非法则回滚整批导入，不静默跳过。

每个数据库只接受一次旧目录导入：一旦存在 `legacy_imports` 记录，自动迁移和正式 CLI 都返回 `already_migrated`，即使来源路径或校验和后来改变，也不会覆盖可编辑的 SQLite 自定义目录。CLI 的 dry-run 仍可严格验证新来源；如需正式导入该来源，目标必须是新建或尚无迁移记录的数据库。

旧 runtime 命令缓存不会导入，命令会从 AstrBot registry 重新同步。插件卸载只删除 runtime 命令；custom 条目永不随插件卸载删除，显式关联缺失插件时标记为 `missing_plugin`。

## 独立 CLI

```bash
python scripts/migrate_custom_groups.py \
  --source /path/custom_groups.json \
  --database /path/command_catalog.db \
  --dry-run
```

dry-run 只校验并输出报告，不创建备份、不写数据库。确认目标是新建或尚无 `legacy_imports` 记录的数据库后执行：

```bash
python scripts/migrate_custom_groups.py \
  --source /path/custom_groups.json \
  --database /path/command_catalog.db
```

CLI 以 JSON 输出状态、校验和、组数、条目数、备份路径或具体错误；失败返回非零退出码。

## v2 破坏性变化

图片帮助菜单、刷新入口、模板、字体、JPEG 缓存以及浏览器/T2I 依赖已移除。旧 `rendering` 和 `ignored_plugins` 配置不再使用。命令发现改由 LLM tools 与 WebUI 分页目录承担。
