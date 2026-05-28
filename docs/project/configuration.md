# 配置说明

配置真源是根目录 `_conf_schema.json`。修改配置字段时必须同步更新 README、本文档和相关测试。

## 字段

### 通用设置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_ai_command_notify` | `bool` | `true` | AI 执行命令前发送通知（"正在执行命令: <command>"）。 |
| `enable_ai_command_result` | `bool` | `true` | AI 执行命令结束后发送成功/失败结果提示。 |
| `ai_command_blacklist` | `list` | `["astrbot", "astrbot-web-searcher", "astrbot-python-interpreter", "session_controller", "builtin_commands", "astrbot-reminder"]` | 禁止 AI 通过 `execute_astrbot_command` 调用的插件列表，使用 `startswith` 匹配。 |
| `ignored_plugins` | `list` | `["astrbot", ..., "astrbot_plugin_help"]` | 帮助菜单中屏蔽的插件列表，使用 `name` 字段匹配。 |

### 正则触发器设置

| 字段 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `regex.max_examples` | `int` | `10` | 1–20 | 每条正则最多生成的示例数，按简单→复杂排序，超出上限只保留前 N 条。 |

### 渲染引擎设置

| 字段 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `rendering.use_t2i` | `bool` | `true` | — | 使用 AstrBot 内置 t2i 渲染，无需 Playwright（推荐）。 |
| `rendering.html_theme` | `string` | `"simple"` | — | HTML 主题名称，目前仅支持 `simple`。 |
| `rendering.jpeg_quality` | `int` | `95` | 50–100 | JPEG 图片质量，数值越高质量越好但文件越大。 |
| `rendering.timeout_analysis` | `float` | `10.0` | 3–30 | 生成数据结构的超时秒数，根据插件数量调整。 |
| `rendering.max_concurrent_tasks` | `int` | `2` | 1–20 | 同时进行的渲染任务数量限制。 |
| `rendering.giant_threshold` | `int` | `1500` | 100–3000 | 超过此高度的插件独占一行显示（仅 Event/Filter 模式有效）。 |
| `rendering.render_wait_timeout` | `int` | `10000` | 5000–30000 | 等待字体和图片加载的最大毫秒数。 |
| `rendering.render_image_timeout` | `int` | `5000` | 1000–10000 | 单张图片的最大加载毫秒数，超时后继续渲染。 |

## 配置维护规则

- README 中的配置表必须和 `_conf_schema.json` 保持一致。
- 运行时代码通过 `ConfigManager` 读取配置，业务流程不要直接散落调用 `config.get(...)`。
- 删除、重命名或改变字段类型时，必须说明兼容影响。
- `ai_command_blacklist` 使用 `startswith` 匹配 `handler_module_path`；黑名单检查跳过通用处理器，因为通用处理器匹配几乎所有消息，不代表命令属于特定插件。
- `ignored_plugins` 使用插件的 `name` 字段匹配。
- `regex.max_examples` 控制正则命令示例生成数量，示例由 `rstr` 从模式自动生成。
