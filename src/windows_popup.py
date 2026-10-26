"""Windows popup menu anchored to the floating window in native coordinates."""
from __future__ import annotations
import ctypes
from ctypes import wintypes


def popup_anchor(window, monitor):
    left, top, right, bottom = window
    ml, mt, mr, mb = monitor
    above = (top + bottom) / 2 > (mt + mb) / 2
    return left, top - 6 if above else bottom + 6, 0x20 if above else 0


def popup_menu(root, rows):
    u = ctypes.windll.user32
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.CreatePopupMenu.restype = wintypes.HMENU
    u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    u.DestroyMenu.argtypes = [wintypes.HMENU]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.TrackPopupMenuEx.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
    u.TrackPopupMenuEx.restype = wintypes.UINT
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HANDLE
    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT), ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    callbacks = []
    handles = []
    def build(entries):
        handle = u.CreatePopupMenu()
        if not handle:
            raise ctypes.WinError()
        handles.append(handle)
        for entry in entries:
            if not entry.title:
                flags, value = 0x800, 0
            elif entry.children is not None:
                flags, value = 0x10, build(entry.children)
            else:
                callbacks.append(entry.action)
                flags = (0 if entry.action else 1) | (8 if entry.checked else 0)
                value = len(callbacks)
            if not u.AppendMenuW(handle, flags, value, entry.title):
                raise ctypes.WinError()
            if entry.children is not None:
                handles.remove(value)
        return handle
    handle = None
    try:
        handle = build(rows)
        hwnd = u.GetAncestor(root.winfo_id(), 2)
        rect = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        monitor = MonitorInfo(); monitor.size = ctypes.sizeof(monitor)
        u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(monitor))
        x, y, align = popup_anchor((rect.left, rect.top, rect.right, rect.bottom),
                                  (monitor.monitor.left, monitor.monitor.top, monitor.monitor.right, monitor.monitor.bottom))
        u.SetForegroundWindow(hwnd)
        selected = u.TrackPopupMenuEx(handle, align | 0x100, x, y, hwnd, None)
    finally:
        # Root menu owns its attached submenus.
        if handle:
            u.DestroyMenu(handle)
        else:
            for created in reversed(handles):
                u.DestroyMenu(created)
    if selected and callbacks[selected-1]:
        callbacks[selected-1]()
