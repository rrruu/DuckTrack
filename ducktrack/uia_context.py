"""v5-C UI Automation snapshot at pointer position (Windows, optional)."""

from __future__ import annotations

import threading
from platform import system
from typing import Any

# Microsoft UIA ControlTypeIds (subset + fallback string).
CONTROL_TYPE_NAMES: dict[int, str] = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
}

# Scheme: sample UIA only on click-like pointer actions by default.
UIA_SAMPLE_ACTIONS = frozenset({
    "left_click",
    "right_click",
    "double_left_click",
    "double_right_click",
})

_uia_lock = threading.Lock()
_uia_tls = threading.local()
_last_error: str | None = None


def get_last_error() -> str | None:
    return _last_error


def _set_last_error(message: str) -> None:
    global _last_error
    _last_error = message


def control_type_name(control_type_id: int) -> str:
    return CONTROL_TYPE_NAMES.get(int(control_type_id), f"ControlType_{int(control_type_id)}")


def _ensure_com_initialized() -> bool:
    """
    Ensure COM is ready on the *current* thread.

    Recorder writes events on a worker thread; UIAutomation requires per-thread
    COM init. Prefer comtypes' own CoInitialize so apartment modes stay consistent.
    """
    if getattr(_uia_tls, "com_ok", False):
        return True
    try:
        import comtypes
    except ImportError as exc:
        _set_last_error(f"comtypes not installed: {exc}")
        return False
    try:
        try:
            comtypes.CoInitialize()
        except OSError as exc:
            # RPC_E_CHANGED_MODE: apartment already set differently — still usable.
            if getattr(exc, "winerror", None) not in (-2147417850,):
                raise
        _uia_tls.com_ok = True
        return True
    except Exception as exc:
        _set_last_error(f"CoInitialize failed: {type(exc).__name__}: {exc}")
        return False


def _get_uia():
    """Return a per-thread IUIAutomation instance (COM objects are not freely shared)."""
    if not _ensure_com_initialized():
        return None
    existing = getattr(_uia_tls, "uia", None)
    if existing is not None:
        return existing
    with _uia_lock:
        # Another call on this thread may have filled tls while we waited.
        existing = getattr(_uia_tls, "uia", None)
        if existing is not None:
            return existing
        try:
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as UIA

            uia = comtypes.client.CreateObject(
                UIA.CUIAutomation,
                interface=UIA.IUIAutomation,
            )
            _uia_tls.uia = uia
            return uia
        except Exception as exc:
            _set_last_error(f"CreateObject failed: {type(exc).__name__}: {exc}")
            return None


def capture_uia_at_point(x: int, y: int) -> dict[str, Any] | None:
    """
    Return a compact UIA snapshot for the element under (x, y).

    Fields: control_type, automation_id, name, bounding_rectangle.
    Never raises.
    """
    if system() != "Windows":
        return None
    try:
        from ctypes import wintypes

        uia = _get_uia()
        if uia is None:
            return None

        element = uia.ElementFromPoint(wintypes.POINT(int(x), int(y)))
        if element is None:
            return None

        control_type_id = int(element.CurrentControlType)
        out: dict[str, Any] = {
            "control_type": control_type_name(control_type_id),
            "bounding_rectangle": {},
        }

        automation_id = element.CurrentAutomationId
        if automation_id:
            out["automation_id"] = str(automation_id)

        name = element.CurrentName
        if name:
            out["name"] = str(name)

        rect = element.CurrentBoundingRectangle
        out["bounding_rectangle"] = {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
        }
        return out
    except Exception:
        return None


def maybe_attach_uia_to_event(event: dict, *, enabled: bool) -> None:
    """Attach context.uia when enabled and event is a click-like action with coordinates."""
    if not enabled:
        return
    action = event.get("action")
    if action not in UIA_SAMPLE_ACTIONS:
        return
    x = event.get("x")
    y = event.get("y")
    if x is None or y is None:
        return

    uia = capture_uia_at_point(int(x), int(y))
    if not uia:
        return

    ctx = event.get("context")
    if not isinstance(ctx, dict):
        ctx = {}
        event["context"] = ctx
    ctx["uia"] = uia
