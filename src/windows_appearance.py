"""Native rounded shape for the Windows floating quota window."""
from __future__ import annotations

import ctypes
from ctypes import wintypes


def round_window(root, radius: int = 12) -> None:
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    hwnd = user32.GetAncestor(root.winfo_id(), 2)
    region = gdi32.CreateRoundRectRgn(0, 0, root.winfo_width() + 1,
                                     root.winfo_height() + 1, radius * 2, radius * 2)
    if not region:
        raise ctypes.WinError()
    # Windows owns the region after SetWindowRgn succeeds.
    if not user32.SetWindowRgn(hwnd, region, True):
        gdi32.DeleteObject(region)
        raise ctypes.WinError()
