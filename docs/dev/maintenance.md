# 维护规则

本文面向维护者和协作 agent，记录开发时需要遵守的仓库级规则。业务细节不要继续塞进 `AGENTS.md` 或 `CLAUDE.md`，应拆到 `docs/project/` 的对应主题文档。

## 文档同步

- 文档不是可选收尾。
- 行为、边界、入口、配置、流程、架构或维护约定变化时，必须同步更新对应文档。
- 下列变化默认需要更新文档：
  - 命令行为或参数变化
  - 配置项、默认值或兼容规则变化
  - 命令索引结构或缓存失效策略变化
  - 渲染模板、资源路径或安全边界变化
  - AI tool 语义、黑名单规则或执行管道变化
  - 自定义命令组 CRUD 或正则示例生成变化
  - 测试、lint 或本地验证流程变化
- 修改 repo-wide 维护规则或 agent 入口约定时，同步更新 `AGENTS.md` 和 `CLAUDE.md`。

## 入口与模块边界

入口与模块事实统一维护在 [`../project/architecture.md`](../project/architecture.md)。本文件只记录维护要求：

- `main.py` 只负责插件入口、生命周期和编排；复杂索引、渲染组装和命令执行逻辑放在 `src/` 对应模块。
- 不要把 `CommandIndex`、`CommandAnalyzer`、`CommandExecutor` 或 `HTMLHelpRenderer` 的内部逻辑重新塞回 `main.py`。

## 本地路径

- 插件数据目录由 AstrBot `StarTools.get_data_dir(self.name)` 提供。
- 不要在插件目录创建或依赖 `<plugin>/data` 作为运行态目录。
- `custom_groups.json` 和 `commands_cache.json` 均存放在插件数据目录。

## 配置维护

- 配置字段语义见 [`../project/configuration.md`](../project/configuration.md)。
- `_conf_schema.json` 字段新增、删除、重命名或类型变化时，必须同步 README、配置文档和相关回归测试。
- 不在维护文档里复制完整配置表。

## 单例重置

几乎所有组件都是模块级单例（`get_help_service()` 等）。测试通过 `reset_*()` 函数重置。修改单例初始化顺序时注意 `init_plugin_service()` 的依赖引导。

## 测试与检查

常用命令见 [`testing.md`](./testing.md)。涉及下列行为时，优先补回归测试：

- 黑名单检查跳过通用处理器
- 递归调用阻止
- 自定义命令组黑名单豁免
- 正则命令示例生成与 `RegexFilter` 匹配
- `_conf_schema.json` 字段兼容
- `RenderNode` 树构建（单命令扁平化、组别名、空组占位）

## 已移除或不属于本插件的能力

本插件不提供插件管理功能（安装/卸载/启用/禁用）、不修改 AstrBot 核心行为、不直接发送消息到平台。修改相关入口前先确认是否真的属于本插件职责。
