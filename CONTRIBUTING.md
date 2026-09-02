# 贡献指南

感谢你参与 `hua-quota-monitor`。

## 开始开发

1. Fork 仓库并从 `main` 创建分支。
2. 使用 Python 3.10 或更高版本创建虚拟环境。
3. macOS 菜单栏开发需要安装 `pyobjc-framework-Cocoa`。
4. 修改后运行完整测试。

```bash
uv venv .venv
uv pip install --python .venv/bin/python pyobjc-framework-Cocoa
python -m py_compile main.py src/*.py tests/*.py
python -m unittest discover -s tests -v
```

## 提交 Pull Request

- 每个 Pull Request 聚焦一个问题。
- 说明行为变化、验证方式和可能的兼容性影响。
- 功能变化需要同步更新 README 或相关说明。
- 不要提交真实的 `auth.json`、Cookie、API Key、SQLite 数据库或包含账号信息的截图。
- 新增业务逻辑时请补充自动化测试。

## 报告问题

Issue 中请包含运行环境、复现步骤、预期行为和实际行为。日志与截图需要先删除邮箱、账号 ID、token、Cookie 和本机路径等敏感信息。
