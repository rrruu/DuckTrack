import os
import platform
import subprocess
from pathlib import Path
from typing import Union

from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button


def name_to_key(name: str) -> Union[Key, KeyCode]:
    try:
        return getattr(Key, name)
    except AttributeError:
        return KeyCode.from_char(name)

def name_to_button(name: str) -> Button:
    return getattr(Button, name)

def get_recordings_dir() -> str:
    """
    Resolve a writable recordings directory.

    Priority:
    1) DUCKTRACK_RECORDINGS_DIR env
    2) ~/Documents/DuckTrack_Recordings
    3) ~/DuckTrack_Recordings
    4) <project_root>/recordings
    """
    candidates = []

    env_dir = os.environ.get("DUCKTRACK_RECORDINGS_DIR")
    if env_dir:
        candidates.append(Path(env_dir))

    candidates.append(Path.home() / "Documents" / "DuckTrack_Recordings")
    candidates.append(Path.home() / "DuckTrack_Recordings")
    candidates.append(Path(__file__).resolve().parent.parent / "recordings")

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".ducktrack-write-probe"
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            probe.unlink(missing_ok=True)
            return str(path)
        except OSError:
            continue

    # Last-resort fallback to current working directory.
    fallback = Path.cwd() / "DuckTrack_Recordings"
    fallback.mkdir(parents=True, exist_ok=True)
    return str(fallback)

def fix_windows_dpi_scaling():
    """
    Fixes DPI scaling issues with legacy windows applications
    Reference: https://pynput.readthedocs.io/en/latest/mouse.html#ensuring-consistent-coordinates-between-listener-and-controller-on-windows
    """
    import ctypes
    PROCESS_PER_MONITOR_DPI_AWARE = 2
    # If DPI awareness has already been set by Qt/system, Windows returns access denied.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
    except Exception:
        pass

def open_file(path):
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
