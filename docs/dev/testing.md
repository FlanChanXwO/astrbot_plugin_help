# 测试与回归检查

从插件目录执行：

```bash
ruff check main.py src tests scripts
ruff format --check main.py src tests scripts
python3 -m compileall main.py src tests scripts
pytest tests/ -v
python3 tests/run_tests.py -v
pre-commit run --all-files
git diff --check
```

测试使用 `tests/conftest.py` 注入 AstrBot mock；新增 AstrBot API 依赖时同步 mock。WebUI 改动除静态断言外还需在 AstrBot page bridge 或等价 mock 中实际验证字段、分页与删除确认。

高风险路径必须覆盖：迁移事务、生命周期同步、身份歧义/隐私、跨用户权限、通用外部路由、3 秒/60 秒监听、60 秒去重、偏好排序与历史清除。结构回归还应确认发布物没有旧图片菜单、模板、字体或浏览器依赖，测试后未生成插件根 `data/`。
