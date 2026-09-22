"""v5-B foreground application context (Windows-first, best-effort)."""

from __future__ import annotations

import os
from collections import Counter
from platform import system
from typing import Any


def _monitor_for_point(monitors: list[dict], x: float, y: float) -> str | None:
    for monitor in monitors or []:
        try:
            mx = float(monitor["x"])
            my = float(monitor["y"])
            mw = float(monitor["width"])
            mh = float(monitor["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if mx <= x < mx + mw and my <= y < my + mh:
            return monitor.get("id")
    return None


def _file_version_info(exe_path: str) -> tuple[str | None, str | None]:
    """Return (ProductName, FileVersion) when available."""
    if not exe_path or system() != "Windows":
        return None, None
    try:
        import ctypes
        from ctypes import wintypes

        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return None, None
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(exe_path, 0, size, buf):
            return None, None

        lptr = wintypes.LPVOID()
        llen = wintypes.UINT()
        if not version.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(lptr), ctypes.byref(llen)):
            return None, None
        if llen.value < 4:
            return None, None
        words = ctypes.cast(lptr, ctypes.POINTER(ctypes.c_ushort))
        lang, codepage = int(words[0]), int(words[1])
        prefix = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\"

        def _query(key: str) -> str | None:
            ptr = wintypes.LPVOID()
            length = wintypes.UINT()
            if not version.VerQueryValueW(buf, prefix + key, ctypes.byref(ptr), ctypes.byref(length)):
                return None
            if not length.value:
                return None
            return ctypes.wstring_at(ptr).strip() or None

        return _query("ProductName"), _query("FileVersion")
    except Exception:
        return None, None


def capture_foreground_context(
    *,
    include_exe_path: bool = False,
    include_version: bool = True,
    monitors: list[dict] | None = None,
) -> dict[str, Any] | None:
    """
    Snapshot the current foreground window.

    Returns None on non-Windows or when no usable foreground window is found.
    Never raises; callers should treat failures as missing context.
    """
    if system() != "Windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        # Title
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""

        # Rects
        rect = wintypes.RECT()
        window_rect = None
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            window_rect = {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
            }

        client = wintypes.RECT()
        client_rect = None
        if user32.GetClientRect(hwnd, ctypes.byref(client)):
            # Client coords are relative; map top-left to screen.
            pt = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            client_rect = {
                "left": int(pt.x),
                "top": int(pt.y),
                "right": int(pt.x) + int(client.right - client.left),
                "bottom": int(pt.y) + int(client.bottom - client.top),
            }

        # Process
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = int(pid.value) if pid.value else None

        process_name = None
        exe_path = None
        if process_id:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
            if handle:
                try:
                    path_buf = ctypes.create_unicode_buffer(4096)
                    size = wintypes.DWORD(len(path_buf))
                    if kernel32.QueryFullProcessImageNameW(handle, 0, path_buf, ctypes.byref(size)):
                        exe_path = path_buf.value
                        process_name = os.path.basename(exe_path) if exe_path else None
                finally:
                    kernel32.CloseHandle(handle)

        dpi_scale = None
        try:
            dpi = int(user32.GetDpiForWindow(hwnd))
            if dpi > 0:
                dpi_scale = round(dpi / 96.0, 4)
        except Exception:
            dpi_scale = None

        monitor_id = None
        if window_rect and monitors:
            cx = (window_rect["left"] + window_rect["right"]) / 2.0
            cy = (window_rect["top"] + window_rect["bottom"]) / 2.0
            monitor_id = _monitor_for_point(monitors, cx, cy)

        app_display_name = None
        app_version = None
        if include_version and exe_path:
            app_display_name, app_version = _file_version_info(exe_path)

        context: dict[str, Any] = {}
        if process_name:
            context["process_name"] = process_name
        if process_id is not None:
            context["process_id"] = process_id
        if include_exe_path and exe_path:
            context["exe_path"] = exe_path
        if app_display_name:
            context["app_display_name"] = app_display_name
        if app_version:
            context["app_version"] = app_version
        if title:
            context["window_title"] = title
        context["window_handle"] = hex(int(hwnd))
        if window_rect:
            context["window_rect"] = window_rect
        if client_rect:
            context["client_rect"] = client_rect
        if monitor_id:
            context["monitor_id"] = monitor_id
        if dpi_scale is not None:
            context["dpi_scale"] = dpi_scale

        return context or None
    except Exception:
        return None


def pick_context_from_events(events: list[dict] | None) -> dict | None:
    """
    Choose a context for a merged trajectory step.

    Prefer the last source event that carries a non-empty context
    (e.g. drag_end / last key_input); otherwise the first.
    """
    if not events:
        return None
    last = None
    first = None
    for event in events:
        ctx = event.get("context")
        if isinstance(ctx, dict) and ctx:
            if first is None:
                first = ctx
            last = ctx
    return last if last is not None else first


def compute_primary_application(trajectory: list[dict]) -> dict | None:
    """Pick the most frequent process_name among actionable steps."""
    counts: Counter[str] = Counter()
    exemplars: dict[str, dict] = {}
    for step in trajectory or []:
        actions = step.get("action") or []
        name = (actions[0] or {}).get("name") if actions else None
        if name in {None, "terminate", "pause", "resume"}:
            continue
        ctx = step.get("context")
        if not isinstance(ctx, dict):
            continue
        process_name = ctx.get("process_name")
        if not process_name:
            continue
        counts[process_name] += 1
        if process_name not in exemplars:
            exemplars[process_name] = ctx

    if not counts:
        return None
    winner, _ = counts.most_common(1)[0]
    src = exemplars.get(winner) or {}
    out: dict[str, Any] = {"process_name": winner}
    for key in ("window_title", "app_display_name", "app_version"):
        if src.get(key):
            out[key] = src[key]
    return out
