# 项目概述

## 定位

`astrbot_plugin_helpinfo` v2 是 AstrBot 的轻量智能命令代理，负责命令目录、AI 搜索与委托执行、会话身份解析、回执去重和用户偏好；不再生成帮助图片。

## 入口

| 入口 | 说明 |
| --- | --- |
| `search_astrbot_command` | 按权限、目标和偏好搜索命令 |
| `execute_astrbot_command` | 使用原请求者权限在当前聊天调度命令 |
| `resolve_astrbot_user` | 解析 UID、@、引用、别名或唯一昵称 |
| 个人别名 tools | 保存、列出和删除会话内个人别名 |
| 目录 CRUD tools | 管理自定义组和条目 |
| `/ai_command_privacy` | 用户委托隐私设置 |
| `/ai_command_alias` | 聊天内个人别名管理 |
| `/ai_command_history clear [目标]` | 清除本人或管理员指定目标的历史 |
| `/astrbot_plugin_helpinfo/custom-groups` | WebUI 目录 CRUD |
| `/astrbot_plugin_helpinfo/commands` | 分页命令目录和策略更新 |

## 边界

- 不管理插件安装、卸载、启用或禁用。
- 不修改 AstrBot 核心权限，不从目标用户继承管理员身份。
- 不直接向其他用户或会话发消息；输出始终回当前聊天。
- 不保存聊天正文、图片或上下文；身份观察仅保存最小元数据。
- 不使用向量数据库；结构化目录与偏好使用 SQLite。

## v2 破坏性变化

`/helps`、`/help_refresh`、图片模板、字体、JPEG 缓存和浏览器/T2I 依赖已删除。大目录通过 WebUI 分页，Agent 通过搜索和近期/常用偏好逐步发现命令。
