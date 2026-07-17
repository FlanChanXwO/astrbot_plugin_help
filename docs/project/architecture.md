# 架构说明

## 模块关系

```text
main.py
  -> src.infrastructure.config.ConfigManager
  -> src.infrastructure.analysis.CommandIndex
  -> src.infrastructure.analysis.CommandAnalyzer
  -> src.infrastructure.analysis.CommandExecutor
  -> src.infrastructure.rendering.HTMLHelpRenderer
  -> src.infrastructure.rendering.HTMLTemplateManager
  -> src.infrastructure.rendering.CacheManager
  -> src.application.services.HelpService / CustomGroupService
       -> AstrBot star_handlers_registry
       -> AstrBot WakingCheckStage + ProcessStage
       -> AstrBot html_render() / t2i
  -> src.infrastructure.logger
```

## 分层架构

插件使用带 DDD 影响的分层架构：

```text
Domain          ->  CommandEntry, RenderNode, PluginCommandSummary (纯 dataclass)
Application     ->  HelpService 命令/渲染编排，CustomGroupService 自定义目录用例
Infrastructure  ->  CommandIndex, analyzers, HTMLHelpRenderer, CacheManager, ConfigManager
```

## `main.py`

`main.py` 保留插件入口职责：

- 初始化路径、`ConfigManager`，并通过 `init_plugin_service()` 按依赖顺序引导所有单例
- 注册 `/helps`（别名 `帮助`）、`/help_refresh`（别名 `刷新帮助缓存`，管理员）
- 注册 LLM tool：命令搜索/执行，以及 `list_custom_groups`、`create_custom_group`、`update_custom_group`、`preview_delete_custom_group`、`confirm_delete_custom_group`、`add_custom_group_command`、`update_custom_group_command`、`delete_custom_group_command` 八项目录管理工具
- 注册 Web API：`/astrbot_plugin_helpinfo/custom-groups`（GET/POST create/update/delete）
- 将事件转发给 `HelpService`

不要把复杂索引、渲染组装或命令执行逻辑重新塞回 `main.py`。

## `src/application/services/help_service.py`

`HelpService` 是中央编排器：

- 桥接 LLM tool、Web API 和基础设施层
- 处理 `/helps` 命令的渲染触发
- 处理 `search_astrbot_command` 的搜索请求
- 处理 `execute_astrbot_command` 的执行请求

## `src/application/services/custom_group_service.py`

`CustomGroupService` 是 Web API 与 AI 目录工具共用的自定义命令组用例：

- 以分组名称及命令触发式为自然键管理目录，持久化成功后才更新内存与失效缓存
- AI 写入仅由入口层的管理员校验放行；普通用户读取自动过滤隐藏和管理员条目
- 整组删除采用预览/一次性 token/确认；配置重载或插件重新初始化会重置服务，使旧 token 失效
- 正则目录项必须可编译，且显式 examples 必须匹配；目录不会创建 AstrBot handler

## `src/infrastructure/analysis/command_index.py`

`CommandIndex` 负责从 AstrBot `star_handlers_registry` 构建命令索引：

- 扫描 `CommandFilter`、`CommandGroupFilter`、`RegexFilter`
- 构建持久化 JSON 缓存到 `commands_cache.json`
- 缓存在已激活 Star 数量变化或自定义命令组修改时失效
- 自定义命令组作为虚拟插件 (`_custom_group_<name>`) 纳入索引
- 正则命令的示例文本通过 `rstr` 从模式自动生成

## `src/infrastructure/analysis/analyzers.py`

`CommandAnalyzer` 负责将扁平命令列表转为 `RenderNode` 树：

- 排序：普通命令 → 正则命令 → 命令组
- 处理组别名、单命令组扁平化和空组占位
- 调用 `_build_plugin_command_tree()` 构建 `RenderNode` 树

## `src/infrastructure/analysis/executor.py`

`CommandExecutor` 负责 AI 命令调度：

- 构建 synthetic `AstrMessageEvent`，先通过 AstrBot 的 `WakingCheckStage` 完成命令匹配和权限过滤
- 默认 `auto` 模式监听本次 synthetic event 最多 3 秒；快速完成时把可归因文本返回 AI，长任务保持后台运行。`background` 立即返回，`custom` 受 60 秒配置上限约束
- 正则命令使用缓存的示例文本或从模式派生的文本作为 `message_str`
- 检测通用处理器（`on_message`、`on_all_message` 等）— 它们匹配几乎所有消息，不代表命令属于特定插件
- 自定义命令组命令只匹配通用处理器且未捕获本地输出时，返回 `external_dispatched`；这表示外部 Bot 路由已受理，AI 应等待当前聊天的异步回复而非重复调用
- 黑名单检查跳过通用处理器，只扫描非通用处理器的 `handler_module_path`
- 阻止递归调用 `execute_astrbot_command`
- `actor=self` 需要显式开启 `enable_ai_self_command`，只改写内部事件发送者为 bot `self_id`，不自动提权

