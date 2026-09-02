# hua-quota-monitor

一个本地优先的 Codex 额度监控工具，提供 macOS 菜单栏、多账号管理、Web 趋势面板和命令行查询。

> [!IMPORTANT]
> 本项目是社区工具，与 OpenAI 无隶属或官方合作关系。Codex/ChatGPT 额度查询依赖网页端接口，该接口不是公开 API，可能随时发生变化。

## 功能

- 在 macOS 菜单栏显示主账号剩余额度、重置时间、点数和重置卡。
- 同时监控多个 Codex/ChatGPT 账号，支持设置主账号、重命名和切换登录账号。
- 将每次成功读取的额度写入本地 SQLite，按账号和额度窗口独立保存。
- 提供最近 24 小时的 Web 额度趋势，支持多账号切换。
- 页面优先读取本地缓存，再在后台并行更新实时额度。
- 读取失败时可继续展示 10 分钟内的最后一次成功数据，超时后不再显示。
- 支持查询官方 OpenAI Admin Usage/Costs API。
- 提供 JSON 输出，方便脚本和其他工具集成。

## 运行要求

- Python 3.10 或更高版本。
- macOS 12 或更高版本（仅菜单栏功能需要）。
- 已登录的 Codex CLI 或 ChatGPT/Codex 客户端，默认读取 `~/.codex/auth.json`。
- 推荐安装 [`uv`](https://docs.astral.sh/uv/) 管理虚拟环境。

## 快速开始

```bash
git clone https://github.com/lmwacn/hua-quota-monitor.git
cd hua-quota-monitor
uv venv .venv
uv pip install --python .venv/bin/python pyobjc-framework-Cocoa
```

启动 macOS 菜单栏：

```bash
.venv/bin/python main.py menubar
```

首次启动时，如果存在 `~/.codex/auth.json`，当前账号会自动加入监控。点击菜单栏图标可以刷新所有账号、管理账号或打开额度趋势面板。

如果只需要 Web 面板，不需要安装 PyObjC：

```bash
python main.py dashboard
```

面板默认监听 `127.0.0.1:48763`；端口被占用时会自动尝试后续端口。指定端口或禁止自动打开浏览器：

```bash
python main.py dashboard --port 49173
python main.py dashboard --no-open
```

## 查询 Codex 额度

```bash
python main.py codex
python main.py codex --json
```

程序默认读取 `~/.codex/auth.json`。也可以指定其他登录文件：

```bash
python main.py codex --auth-file /path/to/auth.json
```

输出包括账号实际拥有的额度窗口及重置时间、Codex plan、点数、重置卡数量与到期时间。当接口没有返回主账号的 5 小时窗口时，Web 面板会显示“5 小时·无限额”说明卡片，但不会将其作为采样写入历史。

## 多账号管理

菜单栏“账号监控”中可以：

- 添加当前账号。
- 通过 Codex CLI 的浏览器登录流程添加账号。
- 从其他 `auth.json` 导入账号。
- 设置菜单栏主账号和修改显示名称。
- 切换 ChatGPT/Codex 当前账号并重新打开客户端。

也可以通过命令行管理：

```bash
# 导入当前登录账号，并设为菜单栏主账号
python main.py account import personal --display-name "个人 Pro" --current

# 导入另一个登录文件
python main.py account import work --auth-file /path/to/auth.json

# 查看、选择和重命名账号
python main.py account list
python main.py account use work
python main.py account rename work "工作账号"

# 更新已存在账号的凭据
python main.py account import work --auth-file /path/to/auth.json --replace
```

设置“菜单栏主账号”只改变默认展示，不会切换 ChatGPT 登录。菜单中的“切换到此账号并重启 ChatGPT”才会替换 `~/.codex/auth.json`；切换前会先保存原账号的最新登录态。

## 缓存与历史记录

运行数据默认位于 `~/.hua-quota/`：

```text
~/.hua-quota/
├── accounts/                 # 多账号凭据，目录权限 0700、文件权限 0600
├── state.json                # 主账号与账号索引
└── usage-history.sqlite3     # 额度采样和短期缓存
```

- 每个账号、每个额度窗口分别记录，不会互相混合。
- 每次成功刷新都会留下采样，即使百分比没有变化。
- 失败刷新不会写入历史，也不会延长缓存有效期。
- 缓存有效期为 10 分钟，历史默认保留 30 天。
- SQLite 中不保存 token 或 Cookie。

Web 页面先展示 10 分钟内的 SQLite 快照，然后异步替换为实时结果。额度趋势统一在 Web 面板查看。

## OpenAI Admin API 用量与费用

复制环境变量示例并填写组织 Admin API Key：

```bash
cp .env.example .env
```

常用查询：

```bash
python main.py api --window 5h
python main.py api --window week --group-by model
python main.py api --kind costs --window month
python main.py api --window 2026-07-01..2026-07-02 --bucket 1h
python main.py api --window 5h --group-by model --json
```

`--window` 支持 `5h`、`today`、`week`、`month`、`30d` 或自定义日期范围。日期不带时区时按本机时区解释。

其他 Usage 类型：

```bash
python main.py api --kind usage --usage-type images --window week
python main.py api --kind usage --usage-type web_searches --window today --group-by model
```

可选类型包括 `completions`、`embeddings`、`moderations`、`images`、`audio_speeches`、`audio_transcriptions`、`vector_stores`、`code_interpreter_sessions`、`file_searches` 和 `web_searches`。

## 高级：探测 ChatGPT 网页接口

如果已经从浏览器导出了 Cookie，可以请求指定的 ChatGPT 网页端接口：

```bash
python main.py chatgpt \
  --url "https://chatgpt.com/your-endpoint" \
  --cookie-file ./cookies.txt
```

`cookies.txt` 支持完整 Cookie header 或 Netscape cookie 文件格式。程序会输出原始 JSON，并扫描 `quota`、`limit`、`usage`、`remaining`、`reset` 等疑似额度字段。

## 数据与安全

- Web 服务默认只监听本机回环地址 `127.0.0.1`，不要在不可信网络中改为公开监听地址。
- `auth.json`、`.env`、Cookie 和 `~/.hua-quota/accounts/` 都包含敏感信息，不要提交到 Git、上传日志或发送给他人。
- 网页授权在一次性隔离目录中执行，成功或失败后都会清理；应用不会读取你的账号密码。
- 发现安全问题时，请参阅 [SECURITY.md](SECURITY.md)，不要在公开 Issue 中粘贴凭据。

## 开发

```bash
python -m py_compile main.py src/*.py tests/*.py
python -m unittest discover -s tests -v
```

核心模块：

- `src/quota_service.py`：统一账号额度读取、缓存回退和历史写入。
- `src/usage_monitor.py`：SQLite 采样、缓存和趋势查询。
- `src/account_store.py`：本地多账号凭据管理。
- `src/dashboard_server.py`：Web 面板服务。
- `src/codex_widget.py`：macOS 菜单栏应用。

欢迎提交 Issue 和 Pull Request。参与开发前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[MIT](LICENSE) © 2026 田小檬
