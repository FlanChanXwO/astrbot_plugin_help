# 项目概述

## 定位

`astrbot_plugin_help` 是 AstrBot 的帮助信息插件，提供可视化帮助菜单和 AI 命令发现/执行能力。

## 能力

- 渲染 JPEG 帮助图片 (`/helps`)
- 命令搜索（jieba 分词 + 多维评分）
- AI 命令执行工具 (`execute_astrbot_command`, `search_astrbot_command`)
- 自定义命令组管理（WebUI API）
- 帮助缓存刷新 (`/help_refresh`，管理员)

## 当前入口

| 入口 | 类型 | 说明 |
|------|------|------|
| `/helps`、`/帮助` | 命令 | 渲染帮助图片 |
| `/help_refresh`、`/刷新帮助缓存` | 命令（管理员） | 刷新命令索引缓存 |
| `search_astrbot_command` | LLM tool | 命令搜索 |
| `execute_astrbot_command` | LLM tool | 命令执行 |
| `list_all_plugins_and_commands` | LLM tool | 命令清单 |
| `/astrbot_plugin_helpinfo/custom-groups` | Web API | 自定义命令组 CRUD |

## 能力边界

- 不提供插件管理功能（安装/卸载/启用/禁用）
- 不修改 AstrBot 核心行为
- 不直接发送消息到平台（除命令执行结果通知外）
- 渲染依赖 AstrBot 的 HTML/T2I 能力或 Playwright

## 关键事实

- 命令索引来源是 AstrBot 的 `star_handlers_registry`，不是手动维护
- 自定义命令组作为虚拟插件纳入索引
- 正则命令示例由 `rstr` 从模式自动生成
- 黑名单检查跳过通用处理器，只针对非通用处理器的 `handler_module_path`
