from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from src.windows_widget import run_widget


@unittest.skipUnless(os.name == 'nt' and importlib.util.find_spec('pystray'), 'Windows UI dependencies required')
class WindowsInteractionTests(unittest.TestCase):
    def test_rounding_clicks_drag_and_lock_survive_restart(self):
        import tkinter as tk
        from src.windows_menu import Entry
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            settings = store / 'windows-widget.json'
            settings.write_text(json.dumps({'x': 120, 'y': 300, 'locked': True}))
            fake_result = {'profile': {'display_name': '测试账号'}, 'usage': {'rate_limit': {'primary_window': {
                'limit_window_seconds': 18000, 'used_percent': 22, 'reset_at': time.time()+3600}}},
                'reset': {'credits': []}, 'updated': datetime.now()}
            for phase in (0, 1):
                root = tk.Tk()
                def interact():
                    root.update()
                    label = next(w for w in root.winfo_children() if isinstance(w, tk.Label))
                    self.assertFalse(any(isinstance(w, tk.Button) for w in root.winfo_children()))
                    self.assertAlmostEqual(root.attributes('-alpha'), 0.88, places=2)
                    self.assertEqual((root.winfo_x(), root.winfo_y()), (120, 300) if phase == 0 else (160, 330), f"phase={phase}, settings={settings.read_text()}")
                    # Assert Windows has a nonrectangular region for the rounded corners.
                    import ctypes
                    from ctypes import wintypes
                    u, g = ctypes.windll.user32, ctypes.windll.gdi32
                    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
                    u.GetAncestor.restype = wintypes.HWND
                    u.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
                    g.CreateRectRgn.restype = wintypes.HANDLE
                    g.DeleteObject.argtypes = [wintypes.HANDLE]
                    region = g.CreateRectRgn(0, 0, 0, 0)
                    try:
                        self.assertEqual(u.GetWindowRgn(u.GetAncestor(root.winfo_id(), 2), region), 3)
                    finally:
                        g.DeleteObject(region)
                    if phase == 0:
                        with patch('src.windows_popup.popup_menu') as popup:
                            label.event_generate('<Button-1>', x=15, y=15, rootx=135, rooty=315)
                            label.event_generate('<ButtonRelease-1>', x=15, y=15, rootx=135, rooty=315)
                            root.update()
                            self.assertEqual(popup.call_count, 1)
                            label.event_generate('<Button-3>', x=15, y=15, rootx=135, rooty=315)
                            root.update()
                            self.assertEqual(popup.call_count, 2)
                            rows = popup.call_args.args[1]
                            next(row for row in rows if row.title == '移动悬浮条').action()
                            self.assertEqual(label.cget('cursor'), 'fleur')
                            label.event_generate('<Button-1>', x=15, y=15, rootx=135, rooty=315)
                            label.event_generate('<Motion>', x=55, y=45, rootx=175, rooty=345, state=256)
                            root.update()
                            label.event_generate('<ButtonRelease-1>', x=55, y=45, rootx=175, rooty=345)
                            root.update()
                            self.assertEqual(popup.call_count, 2, 'drag must not open a menu')
                            self.assertEqual((root.winfo_x(), root.winfo_y()), (160, 330))
                        data = json.loads(settings.read_text())
                        self.assertFalse(data['locked'])
                    else:
                        self.assertEqual(label.cget('cursor'), 'fleur')
                        with patch('src.windows_popup.popup_menu') as popup:
                            label.event_generate('<Button-3>', x=15, y=15)
                            rows = popup.call_args.args[1]
                            next(row for row in rows if row.title == '固定当前位置').action()
                        self.assertEqual(label.cget('cursor'), 'arrow')
                        label.event_generate('<Button-1>', x=15, y=15, rootx=175, rooty=345)
                        label.event_generate('<Motion>', x=55, y=45, rootx=215, rooty=375, state=256)
                        root.update()
                        self.assertEqual((root.winfo_x(), root.winfo_y()), (160, 330))
                    root.tk.call(root.protocol('WM_DELETE_WINDOW'))
                import pystray
                real_icon = pystray.Icon
                def fake_icon(*args):
                    # Exercise real menu validation without starting another tray icon.
                    model = real_icon(*args)
                    list(model.menu.items)
                    return Mock()
                with patch('tkinter.Tk', return_value=root), patch.object(root, 'mainloop', side_effect=interact), \
                     patch('pystray.Icon', side_effect=fake_icon), \
                     patch('src.quota_service.QuotaService.fetch_profile', return_value=fake_result):
                    run_widget(auth_file=store/'missing-auth.json', base_url='https://example.com', store_dir=store, interval=60)
            self.assertTrue(json.loads(settings.read_text())['locked'])
