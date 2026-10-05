# Windows 托盘与额度悬浮条

需 Python 3.10+（包含 Tk）、Windows 10/11。安装与启动见根目录 README。安装完成后也可双击项目中的 `启动Windows悬浮条.vbs`，不显示控制台。

- 默认显示当前登录账号；已有多账号配置时使用所选主账号。
- 额度显示剩余百分比，默认每 60 秒后台刷新；查询失败时沿用现有 10 分钟缓存规则，并标注缓存。
- 拖动悬浮条调整位置，松手保存到 `~/.hua-quota/windows-widget.json`。
- 右键悬浮条或托盘图标可刷新、打开 Web 面板、移动到任务栏左右、隐藏或退出；双击悬浮条打开面板。
- 托盘图标可能收纳到右下角的小箭头内，可在系统设置中选择显示。
- 悬浮条是置顶独立窗口，覆盖任务栏时不会为其他图标预留空间。默认位置按主任务栏计算，可能需手动调整；任务栏自动隐藏时不会自动跟随隐藏。
- 不自动设置开机启动。启动使用 pythonw 可避免控制台窗口。
- Windows 文件锁使用 msvcrt；Windows 文件权限由用户目录 ACL 管理，chmod 不提供 Unix 权限隔离效果。

命令参数：`python main.py widget --interval 60 --auth-file ~/.codex/auth.json --account-store ~/.hua-quota`。
