# GPT 订阅/用量查询

这个工具分两条线：

1. 官方 OpenAI API 用量和费用：使用 OpenAI Admin API Key 查询，稳定可靠。
2. ChatGPT 网页订阅额度：使用本机登录态请求你指定的网页端接口，作为探测器预留。网页端接口不是公开 API，字段和地址可能变化。

## 准备

```bash
cp .env.example .env
```

然后把 `.env` 里的 `OPENAI_ADMIN_KEY` 换成你的 Admin API Key。Admin Key 可在 OpenAI Platform 组织设置里创建。

## 查询最近 5 小时 API 用量和费用

```bash
python main.py api --window 5h
```

## 查询本周，并按模型分组

```bash
python main.py api --window week --group-by model
```

## 查询本月费用

```bash
python main.py api --kind costs --window month
```

## 查询其他 Usage 类型

```bash
python main.py api --kind usage --usage-type images --window week
python main.py api --kind usage --usage-type audio_transcriptions --window 30d
python main.py api --kind usage --usage-type web_searches --window today --group-by model
```

可选类型包括：`completions`、`embeddings`、`moderations`、`images`、`audio_speeches`、`audio_transcriptions`、`vector_stores`、`code_interpreter_sessions`、`file_searches`、`web_searches`。

## 输出原始 JSON

```bash
python main.py api --window 5h --group-by model --json
```

## 自定义日期范围

```bash
python main.py api --window 2026-07-01..2026-07-02 --bucket 1h
```

日期不带时区时按本机时区解释。

## ChatGPT 网页端登录态探测

如果你从浏览器导出了 Cookie，可以保存成 `cookies.txt`。支持两种格式：

- 一整行 Cookie header：`a=b; c=d`
- Netscape cookie 文件格式

然后请求你抓到的 ChatGPT 网页端接口：

```bash
python main.py chatgpt \
  --url "https://chatgpt.com/某个你抓到的接口" \
  --cookie-file ./cookies.txt
```

工具会返回原始 JSON，并自动扫描 `quota`、`limit`、`usage`、`remaining`、`reset` 等疑似额度字段。

注意：ChatGPT 网页端额度接口不是官方公开 API，可能会变化。不要把 cookie、token、响应日志上传到公开仓库。

## 查询 Codex 额度

项目默认读取当前 ChatGPT/Codex 客户端的用户级登录文件 `~/.codex/auth.json`，可以直接运行：

```bash
python main.py codex
```

它会读取 Codex 本机登录态，请求 `https://chatgpt.com/backend-api/wham/usage`，显示：

- 账号实际拥有的 5 小时、周或月度额度窗口及重置时间
- Codex plan type
- reset credits 数量
- reset credits 明细和到期时间
- 附加额度窗口

输出原始 JSON：

```bash
python main.py codex --json
```

如果登录态过期，先在 ChatGPT 客户端重新登录，或运行 `codex login`。也可以用 `--auth-file` 指定其他登录文件。

## 启动可视化面板

```bash
python main.py dashboard
```

默认监听 `127.0.0.1:48763`。如果端口被占用，会自动在后续端口里找一个可用端口。

也可以指定端口：

```bash
python main.py dashboard --port 49173
```

## 启动 macOS 顶栏额度显示

顶栏显示主额度剩余百分比、点数和按 25 点约折算的美元余额。点击图标后，主账号名称后会显示最近刷新的小时和分钟；重置卡数量合并在“重置卡到期时间”二级菜单标题中。重置时间按本机时区显示具体日期、时间及剩余倒计时：

```bash
uv venv .venv
uv pip install --python .venv/bin/python pyobjc-framework-Cocoa
.venv/bin/python main.py menubar
```

顶栏应用同样会按 `--interval` 自动刷新，退出请从顶栏菜单选择“退出”。
每次刷新时，如果当前 `~/.codex/auth.json` 能匹配已管理账号，顶栏应用会自动将可能已刷新的登录凭据同步回该账号。

