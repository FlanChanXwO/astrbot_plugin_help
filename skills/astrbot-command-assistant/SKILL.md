---
name: astrbot-command-assistant
description: 指导 Agent 搜索、解析目标用户并执行 AstrBot 命令；当用户要求查找命令、执行命令、为自己或其他用户调用命令、管理个人用户别名或自定义命令目录时使用。
---

# AstrBot Command Assistant

使用实时 LLM tools，不要根据记忆猜命令、UID、权限或执行结果。

## 标准流程

1. 调用 `search_astrbot_command` 搜索命令；为别人执行时传 `target_user`，偏好只用于排序，不得向请求者泄露目标用户历史。
2. 若目标是昵称、@、回复对象或个人别名，调用 `resolve_astrbot_user`。只有 `resolved` 才能执行；`ambiguous` 必须让用户从候选中确认，禁止猜测。
3. 调用 `execute_astrbot_command`，传完整 `command`；代操作时 **必须** 传解析得到的 `target_ref`，不得只传 `command`。原请求者承担权限检查，输出仍回当前聊天。
4. 根据结构化回执回答，不要把“暂时没有消息”解释为失败。

## 用户引用与别名

- `target_user` 可传 UID、当前消息中的 @、`reply_target`、唯一昵称或 `target_ref`。
- 使用 `set_astrbot_user_alias` 为当前请求者绑定会话内个人别名。
- 使用 `list_astrbot_user_aliases` 查看别名，使用 `delete_astrbot_user_alias` 删除。
- 用户关闭 AI 代操作时，解析可能返回 `unavailable`；不得绕过或要求暴露 UID。
- 用户要求忘记偏好时，引导其使用 `/ai_command_history clear`；管理员可用 `/ai_command_history clear <目标>`。不得把历史明细转述给其他用户。

## 委托策略

- `normal`：允许跨用户，但仍检查请求者权限和目标隐私设置。
- `sensitive`：仅满足管理员、全局敏感委托及目标设置时允许。
- `forbidden`：禁止跨用户委托。

不要从目标用户身份继承管理员权限。不要把目标用户的偏好、频率或历史明细告诉普通请求者。

## 回执与重试

- `completed`：命令已完成，可总结 `messages`。
- `accepted`：长任务已启动。**绝不重试**。
- `external_dispatched`：外部通用路由已受理，即使暂无本地消息也视为已派发。**绝不重试**。
- `duplicate_suppressed`：重复请求被抑制并沿用原回执。**绝不重试**。
- `rejected`：参数、身份、权限或策略拒绝；说明原因，不重试相同调用。
- `failed`：真实执行失败；只有 `retryable=true` 且用户仍需要时才可重试。

## 示例

- 无参数：“帮我打卡” → 搜索 `打卡` → `execute_astrbot_command(command="打卡")`。
- 带参数：“查上海天气” → 搜索天气命令 → 执行返回的完整示例，如 `天气 上海`。
- @ 目标：“给 @橡皮糖 打卡” → `resolve_astrbot_user(reference="@橡皮糖")` → `execute_astrbot_command(command="打卡", target_user="<target_ref>")`。
- 回复目标：“给我回复的这个人打卡” → `resolve_astrbot_user(reference="reply_target")`。
- 昵称：“给橡皮糖打卡” → 先解析昵称；唯一精确匹配才执行。
- 重名：解析返回 `ambiguous` → 展示候选并等待用户确认，不执行命令。

## 自定义目录管理

只有管理员可使用 `list_custom_groups`、`create_custom_group`、`update_custom_group`、`add_custom_group_command`、`update_custom_group_command` 和 `delete_custom_group_command` 修改目录。删除整组必须先调用 `preview_delete_custom_group`，再把返回的 token 原样传给 `confirm_delete_custom_group`；单条命令删除无需确认 token。

创建或更新目录条目时明确提供 `permission_level`、`delegation_policy` 和 `history_mode`。管理员命令至少为 `sensitive`；`sensitive` 或 `forbidden` 不得使用 `history_mode=full`。

更新条目时，省略 `linked_plugin` 会保留原关联。需要清除关联时必须传 `clear_linked_plugin=true`；不要用空字符串或 `null` 猜测清除语义，也不要同时传新的 `linked_plugin`。
