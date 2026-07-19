# 架构说明

## 模块关系

```text
main.py
  -> HelpService（入口编排）
     -> CommandRuntimeService（SQLite 生命周期与目录同步）
     -> DelegatedCommandService（委托策略、回执和历史）
     -> IdentityService（观察与身份解析）
     -> CommandIndex / CommandExecutor（registry 搜索与 synthetic 调度）
  -> CustomGroupService（Web API / LLM tool 共用 CRUD）
  -> CommandCatalog（SQLite）
```

`main.py` 只注册生命周期、聊天命令、LLM tools 和 Web API。复杂目录、身份、历史、策略和执行逻辑留在 `src/application/services/` 与 `src/infrastructure/`。

## SQLite

数据库位于 `StarTools.get_data_dir()` 下的 `command_catalog.db`，启用 WAL、外键和显式事务。其表覆盖：

- runtime/custom 命令、别名、示例、组关系与策略；
- schema/migration 状态；
- 会话参与者、个人别名和隐私设置；
- 执行回执、明细历史与长期聚合。

启动时严格导入旧 `custom_groups.json`：先备份源字节并记录校验和；每个数据库一旦有 `legacy_imports` 记录就不再接受自动迁移或正式 CLI 覆盖，非法数据则回滚整批事务。dry-run 可验证任意新来源，正式导入须使用新建或尚未迁移的数据库。runtime 命令不迁移旧缓存，按 registry 重建。生命周期 hook 在插件加载时增量同步，卸载时删除 runtime 行；custom 行保留，关联缺失插件标为 `missing_plugin`。

## 身份解析

群消息观察器只保存平台、会话、UID、显示名、最后活跃时间及 @ 对象，并跳过 synthetic event。解析优先级是 UID/@ → 唯一引用 → 个人别名 → 实时成员唯一精确名称 → 90 天观察快照。名称使用 NFKC、空白归一化与 casefold；重名只返回候选，opaque `target_ref` 限定当前会话。

用户 `deny_all` 后仍可显示为不可操作，但不返回 `target_ref`、偏好或历史。管理员管理命令可用 `resolve_for_management` 定位目标，但不会把 opaque ref 或完整 UID返回给模型。

## 调度与回执

`execute_astrbot_command` 保留原请求者的权限、管理员角色和插件可用范围；跨用户时仅把 synthetic sender 改为目标。通用处理器按 filter 类型识别。自定义目录只进入外部通用路由时返回 `external_dispatched`。

Agent 为他人执行时必须传 `target_user`。为防模型漏参造成命令落到请求者本人，当前消息同时 `@机器人` 且只有一个非请求者第三方 `@` 时，执行链会恢复该强身份目标；昵称文本、多目标等弱信号不自动推断。

终态包括 `completed`、`accepted`、`external_dispatched`、`duplicate_suppressed`、`rejected` 和 `failed`。前四种已调度状态不得重复调用；handler 已调度后才失败时仍保留 `failed` 语义，但以 `dispatched=true`、`retryable=false` 参与重复抑制。去重键由会话、请求者、目标和规范化命令组成。只把前三种写入目标用户历史。

委托策略：`normal` 允许跨用户；`sensitive` 还需管理员、全局开关和目标同意；`forbidden` 禁止跨用户。管理员命令最低自动提升为 `sensitive`，插件更新只向更严格方向提升。

## 搜索和偏好

基础相关度至少占 80%，目标偏好最多贡献 20%，且不能越过精确匹配或权限边界。第三方搜索只做盲提升，不返回统计。空关键词只允许本人或管理员读取 recent/frequent；`preference_mode=off` 明确拒绝，不回退输出完整目录。

## 自定义目录

SQLite 是唯一运行时权威源。普通/正则条目严格校验触发式和 examples；读取按权限过滤。整组删除 preview→confirm，单条删除直接执行。WebUI 和 8 项目录 tools 共用 `CustomGroupService`。

## 安全边界

- 黑名单只检查非通用处理器，避免通用路由误杀。
- 阻止递归调用 `execute_astrbot_command`。
- `actor=self` 默认关闭，开启也不自动提权。
- 输出始终留在当前聊天，不直接联系目标用户。
- 本插件不包含图片渲染、模板、字体或浏览器运行时。
