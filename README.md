# Help Plugin for AstrBot

<div align="center">

**AstrBot 帮助菜单插件。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A54.10.4-green)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

</div>

---

## ✨ 功能特性

- 🖼️ **可视化帮助菜单** — 将命令列表渲染为精美的 JPEG 图片，支持 3 列瀑布流布局
- 🔍 **智能命令搜索** — 基于 jieba 中文分词的多维度模糊搜索，支持拼音、别名匹配
- 🤖 **AI 命令执行** — 为 LLM 提供 `execute_astrbot_command` 和 `search_astrbot_command` 工具
- 📂 **自定义命令组** — 通过 WebUI 创建虚拟命令组，将任意命令聚合为分组菜单
- 🏷️ **正则命令支持** — 自动识别 RegexFilter 触发器，生成示例文本并正确渲染
- ⚙️ **灵活配置** — 插件黑名单、AI 调用黑名单、渲染引擎选项等丰富的配置项
- 🎨 **双渲染引擎** — 支持 Playwright（本地高质量）或 AstrBot 内置 t2i 服务
- 🧪 **完整测试覆盖** — 40+ 单元测试覆盖核心逻辑

---

## 📦 安装

### 方式一：通过 AstrBot 插件市场安装（推荐）

在 AstrBot 管理面板中搜索 `Help` 并安装。

### 方式二：手动安装

1. 克隆本仓库到 AstrBot 的插件目录：
   ```bash
   cd AstrBot/data/plugins
   git clone https://github.com/FlanChanXwO/astrbot_plugin_helpinfo.git
   ```

2. 安装依赖：
   ```bash
   cd astrbot_plugin_helpinfo
   pip install -r requirements.txt
   ```

3. 重启 AstrBot 或重载插件

---

## 🛠️ 配置项

在 AstrBot 管理面板的「配置」页面，找到 `Help` 插件配置：

### AI 命令配置

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `enable_ai_command_notify` | 布尔值 | AI 执行命令前发送通知 | `true` |
| `enable_ai_command_result` | 布尔值 | AI 命令调度成功/失败发送提示；命令最终输出由后台命令自己发送 | `true` |
| `enable_ai_self_command` | 布尔值 | 允许 AI 使用 `actor=self` 以机器人自身 `self_id` 执行命令，权限仍由 AstrBot 正常判断 | `false` |
| `ai_command_blacklist` | 列表 | 禁止 AI 调用的插件列表 | 见配置说明 |

### 黑名单配置

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `ignored_plugins` | 列表 | 不展示在帮助菜单中的插件 ID | 见配置说明 |

### 正则触发器配置 (`regex`)

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `regex.max_examples` | 整数 | 每条正则命令最多生成的示例数 | `10` |

### 渲染引擎配置 (`rendering`)

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `rendering.use_t2i` | 布尔值 | 使用 AstrBot 内置 t2i 渲染（无需 Playwright） | `false` |
| `rendering.html_theme` | 字符串 | HTML 主题名称，目前仅 `simple` | `simple` |
| `rendering.jpeg_quality` | 整数 | JPEG 图片质量 (50-100) | `95` |
| `rendering.timeout_analysis` | 浮点数 | 数据结构分析超时（秒） | `10.0` |
| `rendering.max_concurrent_tasks` | 整数 | 最大并发渲染数 | `2` |
| `rendering.giant_threshold` | 整数 | 巨型块阈值 (pt)，超过则独占一行 | `1500` |

---

## 📝 使用方法

### 基础命令

| 命令 | 中文别名 | 说明 |
|------|---------|------|
| `/helps [关键词]` | `/帮助` | 显示帮助菜单图片；支持关键词搜索 |
| `/help_refresh` | `/刷新帮助缓存` | 刷新命令缓存（需管理员权限） |

**`/helps` 使用示例：**

| 命令 | 说明 |
|------|------|
| `/helps` | 显示完整帮助菜单 |
| `/helps sub` | 搜索包含 "sub" 的命令 |
| `/helps 订阅` | 中文关键词搜索 |

### 命令菜单结构

帮助菜单按以下规则组织：

1. **插件卡片** — 每个插件独立成卡，显示版本和描述
2. **命令分组** — 原生 `CommandGroupFilter` 分组和自定义命令组均显示为 📂 分组
3. **排序规则** — 普通命令 → 正则命令 → 分组命令，同类型按名称排序
4. **标签显示** — 管理命令标红、正则命令标橙、事件命令标黄
5. **别名展示** — 命令和分组均显示别名，超过 5 个显示 `+N`

---

## 🤖 LLM 工具

本插件为 AI 提供以下工具函数：

- `search_astrbot_command` — 搜索 AstrBot 命令，支持模糊匹配和权限过滤
- `execute_astrbot_command` — 执行 AstrBot 命令，支持普通命令和正则触发命令

在 AstrBot 的 LLM 配置中开启工具调用即可使用。

**AI 工具特性：**
- 自动检测用户权限（管理员可查看所有命令，普通用户仅查看普通命令）
- `execute_astrbot_command` 只返回调度结果，不等待命令最终输出；图片生成等长耗时命令会在后台继续执行并自行把结果发到当前聊天
- `actor=self` 默认禁用；显式开启 `enable_ai_self_command` 后，命令发送者改为机器人 `self_id`，但不会自动提权
- 正则命令自动派生示例文本执行，确保 `RegexFilter` 能正确匹配
- 自定义命令组命令即使只匹配通用处理器也会返回成功（转发命令）
- 黑名单插件自动拦截，防止 AI 调用敏感命令

---

## 🏗️ 项目架构

本项目采用 **分层架构**，受 DDD 设计影响：

```
src/
├── domain/           # 领域层 - 核心业务实体
│   ├── entities/     # 实体 (CommandEntry, RenderNode, PluginCommandSummary)
│   ├── value_objects/# 值对象
│   └── exceptions.py # 领域异常
├── application/      # 应用层 - 用例编排
│   ├── dto/          # 数据传输对象
│   └── services/     # 应用服务 (HelpService)
└── infrastructure/   # 基础设施层 - 技术实现
    ├── analysis/     # 命令分析 (CommandIndex, CommandAnalyzer, CommandExecutor)
    ├── config/       # 配置管理 (ConfigManager)
    ├── persistence/  # 数据持久化 (CacheManager)
    ├── rendering/    # 图片渲染 (HTMLHelpRenderer, TemplateManager)
    └── utils/        # 工具函数
```

### 关键设计原则

1. **单例模式** — 核心组件均为模块级单例，通过 `get_*()` 访问，测试通过 `reset_*()` 重置
2. **依赖注入** — `init_plugin_service()` 按依赖顺序引导所有单例
3. **缓存策略** — 命令索引持久化到 JSON，按已激活 Star 数量自动失效
4. **类型安全** — 全面使用 Python 类型注解

---

## 🌐 WebUI（自定义命令组）

通过 AstrBot 管理面板的「页面」入口访问：

- **创建分组** — 将任意命令聚合为虚拟插件分组
- **支持类型** — 普通命令（`command`）和正则命令（`regex`）
- **别名管理** — 为分组和命令设置别名
- **权限控制** — 支持标记管理命令
- **隐藏命令** — 可将命令标记为隐藏，不出现在帮助菜单中

WebUI 数据持久化到 `data/plugin_data/astrbot_plugin_help/data/custom_groups.json`。

---

## 📄 开源协议

本项目基于 [MIT](LICENSE) 协议开源。

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