额度读取成功时，程序会将每个账号、每个额度窗口的百分比和采样时间记录到 `~/.hua-quota/usage-history.sqlite3`。即使百分比没有变化，每次成功刷新也会留下独立采样。读取失败时会继续显示最后一次成功数据，并标记为缓存；缓存有效期为 10 分钟，超时后不再显示百分比。失败刷新不会续期缓存，也不会写入历史采样。数据库不保存 token 或 Cookie，历史默认保留 30 天。

顶栏的“额度消耗监控”会显示本周期已用、距上次采样和最近 1 小时的消耗变化。选择顶栏菜单的“打开额度趋势面板”会自动启动 Web 服务，并打开当前账号最近 24 小时的消耗趋势。如果顶栏使用了自定义 `--account-store`，面板会自动使用同一数据库。
当主额度接口不返回 5 小时窗口时，Web 面板会显示“5 小时·无限额”卡片；该卡片只是界面说明，不会作为虚假采样写入额度历史。
Web 面板加载时会先显示 10 分钟内的本地 SQLite 快照，再在后台并行更新额度和重置卡数据；实时结果返回后页面会自动替换，无需手动刷新。
已导入的多个账号会出现在 Web 面板顶部的账号选择器中，默认选中顶栏主账号。切换只会查看相应账号的额度和历史，不会切换 ChatGPT 当前登录账号。
额度窗口卡片的剩余百分比只在右上角显示一次，底部将重置倒计时和时间压缩为单行。

### 顶栏监控多个账号

可以直接在顶栏的“管理账号”二级菜单操作：

- “添加当前账号…”：为当前 `~/.codex/auth.json` 输入一个别名并保存；如果该账号已存在，可直接更新其凭据。
- “网页登录添加账号…”：打开 Codex 官方浏览器登录，授权成功后自动导入，不覆盖当前账号。
- “从 auth.json 导入…”：通过 macOS 文件选择框选择其他账号的登录文件。
- “打开账号存储目录”：在 Finder 中查看本机凭据目录。

账号的详情二级菜单还可以“设为顶栏主账号”和“重命名账号…”。显示名支持中文、英文、数字和空格，顶栏的“顶栏主账号”会显示这个名称。

网页授权使用 [`codex login` 官方流程](https://learn.chatgpt.com/docs/auth)，顶栏不读取你的账号密码。授权会在一次性隔离目录中进行，完成或失败后均会清理；由于浏览器可能已登录 ChatGPT，请在授权页确认选中的账号。

也可以使用命令行。先把当前 ChatGPT/Codex 登录态导入为一个账号：

```bash
python main.py account import personal --display-name "个人 Pro" --current
```

切换到另一个 ChatGPT 账号并完成登录后，再导入一次：

```bash
python main.py account import work
python main.py account list
python main.py account rename work "工作账号"
```

如果已经准备了其他 `auth.json`，可以直接指定：

```bash
python main.py account import backup --auth-file /path/to/auth.json
```

再启动顶栏，“账号监控”二级菜单会按接口返回的窗口时长，动态展示每个账号实际拥有的 5 小时、周或月度额度，以及点数、重置卡和更新时间。当前顶栏主账号每 `--interval` 秒刷新，全部账号最快每 5 分钟刷新，也可以手动选择“刷新全部账号”。每个已管理账号的成功刷新都会以独立账号 ID 写入额度历史数据库，不会与其他账号混合。

在非当前账号的详情菜单中选择“切换到此账号并重启 ChatGPT”，工具会：

1. 请求 ChatGPT 正常退出，不会强制结束进程。
2. 保存原账号的最新登录态。
3. 原子替换 `~/.codex/auth.json`，然后重新打开 ChatGPT。

账号凭据默认保存在 `~/.hua-quota/accounts/`，目录权限为 `0700`，凭据文件权限为 `0600`。这些文件包含可用于登录的 token，不要上传、提交到 Git 或发送给他人。长时间没有使用的账号可能会显示登录态失效，需要重新登录后用 `--replace` 导入：

```bash
python main.py account import work --replace
```
