# 配置说明

配置真源是根目录 `_conf_schema.json`。

| 字段 | 默认值 | 语义 |
| --- | --- | --- |
| `enable_ai_command_notify` | `true` | 调度前通知当前聊天 |
| `enable_ai_command_result` | `true` | 输出调度结果提示 |
| `enable_ai_self_command` | `false` | 允许 `actor=self`；不自动提权 |
| `ai_command_auto_wait_seconds` | `3` | `auto` 同步监听窗口，结束不取消任务 |
| `ai_command_max_wait_seconds` | `60` | `custom` 单次监听上限 |
| `enable_sensitive_delegation` | `false` | 全局允许敏感跨用户委托 |
| `allow_admin_target_override` | `false` | 管理员绕过目标委托隐私 |
| `ai_command_dedupe_window_seconds` | `60` | 同会话/请求者/目标/命令去重窗口 |
| `command_history_retention_days` | `90` | 明细历史与观察身份保留期 |
| `ai_command_blacklist` | 见 schema | 禁止 AI 调度的插件前缀 |
| `regex.max_examples` | `10` | 正则条目示例生成数量 |

等待窗口只限制 tool 同步等待，不是执行取消超时。敏感委托默认全局关闭；即使开启，也必须同时满足原请求者为管理员且目标允许。历史默认只保存命令标识；只有普通、明确安全且 `history_mode=full` 的条目保存完整调用。

v2 不再识别旧 `rendering` 和 `ignored_plugins` 配置，它们可从部署配置中删除。图片帮助能力已经移除；AI 调度黑名单 `ai_command_blacklist` 继续有效。

自定义目录条目的 `linked_plugin` 是可选生命周期关联。LLM 更新工具省略该字段时保持现值；显式清除必须传 `clear_linked_plugin=true`，清除后 `availability` 恢复为 `available`。该布尔字段与新的非空 `linked_plugin` 互斥，空字符串不会被解释为清除。
