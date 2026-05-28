# 测试与回归检查

## 基础命令

在插件目录运行测试与检查：

| 场景 | 工作目录 | 命令 |
| --- | --- | --- |
| Python lint | 插件目录 | `ruff check main.py src tests` |
| Python 格式检查 | 插件目录 | `ruff format --check main.py src tests` |
| 语法检查 | 插件目录 | `python3 -m compileall main.py src tests` |
| pytest | 插件目录 | `pytest tests/ -v` |
| 正则示例测试 | 插件目录 | `python tests/run_tests.py -v` |

> [!TIP]
> 这些是测试与检查命令，不是插件的独立运行命令。实际集成验证入口见 [`setup.md`](./setup.md#本地集成验证)。

## 分层验证矩阵

| 改动类型 | 最小检查 | 建议额外回归 | 关注点 |
| --- | --- | --- | --- |
| Python 业务逻辑 | `ruff check`、语法检查、相关测试 | 命令路径和 tool handler 路径 | 不要只跑被改函数附近的测试。 |
| 渲染模板 / 资源 | `ruff check`、语法检查 | `/helps` 手工生成帮助图片 | 关注 T2I、字体内联、CSS 3 列布局。 |
| 配置 schema | `ruff check`、相关配置测试 | README 与配置文档同步核对 | 旧配置是否还能被容忍。 |
| 命令索引 / 黑名单 | `ruff check`、相关测试 | AI 执行实际命令验证 | 通用处理器跳过、递归阻止、转发检测。 |
| 命令搜索 | `ruff check`、相关测试 | jieba 分词和评分验证 | 覆盖中文和英文查询。 |
| 自定义命令组 | `ruff check`、相关测试 | Web API CRUD + 正则示例生成 | `rstr` 示例、虚拟插件合并。 |

## 高风险改动清单

| 改动 | 风险 | 建议 |
| --- | --- | --- |
| 黑名单检查逻辑 | 误杀合法命令（如通用处理器误匹配） | 覆盖通用处理器跳过、自定义命令组豁免。 |
| `CommandExecutor` 执行管道 | 递归调用或命令注入 | 覆盖递归阻止和空命令边界。 |
| 正则命令示例生成 | `rstr` 生成的示例无法触发 `RegexFilter` | 补示例到缓存的端到端测试。 |
| `RenderNode` 树构建 | 帮助图片布局错乱或空组占位 | 覆盖单命令扁平化和组别名。 |
| HTML/CSS 模板 | 图片或 3 列布局异常 | 手工验证 T2I 输出。 |
| 自定义命令组 CRUD | `custom_groups.json` 损坏或虚拟插件索引错 | 覆盖创建/更新/删除和真实插件合并。 |
| `_conf_schema.json` 字段 | 旧配置兼容 | 同步 README 和配置文档。 |

## 测试 Mock 说明

`tests/conftest.py` 通过 `sys.modules` 注入 AstrBot 模块 mock。这意味着：

- 测试运行不需要真实 AstrBot 环境
- mock 模块列表需要随 AstrBot API 变化同步更新
- 新增对 AstrBot 内部 API 的依赖时，需在 `conftest.py` 补充对应 mock
