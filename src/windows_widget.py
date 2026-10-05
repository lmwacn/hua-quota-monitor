from __future__ import annotations

import json
import math
import os
import queue
import threading
import webbrowser
from pathlib import Path

from src.account_store import AccountStore
from src.dashboard_server import create_dashboard_server
from src.quota_service import QuotaService
from src.rate_windows import rate_limit_windows
from src.usage_monitor import UsageMonitor


def quota_text(result: dict) -> str:
    usage = result.get("usage")
    if usage is None:
        return "Codex · 查询失败（右键查看详情）"
    parts = []
    for label, window in rate_limit_windows(usage.get("rate_limit") or {}):
        try:
            used = float(window["used_percent"])
            if not math.isfinite(used):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        parts.append(f"{label} {max(0, min(100, 100-used)):.0f}%")
    text = "Codex · " + (" ｜ ".join(parts) or "暂无额度窗口")
    return text + (" · 缓存" if result.get("usage_cached") else "")


def run_widget(*, auth_file: Path, base_url: str, store_dir: Path, interval: int) -> int:
    if os.name != "nt":
        print("widget 模式仅支持 Windows。")
        return 2
    try:
        import tkinter as tk
        from tkinter import messagebox
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("请先安装 Windows 界面依赖：python -m pip install -r requirements-windows.txt")
        return 2
    import ctypes
    from ctypes import wintypes

    store = AccountStore(root=store_dir, canonical_auth_path=auth_file)
    service = QuotaService(auth_file=auth_file, base_url=base_url,
                           monitor=UsageMonitor(store_dir / "usage-history.sqlite3"),
                           account_store=store)
    root = tk.Tk()
    root.title("Codex 额度")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.96)
    label = tk.Label(root, text="Codex · 正在读取额度…", bg="#182332", fg="#82e3bc",
                     font=("Microsoft YaHei UI", 10), padx=12, pady=5, cursor="fleur")
    label.pack()
    events = queue.Queue()
    settings = store_dir / "windows-widget.json"
    busy = False
    closed = False
    last_error = "暂无错误。"
    server = None
    dashboard_url = None
    drag = None

    def place(side="right", *, save=True):
        root.update_idletasks()
        rect = wintypes.RECT()
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        taskbar = user32.FindWindowW("Shell_TrayWnd", None)
        width, height = root.winfo_width(), root.winfo_height()
        if taskbar and user32.GetWindowRect(taskbar, ctypes.byref(rect)) and rect.right-rect.left > rect.bottom-rect.top:
            x = rect.left + 90 if side == "left" else rect.right - width - 340
            y = rect.top + max(0, (rect.bottom-rect.top-height)//2)
        else:
            x = 90 if side == "left" else root.winfo_screenwidth()-width-340
            y = root.winfo_screenheight()-height-70
        root.geometry(f"+{max(0,x)}+{max(0,y)}")
        root.deiconify()
        if save:
            save_position()

    def save_position():
        settings.write_text(json.dumps({"x": root.winfo_x(), "y": root.winfo_y()}), encoding="utf-8")

    def toggle():
        if root.state() == "withdrawn":
            root.deiconify()
        else:
            root.withdraw()

    def refresh():
        nonlocal busy
        if busy:
            return
        busy = True
        def worker():
            try:
                # Respect the selected monitor account; otherwise read current auth directly.
                profile = store.get_current() or service.canonical_profile()
                result = service.fetch_profile(profile, active_name=store.detect_active_name())
                events.put(("result", result))
            except Exception as exc:
                events.put(("result", {"usage": None, "error": str(exc)}))
        threading.Thread(target=worker, daemon=True, name="windows-quota").start()

    def open_dashboard():
        nonlocal server, dashboard_url
        try:
            if server is None:
                server, dashboard_url = create_dashboard_server(host="127.0.0.1", port=48763,
                    auth_file=auth_file, base_url=base_url, store_dir=store_dir)
                threading.Thread(target=server.serve_forever, daemon=True).start()
            webbrowser.open(dashboard_url)
        except Exception as exc:
            messagebox.showerror("无法打开面板", str(exc), parent=root)

    def quit_app():
        nonlocal closed
        closed = True
        save_position()
        icon.stop()
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()
        root.destroy()

    actions = {"toggle": toggle, "refresh": refresh, "dashboard": open_dashboard,
               "left": lambda: place("left"), "right": place, "quit": quit_app,
               "error": lambda: messagebox.showinfo("刷新详情", last_error, parent=root)}
    menu = tk.Menu(root, tearoff=False)
    for title, action in [("刷新额度", "refresh"), ("打开详细面板", "dashboard"),
                          ("移到任务栏左侧", "left"), ("移到任务栏右侧", "right"),
                          ("隐藏悬浮条", "toggle"), ("刷新详情", "error"), ("退出", "quit")]:
        menu.add_command(label=title, command=actions[action])
    label.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
    label.bind("<Double-Button-1>", lambda e: open_dashboard())

    def press(event):
        nonlocal drag
        drag = (event.x_root-root.winfo_x(), event.y_root-root.winfo_y())
    def move(event):
        if drag:
            root.geometry(f"{event.x_root-drag[0]:+d}{event.y_root-drag[1]:+d}")
    def release(event):
        nonlocal drag
        drag = None
        save_position()
    label.bind("<Button-1>", press)
    label.bind("<B1-Motion>", move)
    label.bind("<ButtonRelease-1>", release)
    image = Image.new("RGBA", (64, 64), "#182332")
    draw = ImageDraw.Draw(image)
    draw.arc((10, 10, 54, 54), 35, 325, fill="#82e3bc", width=8)
    draw.ellipse((27, 27, 37, 37), fill="#82e3bc")
    def callback(action):
        return lambda icon, item: events.put(("action", action))
    icon = pystray.Icon("hua-quota-monitor", image, "Codex 额度监控", pystray.Menu(
        pystray.MenuItem("显示 / 隐藏悬浮条", callback("toggle"), default=True),
        pystray.MenuItem("刷新额度", callback("refresh")),
        pystray.MenuItem("打开详细面板", callback("dashboard")),
        pystray.MenuItem("移到任务栏左侧", callback("left")),
        pystray.MenuItem("移到任务栏右侧", callback("right")),
        pystray.MenuItem("退出", callback("quit"))))
    threading.Thread(target=icon.run, daemon=True, name="windows-tray").start()

    place(save=False)
    try:
        position = json.loads(settings.read_text(encoding="utf-8"))
        x, y = int(position["x"]), int(position["y"])
        # Keep saved positions only when visible on a connected display.
        monitor_point = wintypes.POINT(x, y)
        ctypes.windll.user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        ctypes.windll.user32.MonitorFromPoint.restype = wintypes.HANDLE
        if ctypes.windll.user32.MonitorFromPoint(monitor_point, 0):
            root.geometry(f"{x:+d}{y:+d}")
    except (OSError, ValueError, KeyError, TypeError):
        pass

    def poll():
        nonlocal busy, last_error
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == "action":
                actions[value]()
                if closed:
                    return
            else:
                busy = False
                text = quota_text(value)
                label.config(text=text)
                icon.title = text[:127]
                last_error = value.get("error") or "最近一次刷新成功。"
        root.after(100, poll)

    def tick():
        refresh()
        root.after(max(15, interval)*1000, tick)
    root.after(100, poll)
    tick()
    try:
        root.mainloop()
    finally:
        icon.stop()
    return 0
