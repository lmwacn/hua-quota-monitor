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

- 5 小时窗口已用百分比和重置时间
- 周窗口已用百分比和重置时间
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

顶栏显示主额度剩余百分比、点数和按 25 点约折算的美元余额，点击图标可查看主额度、点数余额、重置卡数量和每张重置卡的到期时间；重置时间按本机时区显示具体日期、时间及剩余倒计时：

```bash
uv venv .venv
uv pip install --python .venv/bin/python pyobjc-framework-Cocoa
.venv/bin/python main.py menubar
```

顶栏应用同样会按 `--interval` 自动刷新，退出请从顶栏菜单选择“退出”。