## `src/infrastructure/rendering/html_renderer.py`

`HTMLHelpRenderer` 负责图片渲染：

- 两种后端：Playwright（本地，高质量）vs AstrBot 内置 t2i 服务
- 调用 `HTMLTemplateManager` 获取 Jinja2 模板和 CSS
- 渲染结果为 JPEG，由 `CacheManager` 按配置哈希缓存

## `src/infrastructure/rendering/html_template_manager.py`

`HTMLTemplateManager` 管理 Jinja2 模板：

- 加载 `templates/simple/help_template.html`
- CSS 3 列布局 (`column-count: 3`)
- 提供模板渲染上下文

## `src/infrastructure/rendering/cache_manager.py`

`CacheManager` 管理渲染图片缓存：

- 以配置哈希为键缓存 JPEG
- 配置变更时自动失效

## `src/infrastructure/config/config_manager.py`

`ConfigManager` 负责配置加载和自定义命令组持久化：

- 从 `_conf_schema.json` 加载和归一化配置
- 自定义命令组 CRUD 并持久化到 `custom_groups.json`
- 入口和核心模块应读取已解析后的属性，不要散落直接调用 `config.get(...)`

## 单例模式

几乎所有组件都是模块级单例：

```python
get_help_service(), get_custom_group_service(), get_command_index(), get_command_analyzer()
get_command_executor(), get_html_renderer(), get_cache_manager()
```

测试通过 `reset_*()` 函数重置。`init_plugin_service()` 在 `main.py` 中按依赖顺序引导所有单例。

## 数据流（帮助菜单渲染）

```text
star_handlers_registry -> CommandIndex._build_index() -> _command_cache (dict)
                                                      -> _plugin_cache (PluginCommandSummary)
                            |
                   _apply_custom_groups()  <-  custom_groups.json
                            |
            CommandAnalyzer.analyze_hierarchy()
                            |
            _build_plugin_command_tree()  ->  RenderNode tree
                            |
            HTMLTemplateManager (Jinja2) + HTMLHelpRenderer (Playwright/t2i)
                            |
                       JPEG image -> CacheManager
```

## 自定义命令组

- 用户可通过 WebUI API 或 AI 的八项目录工具定义，持久化到 `custom_groups.json`
- 作为虚拟插件 (`_custom_group_*`) 纳入命令索引
- 可与同名真实插件合并
- 支持 `command` 和 `regex` 类型，正则类型通过 `rstr` 自动生成示例
- 自定义正则命令 `group_name=None`（扁平展示，不按文件夹分组）

## AI Tool 系统

- `search_astrbot_command`：命令发现，使用 jieba 分词、多维评分、权限过滤
- `execute_astrbot_command`：安全调度，含黑名单检查（跳过通用处理器）、递归调用阻止和转发插件检测；初始化时通过 `FunctionTool` 显式注册 JSON Schema，`command(string)` 为必填字段，且即使目标命令无额外参数也要传入完整触发文本；docstring 的 `Args` 仍与函数签名保持同步，供装饰器兼容注册；正则命令使用示例文本作为 `message_str` 以触发 `RegexFilter` 匹配；自定义目录命令由通用路由器受理而未捕获本地输出时返回 `external_dispatched`，表明外部框架会异步回复；只捕获本次 synthetic event，默认最多监听 3 秒，不取消长任务
- 自定义目录工具：`list_custom_groups` 受读取权限过滤；其余七项写工具仅管理员可用，整组删除必须 preview→confirm
- `list_all_plugins_and_commands`：完整命令清单，供 AI 上下文使用

## 安全边界

- 黑名单检查必须跳过通用处理器（`on_message` 等），因为通用处理器匹配几乎所有消息，不代表命令属于黑名单插件
- 递归调用 `execute_astrbot_command` 被阻止
- 自定义普通命令按当前命令前缀与大小写规则识别；自定义正则可使用 `regex:<pattern>` 或实际触发文本。二者即使只匹配到通用处理器也会继续调度且不应被黑名单拦截
- `actor=self` 默认禁用；启用后仅把内部命令事件发送者改为 bot `self_id`，权限仍由 AstrBot 原管道判断，不自动授予管理员权限
