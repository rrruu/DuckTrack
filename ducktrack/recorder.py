import json
import math
import os
import platform
import threading
import time
from datetime import datetime
from platform import system
from queue import Empty, Full, Queue

import pyautogui
from pynput import keyboard, mouse
from pynput.keyboard import KeyCode

from .adaptive_capture import AdaptiveScreenCapture
from .capture_client import CaptureClient
from .metadata import MetadataManager
from .screen_video import ScreenVideoRecorder
from .subtask_schema import build_covering_sub_tasks, last_actionable_step_index, new_sub_task_id
from .util import fix_windows_dpi_scaling, get_recordings_dir


class Recorder(threading.Thread):
    """
    Makes recordings in a headless thread.
    """

    def __init__(
        self,
        natural_scrolling: bool,
        save_screenshots: bool = True,
        save_step_files: bool = False,
        recordings_dir: str | None = None,
    ):
        super().__init__(daemon=True)

        if system() == "Windows":
            fix_windows_dpi_scaling()

        self.recording_path = self._get_recording_path(recordings_dir)

        self._is_recording = False
        self._is_paused = False
        self._step_counter = 0
        self.last_error = None
        self._finalize_lock = threading.Lock()
        self._finalize_started = False
        self._finalize_done = False
        self._finalize_thread = None

        self.event_queue = Queue(maxsize=4096)
        self._dropped_events = 0
        self.events_file = open(os.path.join(self.recording_path, "events.jsonl"), "a")
        # Save per-step screenshots into obj/ (referenced by trajectory.objects).
        self.save_screenshots = save_screenshots
        self.save_step_files = save_step_files
        self.obj_dir = os.path.join(self.recording_path, "obj") if self.save_screenshots else None
        self.steps_dir = os.path.join(self.recording_path, "steps") if self.save_step_files else None
        if self.obj_dir is not None:
            os.makedirs(self.obj_dir, exist_ok=True)
        if self.steps_dir is not None:
            os.makedirs(self.steps_dir, exist_ok=True)

        # Performance guard: cap screenshot frequency to keep CPU/IO stable.
        self._screenshot_min_interval_s = 0.8
        self._last_screenshot_ts = 0.0
        self._last_screenshot_rel: str | None = None

        self.metadata_manager = MetadataManager(
            recording_path=self.recording_path,
            natural_scrolling=natural_scrolling
        )
        self.adaptive_capture = AdaptiveScreenCapture(
            recording_path=self.recording_path,
            monitors=self.metadata_manager.metadata.get("monitors", []),
        )
        self.capture_client = CaptureClient(
            recording_path=self.recording_path,
            metadata=self.metadata_manager.metadata,
        )
        self.screen_video_recorder = ScreenVideoRecorder(
            recording_path=self.recording_path,
            fps=float(os.environ.get("DUCKTRACK_SCREEN_VIDEO_FPS", "4") or 4),
        )
        self._continuous_video_enabled = os.environ.get("DUCKTRACK_CONTINUOUS_VIDEO", "0").lower() in {
            "1", "true", "yes", "on"
        }

        self._drag_threshold = 5
        self._double_click_threshold = 0.35
        self._drag_move_sample_interval_s = 0.05
        self._last_drag_move_ts = 0.0
        self._pressed_button = None
        self._press_pos = None
        self._active_drag_button = None
        self._last_click = {
            "left": {"time_stamp": 0.0, "position": None},
            "right": {"time_stamp": 0.0, "position": None}
        }

        # Sub-task segmentation (Feature-1): first segment starts at index 0.
        self._current_sub_task_id = new_sub_task_id()
        self._sub_task_index = 0
        self._manual_boundary_count = 0
        self._initial_sub_task_id = self._current_sub_task_id

        self.mouse_listener = mouse.Listener(
            on_move=self.on_move,
            on_click=self.on_click,
            on_scroll=self.on_scroll)

        self.keyboard_listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release)

    def _enqueue_event(self, event: dict, important: bool = False):
        try:
            self.event_queue.put(event, block=False)
            return
        except Full:
            pass

        if important:
            try:
                self.event_queue.put(event, block=True, timeout=0.05)
                return
            except Full:
                pass

        self._dropped_events += 1

    def on_move(self, x, y):
        if self._is_paused or self._pressed_button is None or self._press_pos is None:
            return

        distance = math.sqrt((x - self._press_pos[0]) ** 2 + (y - self._press_pos[1]) ** 2)
        if self._active_drag_button is None and distance >= self._drag_threshold:
            now = time.perf_counter()
            self._active_drag_button = self._pressed_button
            self._last_drag_move_ts = now
            self._enqueue_event({
                "time_stamp": now,
                "action": "drag_start",
                "button": self._pressed_button,
                "start_x": self._press_pos[0],
                "start_y": self._press_pos[1],
                "x": x,
                "y": y
            }, important=True)
            return

        # Record mouse trajectory only for an ongoing drag, and sample it.
        if self._active_drag_button is not None:
            now = time.perf_counter()
            if now - self._last_drag_move_ts >= self._drag_move_sample_interval_s:
                self._last_drag_move_ts = now
                self._enqueue_event({
                    "time_stamp": now,
                    "action": "drag_move",
                    "button": self._active_drag_button,
                    "x": x,
                    "y": y
                })

    def _to_click_action(self, button_name: str) -> str:
        if button_name == "left":
            return "left_click"
        if button_name == "right":
            return "right_click"
        return f"{button_name}_click"

    def _to_double_click_action(self, button_name: str) -> str:
        if button_name == "left":
            return "double_left_click"
        if button_name == "right":
            return "double_right_click"
        return f"double_{button_name}_click"

    def _is_double_click(self, button_name: str, x, y, now: float) -> bool:
        if button_name not in self._last_click:
            return False

        last = self._last_click[button_name]
        if last["position"] is None:
            return False

        delta_t = now - last["time_stamp"]
        delta_pos = math.sqrt((x - last["position"][0]) ** 2 + (y - last["position"][1]) ** 2)

        return delta_t <= self._double_click_threshold and delta_pos <= self._drag_threshold

    def on_click(self, x, y, button, pressed):
        if self._is_paused:
            return

        button_name = button.name

        if pressed:
            self._pressed_button = button_name
            self._press_pos = (x, y)
            return

        event_time = time.perf_counter()

        if self._active_drag_button == button_name:
            self._enqueue_event({
                "time_stamp": event_time,
                "action": "drag_end",
                "button": button_name,
                "x": x,
                "y": y
            }, important=True)
            self._active_drag_button = None
            self._last_drag_move_ts = 0.0
        else:
            if self._is_double_click(button_name, x, y, event_time):
                action = self._to_double_click_action(button_name)
                if button_name in self._last_click:
                    self._last_click[button_name] = {"time_stamp": 0.0, "position": None}
            else:
                action = self._to_click_action(button_name)
                if button_name in self._last_click:
                    self._last_click[button_name] = {"time_stamp": event_time, "position": (x, y)}

            self._enqueue_event({
                "time_stamp": event_time,
                "action": action,
                "x": x,
                "y": y,
                "button": button_name
            }, important=True)

        self._pressed_button = None
        self._press_pos = None

    def on_scroll(self, x, y, dx, dy):
        if not self._is_paused:
            self._enqueue_event({
                "time_stamp": time.perf_counter(),
                "action": "scroll",
                "x": x,
                "y": y,
                "dx": dx,
                "dy": dy
            }, important=True)

    def on_press(self, key):
        if not self._is_paused:
            event_time = time.perf_counter()
            if isinstance(key, KeyCode) and key.char is not None:
                # Printable chars only emit key_input to avoid 3x event explosion.
                self._enqueue_event({
                    "time_stamp": event_time,
                    "action": "key_input",
                    "text": key.char
                }, important=True)
            else:
                key_name = key.char if isinstance(key, KeyCode) else key.name
                self._enqueue_event({
                    "time_stamp": event_time,
                    "action": "press",
                    "name": key_name
                }, important=True)

    def on_release(self, key):
        if not self._is_paused:
            if isinstance(key, KeyCode) and key.char is not None:
                return
            self._enqueue_event({
                "time_stamp": time.perf_counter(),
                "action": "release",
                "name": key.char if isinstance(key, KeyCode) else key.name
            }, important=True)

    def run(self):
        self._is_recording = True

        try:
            self.metadata_manager.collect()
            self.adaptive_capture.start()
            if self._continuous_video_enabled:
                self.screen_video_recorder.start()
            self.capture_client.start_recording()

            self.mouse_listener.start()
            self.keyboard_listener.start()

            while self._is_recording or not self.event_queue.empty():
                try:
                    event = self.event_queue.get(timeout=0.2)
                except Empty:
                    continue
                self._capture_step_artifacts(event)
                self.events_file.write(json.dumps(event) + "\n")
            if self._dropped_events:
                self.events_file.write(json.dumps({
                    "time_stamp": time.perf_counter(),
                    "action": "recorder_drop_stats",
                    "dropped_events": self._dropped_events
                }) + "\n")
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._is_recording = False
        finally:
            try:
                self.mouse_listener.stop()
                self.keyboard_listener.stop()
            except Exception:
                pass

    def _capture_step_artifacts(self, event: dict):
        step_idx = self._step_counter
        obj_image_filename = f"step_{step_idx}.png"
        step_filename = f"step_{step_idx}.json"

        obj_image_rel = f"obj\\{obj_image_filename}"
        step_path = os.path.join(self.steps_dir, step_filename) if self.steps_dir else None
        action = event.get("action")
        is_key_action = action in {"press", "release", "key_input"}
        is_motion_sample = action == "drag_move"
        wants_fresh = action in {
            "left_click",
            "right_click",
            "double_left_click",
            "double_right_click",
            "drag_start",
            "drag_end",
            "scroll",
            "pause",
            "resume",
            "sub_task_boundary",
        }

        if self.save_screenshots and self.obj_dir is not None:
            now = float(event.get("time_stamp") or time.perf_counter())
            should_capture = (
                wants_fresh
                or self._last_screenshot_rel is None
                or (
                    not is_key_action
                    and not is_motion_sample
                    and (now - self._last_screenshot_ts) >= self._screenshot_min_interval_s
                )
            )

            if should_capture:
                try:
                    frames = self.adaptive_capture.request_event_frames(step_idx)
                    event["frames"] = frames
                    self._last_screenshot_rel = frames["before"]
                    self._last_screenshot_ts = now
                except Exception:
                    # Best-effort screenshot capture; never fail the whole recording.
                    pass

            event["observation"] = self._last_screenshot_rel
            event["objects"] = self._last_screenshot_rel
        else:
            event["observation"] = None
            event["objects"] = None

        # Optional debug artifact: skip noisy key events to reduce disk I/O.
        if self.save_step_files and step_path and not is_key_action:
            step_data = {"step_idx": step_idx, "event": event}
            with open(step_path, "w", encoding="utf-8") as f:
                json.dump(step_data, f, ensure_ascii=False, indent=4)

        self._step_counter += 1

    def stop_recording(self):
        self._is_recording = False
        with self._finalize_lock:
            if self._finalize_started:
                return
            self._finalize_started = True

        def _finalize():
            try:
                try:
                    # Wait for capture loop to drain queue and exit cleanly.
                    self.join(timeout=30)
                except RuntimeError:
                    # Joining current thread or non-started thread; continue best-effort.
                    pass

                self.metadata_manager.end_collect()
                self.capture_client.stop_recording()
                self.adaptive_capture.stop()
                if self._continuous_video_enabled:
                    self.screen_video_recorder.stop()
                self.metadata_manager.set_video_capture_backend(self.capture_client.backend_name)
                self.metadata_manager.add_video_timing(self.capture_client.video_timing)
                if self.adaptive_capture.last_error:
                    self.metadata_manager.metadata["adaptive_capture_error"] = self.adaptive_capture.last_error
                self.metadata_manager.metadata["capture_mode"] = "adaptive_event_frames"
                if self._continuous_video_enabled and self.screen_video_recorder.last_error:
                    self.metadata_manager.metadata["screen_video_error"] = self.screen_video_recorder.last_error
                elif self._continuous_video_enabled:
                    self.metadata_manager.metadata["screen_video_path"] = "screen_recording.mp4"
                try:
                    self.events_file.close()
                except Exception:
                    pass
                self.metadata_manager.save_metadata()
                self._export_trajectory_json()
                self._finalize_done = True
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"

        self._finalize_thread = threading.Thread(target=_finalize, daemon=True)
        self._finalize_thread.start()

    def wait_until_finalized(self, timeout: float = 60.0) -> bool:
        thread = self._finalize_thread
        if thread is not None:
            thread.join(timeout=timeout)
        return self._finalize_done

    def _export_trajectory_json(self):
        events_path = os.path.join(self.recording_path, "events.jsonl")
        if not os.path.exists(events_path):
            return

        with open(events_path, "r", encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        frames_by_observation = {
            event.get("observation"): event.get("frames")
            for event in events
            if event.get("observation") and event.get("frames")
        }

        desktop = self.metadata_manager.metadata.get("virtual_desktop") or {}
        desktop_x = desktop.get("x", 0)
        desktop_y = desktop.get("y", 0)
        width = desktop.get("width") or self.metadata_manager.metadata.get("screen_width", 1) or 1
        height = desktop.get("height") or self.metadata_manager.metadata.get("screen_height", 1) or 1
        monitors = self.metadata_manager.metadata.get("monitors", [])
        first_ts = events[0]["time_stamp"] if events else time.perf_counter()

        def norm_x(x):
            return max(0.0, min(1.0, (float(x) - float(desktop_x)) / float(width)))

        def norm_y(y):
            return max(0.0, min(1.0, (float(y) - float(desktop_y)) / float(height)))

        def monitor_for(x, y):
            for monitor in monitors:
                if (
                    monitor["x"] <= x < monitor["x"] + monitor["width"]
                    and monitor["y"] <= y < monitor["y"] + monitor["height"]
                ):
                    return monitor["id"]
            return None

        def build_step(step_idx, action_name, params, elapsed, observation=None, objects=None):
            return {
                "observation": observation,
                "screenshot_caption": None,
                "rationale": None,
                "step_instruction": "",
                "action": [{"name": action_name, "parameters": params}],
                "width": width,
                "height": height,
                "elapsed_time": round(elapsed, 3),
                "objects": objects or (f"obj\\step_{step_idx}.png" if self.save_screenshots else None),
                "frames": frames_by_observation.get(observation),
                "reference_idx": None,
            }

        trajectory = []
        pending_drag = None
        pending_drag_points = None
        # Timeline after the last committed step (same time base as elapsed_time).
        # Insert 1s wait steps only when the next real action is more than 1s later,
        # advancing the cursor second-by-second so waits never "overlap" real actions.
        timeline_end = None
        last_real_observation = None
        last_real_objects = None
        typing_buffer = ""
        typing_start_elapsed = None
        typing_observation = None
        typing_objects = None

        # Suppress accidental double-releases logged as two very close clicks at ~same spot.
        _last_click_export = None  # (elapsed, nx, ny, kind)

        def append_real_action(
            action_name,
            params,
            elapsed,
            observation=None,
            objects=None,
        ):
            nonlocal timeline_end, last_real_observation, last_real_objects
            el = float(elapsed)
            if timeline_end is not None:
                while False and el - timeline_end > 1.0 + 1e-9:
                    wtime = timeline_end + 1.0
                    trajectory.append(
                        build_step(
                            len(trajectory),
                            "wait",
                            {
                                "duration": 1.0,
                                "needs_review": True,
                            },
                            round(wtime, 3),
                            last_real_observation,
                            last_real_objects,
                        )
                    )
                    timeline_end = wtime
            trajectory.append(
                build_step(
                    len(trajectory),
                    action_name,
                    params,
                    round(el, 3),
                    observation,
                    objects,
                )
            )
            timeline_end = el
            last_real_observation = observation
            last_real_objects = objects

        def flush_typing_buffer():
            nonlocal typing_buffer, typing_start_elapsed, typing_observation, typing_objects
            if typing_buffer and typing_start_elapsed is not None:
                append_real_action(
                    "type",
                    {"text": typing_buffer},
                    typing_start_elapsed,
                    typing_observation,
                    typing_objects,
                )
            typing_buffer = ""
            typing_start_elapsed = None
            typing_observation = None
            typing_objects = None

        def should_skip_spurious_click(elapsed, nx, ny, kind: str) -> bool:
            nonlocal _last_click_export
            if _last_click_export is None:
                return False
            e0, x0, y0, k0 = _last_click_export
            if k0 != kind:
                return False
            if float(elapsed) - float(e0) > 0.15:
                return False
            if math.hypot(float(nx) - float(x0), float(ny) - float(y0)) > 0.02:
                return False
            return True

        def record_click_export(elapsed, nx, ny, kind: str) -> None:
            nonlocal _last_click_export
            _last_click_export = (float(elapsed), float(nx), float(ny), kind)

        # If multiple wheel ticks happen in a short time window,
        # merge them into a single continuous scroll action.
        scroll_group_gap_s = 0.25

        # Open sub-task ranges collected while walking events (Feature-1).
        subtask_ranges = [{
            "id": self._initial_sub_task_id,
            "index": 0,
            "instruction": "",
            "start_step": 0,
        }]
        export_boundary_count = 0

        i = 0
        while i < len(events):
            event = events[i]
            action = event.get("action")

            if action == "sub_task_boundary":
                flush_typing_buffer()
                export_boundary_count += 1
                subtask_ranges.append({
                    "id": event.get("sub_task_id") or new_sub_task_id(),
                    "index": int(event.get("sub_task_index", len(subtask_ranges))),
                    "instruction": event.get("instruction") or "",
                    "start_step": len(trajectory),
                })
                i += 1
                continue

            # Merge continuous text input into one "type" action.
            if action == "key_input":
                elapsed = event["time_stamp"] - first_ts
                if typing_start_elapsed is None:
                    typing_start_elapsed = elapsed
                    typing_observation = event.get("observation")
                    typing_objects = event.get("objects")
                typing_buffer += event.get("text", "")
                i += 1
                continue

            # Skip press/release for single printable chars when typing buffer is active.
            if action in ("press", "release"):
                key_name = event.get("name")
                if (
                    isinstance(key_name, str)
                    and len(key_name) == 1
                    and typing_start_elapsed is not None
                ):
                    i += 1
                    continue

            # Any non-typing event flushes current text buffer first.
            flush_typing_buffer()

            if action == "scroll":
                first_event = event
                last_event = event
                dx_sum = 0
                dy_sum = 0

                j = i
                while j < len(events) and events[j].get("action") == "scroll":
                    # Stop if the wheel gesture is "broken" by a pause.
                    if j > i and events[j]["time_stamp"] - events[j - 1]["time_stamp"] > scroll_group_gap_s:
                        break
                    dx_sum += events[j].get("dx", 0)
                    dy_sum += events[j].get("dy", 0)
                    last_event = events[j]
                    j += 1

                elapsed = first_event["time_stamp"] - first_ts
                append_real_action(
                    "scroll",
                    {
                        "x": norm_x(last_event["x"]) if "x" in last_event else None,
                        "y": norm_y(last_event["y"]) if "y" in last_event else None,
                        "screen_x": last_event.get("x"),
                        "screen_y": last_event.get("y"),
                        "monitor_id": monitor_for(last_event["x"], last_event["y"])
                        if "x" in last_event and "y" in last_event else None,
                        # Keep existing schema: dy aggregated into "clicks"
                        "clicks": dy_sum,
                    },
                    elapsed,
                    first_event.get("observation"),
                    first_event.get("objects"),
                )
                i = j
                continue

            elapsed = event["time_stamp"] - first_ts

            if action == "left_click":
                nx = norm_x(event["x"])
                ny = norm_y(event["y"])
                if should_skip_spurious_click(elapsed, nx, ny, "left"):
                    i += 1
                    continue
                append_real_action(
                    "click",
                    {
                        "x": nx,
                        "y": ny,
                        "screen_x": event["x"],
                        "screen_y": event["y"],
                        "monitor_id": monitor_for(event["x"], event["y"]),
                    },
                    elapsed,
                    event.get("observation"),
                    event.get("objects"),
                )
                record_click_export(elapsed, nx, ny, "left")
            elif action == "right_click":
                nx = norm_x(event["x"])
                ny = norm_y(event["y"])
                if should_skip_spurious_click(elapsed, nx, ny, "right"):
                    i += 1
                    continue
                append_real_action(
                    "rightClick",
                    {
                        "x": nx,
                        "y": ny,
                        "screen_x": event["x"],
                        "screen_y": event["y"],
                        "monitor_id": monitor_for(event["x"], event["y"]),
                    },
                    elapsed,
                    event.get("observation"),
                    event.get("objects"),
                )
                record_click_export(elapsed, nx, ny, "right")
            elif action == "double_left_click":
                append_real_action(
                    "doubleClick",
                    {
                        "x": norm_x(event["x"]),
                        "y": norm_y(event["y"]),
                        "screen_x": event["x"],
                        "screen_y": event["y"],
                        "monitor_id": monitor_for(event["x"], event["y"]),
                    },
                    elapsed,
                    event.get("observation"),
                    event.get("objects"),
                )
            elif action == "double_right_click":
                append_real_action(
                    "doubleRightClick",
                    {
                        "x": norm_x(event["x"]),
                        "y": norm_y(event["y"]),
                        "screen_x": event["x"],
                        "screen_y": event["y"],
                        "monitor_id": monitor_for(event["x"], event["y"]),
                    },
                    elapsed,
                    event.get("observation"),
                    event.get("objects"),
                )
            elif action == "drag_start":
                pending_drag = event
                pending_drag_points = [
                    {
                        "x": norm_x(pending_drag.get("start_x", pending_drag.get("x", 0))),
                        "y": norm_y(pending_drag.get("start_y", pending_drag.get("y", 0))),
                    }
                ]
            elif action == "drag_move" and pending_drag is not None:
                if pending_drag_points is None:
                    pending_drag_points = []
                pending_drag_points.append({
                    "x": norm_x(event.get("x", pending_drag.get("x", 0))),
                    "y": norm_y(event.get("y", pending_drag.get("y", 0))),
                })
            elif action == "drag_end" and pending_drag is not None:
                drag_path = pending_drag_points[:] if pending_drag_points else []
                drag_path.append({
                    "x": norm_x(event.get("x", pending_drag.get("x", 0))),
                    "y": norm_y(event.get("y", pending_drag.get("y", 0))),
                })
                append_real_action(
                    "dragTo",
                    {
                        "start_x": norm_x(pending_drag.get("start_x", pending_drag.get("x", 0))),
                        "start_y": norm_y(pending_drag.get("start_y", pending_drag.get("y", 0))),
                        "end_x": norm_x(event.get("x", pending_drag.get("x", 0))),
                        "end_y": norm_y(event.get("y", pending_drag.get("y", 0))),
                        "start_monitor_id": monitor_for(
                            pending_drag.get("start_x", pending_drag.get("x", 0)),
                            pending_drag.get("start_y", pending_drag.get("y", 0)),
                        ),
                        "end_monitor_id": monitor_for(
                            event.get("x", pending_drag.get("x", 0)),
                            event.get("y", pending_drag.get("y", 0)),
                        ),
                        "button": event.get("button", pending_drag.get("button", "left")),
                        "duration": round(event["time_stamp"] - pending_drag["time_stamp"], 3),
                        "path": drag_path,
                    },
                    elapsed,
                    event.get("observation") or pending_drag.get("observation"),
                    event.get("objects") or pending_drag.get("objects"),
                )
                pending_drag = None
                pending_drag_points = None
            elif action == "press":
                append_real_action(
                    "keyDown",
                    {"key": event.get("name")},
                    elapsed,
                    event.get("observation"),
                    event.get("objects"),
                )
            elif action == "release":
                append_real_action(
                    "keyUp",
                    {"key": event.get("name")},
                    elapsed,
                    event.get("observation"),
                    event.get("objects"),
                )
            elif action in ["pause", "resume"]:
                append_real_action(
                    action,
                    {},
                    elapsed,
                    event.get("observation"),
                    event.get("objects"),
                )

            i += 1

        flush_typing_buffer()

        # If we saw a drag_start but never captured drag_end,
        # still emit a dragTo step by treating the end == start.
        if pending_drag is not None:
            start_x_val = pending_drag.get("start_x", pending_drag.get("x", 0))
            start_y_val = pending_drag.get("start_y", pending_drag.get("y", 0))
            drag_path = pending_drag_points[:] if pending_drag_points else []
            drag_path.append({
                "x": norm_x(start_x_val),
                "y": norm_y(start_y_val),
            })
            append_real_action(
                "dragTo",
                {
                    "start_x": norm_x(start_x_val),
                    "start_y": norm_y(start_y_val),
                    "end_x": norm_x(start_x_val),
                    "end_y": norm_y(start_y_val),
                    "button": pending_drag.get("button", "left"),
                    "duration": 0.0,
                    "path": drag_path,
                },
                pending_drag["time_stamp"] - first_ts,
                pending_drag.get("observation"),
                pending_drag.get("objects"),
            )

        terminate_elapsed = float(events[-1]["time_stamp"] - first_ts) if events else 0.0
        terminate_observation = last_real_observation
        terminate_objects = last_real_objects

        if not trajectory:
            initial_observation = None
            if self.save_screenshots and self.obj_dir is not None:
                initial_rel = "obj\\step_0.png"
                initial_path = os.path.join(self.obj_dir, "step_0.png")
                try:
                    screenshot = pyautogui.screenshot()
                    screenshot.save(initial_path)
                    try:
                        screenshot.close()
                    except Exception:
                        pass
                    initial_observation = initial_rel
                except Exception:
                    initial_observation = None
            terminate_observation = initial_observation
            terminate_objects = initial_observation

        # Do not insert idle waits before terminate (avoid flooding waits at end of recording).
        trajectory.append(
            build_step(
                len(trajectory),
                "terminate",
                {"status": "success"},
                round(terminate_elapsed, 3),
                terminate_observation,
                terminate_objects,
            )
        )

        last_actionable = last_actionable_step_index(trajectory)
        while len(subtask_ranges) > 1 and int(subtask_ranges[-1].get("start_step", 0)) > last_actionable:
            subtask_ranges.pop()
            export_boundary_count = max(0, export_boundary_count - 1)

        if export_boundary_count == 0:
            boundary_count = 0
            ranges_for_build = None
        else:
            boundary_count = export_boundary_count
            ranges_for_build = subtask_ranges

        sub_tasks, sub_task_metadata = build_covering_sub_tasks(
            trajectory,
            ranges=ranges_for_build,
            manual_boundary_count=boundary_count,
            initial_id=self._initial_sub_task_id,
        )

        app_name = "unknown"
        try:
            app_name = platform.node() or "unknown"
        except Exception:
            pass

        result = {
            "query": "",
            "application": app_name,
            "platform_type": (self.metadata_manager.metadata.get("system", "unknown") or "unknown").lower(),
            "url": None,
            "sub_tasks": sub_tasks,
            "sub_task_metadata": sub_task_metadata,
            "trajectory": trajectory,
        }

        trajectory_path = os.path.join(self.recording_path, "trajectory.json")
        with open(trajectory_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=4)

    def mark_sub_task_boundary(self, instruction: str = ""):
        """Start a new sub-task segment from the next recorded actions."""
        if not self._is_recording:
            raise RuntimeError("No active recording")
        if self.last_error:
            raise RuntimeError(f"Recorder crashed: {self.last_error}")
        self._sub_task_index += 1
        self._current_sub_task_id = new_sub_task_id()
        self._manual_boundary_count += 1
        text = instruction if isinstance(instruction, str) else ""
        self._enqueue_event(
            {
                "time_stamp": time.perf_counter(),
                "action": "sub_task_boundary",
                "sub_task_id": self._current_sub_task_id,
                "sub_task_index": self._sub_task_index,
                "instruction": text,
            },
            important=True,
        )
        return {
            "sub_task_id": self._current_sub_task_id,
            "sub_task_index": self._sub_task_index,
            "manual_boundary_count": self._manual_boundary_count,
        }

    def get_sub_task_state(self) -> dict:
        return {
            "sub_task_id": self._current_sub_task_id,
            "sub_task_index": self._sub_task_index,
            "manual_boundary_count": self._manual_boundary_count,
        }

    def pause_recording(self):
        if not self._is_paused and self._is_recording:
            self._is_paused = True
            self.adaptive_capture.pause()
            self.capture_client.pause_recording()
            self._enqueue_event({
                "time_stamp": time.perf_counter(),
                "action": "pause"
            }, important=True)

    def resume_recording(self):
        if self._is_paused and self._is_recording:
            self._is_paused = False
            self.adaptive_capture.resume()
            self.capture_client.resume_recording()
            self._enqueue_event({
                "time_stamp": time.perf_counter(),
                "action": "resume"
            }, important=True)

    def _get_recording_path(self, recordings_dir: str | None = None) -> str:
        recordings_dir = recordings_dir or get_recordings_dir()

        os.makedirs(recordings_dir, exist_ok=True)

        current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        recording_path = os.path.join(recordings_dir, f"recording-{current_time}")
        suffix = 1
        original_recording_path = recording_path
        while os.path.exists(recording_path):
            suffix += 1
            recording_path = f"{original_recording_path}-{suffix}"
        os.mkdir(recording_path)

        return recording_path
