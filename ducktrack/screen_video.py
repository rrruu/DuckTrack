import os
import threading
import time

import pyautogui


class ScreenVideoRecorder:
    def __init__(self, recording_path: str, fps: float = 4.0):
        self.recording_path = recording_path
        self.fps = max(1.0, float(fps or 4.0))
        self.path = os.path.join(recording_path, "screen_recording.mp4")
        self.last_error = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = None

    def start(self):
        if self._thread is not None:
            return
        self._started_at = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15.0):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self):
        try:
            import av

            container = av.open(self.path, "w")
            stream = container.add_stream("mpeg4", rate=int(round(self.fps)))
            first = pyautogui.screenshot().convert("RGB")
            width, height = first.size
            width -= width % 2
            height -= height % 2
            stream.width = max(2, width)
            stream.height = max(2, height)
            stream.pix_fmt = "yuv420p"

            interval = 1.0 / self.fps
            next_frame_at = time.perf_counter()

            while not self._stop_event.is_set():
                now = time.perf_counter()
                if now < next_frame_at:
                    time.sleep(min(0.05, next_frame_at - now))
                    continue

                img = pyautogui.screenshot().convert("RGB")
                if img.size != (stream.width, stream.height):
                    img = img.resize((stream.width, stream.height))
                frame = av.VideoFrame.from_image(img)
                for packet in stream.encode(frame):
                    container.mux(packet)
                next_frame_at += interval

            # Ensure very short recordings still produce a playable file.
            if self._started_at and time.perf_counter() - self._started_at < interval:
                img = pyautogui.screenshot().convert("RGB")
                if img.size != (stream.width, stream.height):
                    img = img.resize((stream.width, stream.height))
                frame = av.VideoFrame.from_image(img)
                for packet in stream.encode(frame):
                    container.mux(packet)

            for packet in stream.encode():
                container.mux(packet)
            container.close()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
