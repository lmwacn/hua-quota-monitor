from __future__ import annotations

import json
import math
import os
import queue
import threading
import webbrowser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from src.windows_menu import Entry, quota_entries

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
    root.attributes("-alpha", 0.88)
    root.configure(bg="#222b38")
    label = tk.Label(root, text="Codex · 正在读取额度…", bg="#222b38", fg="#eef2f7",
                     font=("Microsoft YaHei UI", 10), padx=12, pady=4, cursor="arrow")
    label.pack(side="left")
    events = queue.Queue()
    settings = store_dir / "windows-widget.json"
    try:
        position = json.loads(settings.read_text(encoding="utf-8"))
        if not isinstance(position, dict):
            position = {}
    except (OSError, ValueError):
        position = {}
    locked = position.get("locked", True) is not False
    label.config(cursor="arrow" if locked else "fleur")
    from src.windows_appearance import round_window
    rounded_size = None
    def update_shape():
        nonlocal rounded_size
        size = (root.winfo_width(), root.winfo_height())
        if size != rounded_size:
            rounded_size = size
            round_window(root, radius=6)
    root.bind("<Configure>", lambda event: root.after_idle(update_shape) if event.widget is root else None)
    busy = False
    closed = False
    last_error = "暂无错误。"
    server = None
    dashboard_url = None
    poll_timer = None
    tick_timer = None
    drag = None
    click_origin = None
    dragged = False
    results = {}
    profiles = store.list_accounts()
    selected = None
    main_result = None

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
        settings.write_text(json.dumps({"x": root.winfo_x(), "y": root.winfo_y(), "locked": locked}), encoding="utf-8")

    def toggle_lock():
        nonlocal locked, drag
        locked = not locked
        drag = None
        label.config(cursor="arrow" if locked else "fleur")
        save_position()
        icon.update_menu()

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
                current_profiles = store.list_accounts()
                current = store.get_current()
                active = store.detect_active_name()
                targets = current_profiles or [service.canonical_profile()]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    fetched = list(pool.map(lambda profile: service.fetch_profile(profile, active_name=active), targets))
                events.put(("batch", (current_profiles, current.name if current else None, fetched)))
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
        for timer in (poll_timer, tick_timer):
            if timer is not None:
                root.after_cancel(timer)
        save_position()
        icon.stop()
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()
        root.destroy()

    def select_account(name):
        nonlocal selected, main_result
        try:
            store.set_current(name)
            selected = name
            main_result = results.get(name)
            if main_result:
                label.config(text=quota_text(main_result))
            refresh()
        except Exception as exc:
            messagebox.showerror("设置失败", str(exc), parent=root)

    def rename_account(name):
        from tkinter import simpledialog
        display = simpledialog.askstring("重命名账号", "显示名称：", parent=root,
                                        initialvalue=store.get_account(name).display_name)
        if display:
            try:
                store.set_display_name(name, display)
                refresh()
            except Exception as exc:
                messagebox.showerror("重命名失败", str(exc), parent=root)

    def import_account(current=False):
        from tkinter import filedialog, simpledialog
        path = str(auth_file) if current else filedialog.askopenfilename(parent=root,
            title="选择 auth.json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        display = simpledialog.askstring("添加账号", "账号显示名称：", parent=root)
        if not display:
            return
        try:
            names = {p.name for p in store.list_accounts()}
            index = 1
            while f"account-{index}" in names:
                index += 1
            store.import_account(f"account-{index}", path, display_name=display, make_current=not names)
            refresh()
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=root)

    actions = {"toggle": toggle, "refresh": refresh, "dashboard": open_dashboard,
               "left": lambda: place("left"), "right": place, "quit": quit_app,
               "lock": toggle_lock, "add": lambda: import_account(True),
               "import": import_account,
               "error": lambda: messagebox.showinfo("刷新详情", last_error, parent=root)}

    def entries():
        rows = quota_entries(main_result, profiles, results, selected, actions, select_account, rename_account)
        rows += [Entry(), Entry("移动悬浮条" if locked else "固定当前位置", toggle_lock),
                 Entry("移到任务栏左侧", actions["left"]), Entry("移到任务栏右侧", actions["right"]),
                 Entry("显示 / 隐藏悬浮条", toggle), Entry("刷新详情", actions["error"]),
                 Entry(), Entry("退出", quit_app)]
        return rows

    def show_menu(x=None, y=None):
        from src.windows_popup import popup_menu
        popup_menu(root, entries())

    actions["menu"] = show_menu
    label.bind("<Button-3>", lambda e: show_menu(e.x_root, e.y_root))

    def press(event):
        nonlocal drag, click_origin, dragged
        click_origin = (event.x_root, event.y_root)
        dragged = False
        if not locked:
            drag = (event.x_root-root.winfo_x(), event.y_root-root.winfo_y())

    def move(event):
        nonlocal dragged
        if drag and not locked:
            if max(abs(event.x_root-click_origin[0]), abs(event.y_root-click_origin[1])) >= 5:
                dragged = True
            if dragged:
                root.geometry(f"{event.x_root-drag[0]:+d}{event.y_root-drag[1]:+d}")

    def release(event):
        nonlocal drag
        drag = None
        if dragged:
            save_position()
        else:
            show_menu(event.x_root, event.y_root)
    label.bind("<Button-1>", press)
    label.bind("<B1-Motion>", move)
    label.bind("<ButtonRelease-1>", release)
    image = Image.new("RGBA", (64, 64), "#182332")
    draw = ImageDraw.Draw(image)
    draw.arc((10, 10, 54, 54), 35, 325, fill="#82e3bc", width=8)
    draw.ellipse((27, 27, 37, 37), fill="#82e3bc")
    def callback(action):
        return lambda icon, item: events.put(("action", action))
    def queued_call(action):
        return lambda icon, item: events.put(("call", action))

    def tray_items(rows):
        for row in rows:
            if not row.title:
                yield pystray.Menu.SEPARATOR
            elif row.children is not None:
                yield pystray.MenuItem(row.title, pystray.Menu(*tray_items(row.children)))
            else:
                action = queued_call(row.action) if row.action else None
                checked = (lambda item, value=row.checked: value) if row.checked is not None else None
                yield pystray.MenuItem(row.title, action, enabled=row.action is not None, checked=checked)

    def tray_menu():
        yield pystray.MenuItem("额度详情", callback("menu"), default=True, visible=False)
        yield from tray_items(entries())

    icon = pystray.Icon("hua-quota-monitor", image, "Codex 额度监控", pystray.Menu(tray_menu))
    threading.Thread(target=icon.run, daemon=True, name="windows-tray").start()

    place(save=False)
    try:
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
        nonlocal busy, last_error, main_result, profiles, selected, results, poll_timer
        while not events.empty():
            kind, value = events.get_nowait()
            if kind in ("action", "call"):
                (actions[value] if kind == "action" else value)()
                if closed:
                    return
            else:
                busy = False
                if kind == "batch":
                    profiles, selected, fetched = value
                    results = {getattr(r["profile"], "name", None) or "canonical": r for r in fetched}
                    value = results.get(selected) or fetched[0]
                main_result = value
                text = quota_text(value)
                label.config(text=text)
                icon.title = text[:127]
                last_error = value.get("error") or "最近一次刷新成功。"
                icon.update_menu()
        poll_timer = root.after(100, poll)

    def tick():
        nonlocal tick_timer
        refresh()
        tick_timer = root.after(max(15, interval)*1000, tick)
    root.protocol("WM_DELETE_WINDOW", quit_app)
    poll_timer = root.after(100, poll)
    tick()
    try:
        root.mainloop()
    finally:
        icon.stop()
    return 0
