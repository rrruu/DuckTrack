import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


@dataclass
class CaptureRequest:
    due_at: float
    path: Path


class AdaptiveScreenCapture:
    """Low-rate screen cache plus asynchronous event-frame persistence."""

    def __init__(self, recording_path: str, monitors: list[dict]):
        self.frames_dir = Path(recording_path) / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.monitors = monitors
        self.poll_fps = max(0.2, float(os.environ.get("DUCKTRACK_CAPTURE_POLL_FPS", "0.5")))
        self.change_threshold = max(0.0, float(os.environ.get("DUCKTRACK_CHANGE_THRESHOLD", "2.5")))
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._latest: Image.Image | None = None
        self._latest_thumb: Image.Image | None = None
        self._requests: list[CaptureRequest] = []
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.saved_frames = 0

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="adaptive-screen-capture", daemon=True)
            self._thread.start()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()
        self._wake.set()

    def stop(self, timeout: float = 10.0):
        # Preserve promised frame paths even when recording stops before delayed
        # samples become due. The latest cached frame is preferable to a broken link.
        with self._lock:
            pending = self._requests
            self._requests = []
            latest = self._latest.copy() if self._latest is not None else None
        if latest is None and pending:
            try:
                latest = self._grab()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
        if latest is not None:
            for item in pending:
                self._save(latest.copy(), item.path)
            latest.close()
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def request_event_frames(self, event_id: int) -> dict:
        """Return stable paths immediately; image writes happen off the event thread."""
        before_rel = f"frames/event_{event_id:06d}_before.webp"
        after_rel = f"frames/event_{event_id:06d}_after.webp"
        settled_rel = f"frames/event_{event_id:06d}_settled.webp"
        with self._lock:
            if self._latest is not None:
                self._save(self._latest.copy(), self.frames_dir / Path(before_rel).name)
            now = time.perf_counter()
            if self._latest is None:
                self._requests.append(CaptureRequest(now, self.frames_dir / Path(before_rel).name))
            self._requests.extend([
                CaptureRequest(now + 0.25, self.frames_dir / Path(after_rel).name),
                CaptureRequest(now + 1.0, self.frames_dir / Path(settled_rel).name),
            ])
        self._wake.set()
        return {"before": before_rel, "after": after_rel, "settled": settled_rel}

    def _grab(self) -> Image.Image:
        try:
            import mss

            with mss.mss() as capture:
                shot = capture.grab(capture.monitors[0])
                return Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception:
            import pyautogui

            return pyautogui.screenshot().convert("RGB")

    def _changed(self, image: Image.Image) -> bool:
        thumb = image.copy()
        thumb.thumbnail((320, 180))
        thumb = thumb.convert("L")
        previous = self._latest_thumb
        self._latest_thumb = thumb
        if previous is None or previous.size != thumb.size:
            return True
        return ImageStat.Stat(ImageChops.difference(previous, thumb)).mean[0] >= self.change_threshold

    def _save(self, image: Image.Image, path: Path):
        # Fast WebP encoding keeps CPU spikes below the instrument UI thread.
        image.save(path, "WEBP", quality=75, method=0)
        self.saved_frames += 1
        image.close()

    def _run(self):
        interval = 1.0 / self.poll_fps
        next_poll = 0.0
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    self._wake.wait(0.5)
                    self._wake.clear()
                    continue

                now = time.perf_counter()
                with self._lock:
                    due = [item for item in self._requests if item.due_at <= now]
                    self._requests = [item for item in self._requests if item.due_at > now]
                needs_frame = bool(due) or now >= next_poll
                if needs_frame:
                    image = self._grab()
                    changed = self._changed(image)
                    with self._lock:
                        old = self._latest
                        self._latest = image.copy()
                    if old is not None:
                        old.close()
                    for item in due:
                        self._save(image.copy(), item.path)
                    if changed and not due:
                        # Keep sparse autonomous UI changes that have no input event.
                        rel = self.frames_dir / f"change_{time.time_ns()}.webp"
                        self._save(image.copy(), rel)
                        with open(self.frames_dir / "changes.jsonl", "a", encoding="utf-8") as log:
                            log.write(json.dumps({
                                "time_stamp": time.perf_counter(),
                                "frame": f"frames/{rel.name}",
                            }) + "\n")
                    image.close()
                    next_poll = now + interval

                wait_for = max(0.02, min(0.25, next_poll - time.perf_counter()))
                with self._lock:
                    if self._requests:
                        wait_for = min(wait_for, max(0.01, self._requests[0].due_at - time.perf_counter()))
                self._wake.wait(wait_for)
                self._wake.clear()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                latest = self._latest
                self._latest = None
            if latest is not None:
                latest.close()
