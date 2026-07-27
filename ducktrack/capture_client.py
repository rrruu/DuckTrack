import os
import threading
import time
from typing import Any


class CaptureClient:
    """
    Thin adapter around `openadapt-capture`.

    The generated OpenAdapt video is action-gated rather than wall-clock
    continuous, so annotators should fall back to step screenshots instead of
    assuming a seekable real-time timeline.
    """

    backend_name = "openadapt_capture"

    def __init__(self, recording_path: str, metadata: dict[str, Any]):
        self.recording_path = recording_path
        self.metadata = metadata
        self._recorder_context = None
        self._recorder_instance = None
        self._openadapt_patched = False
        self._enabled = self._env_bool("DUCKTRACK_OA_ENABLED", False)
        self.video_timing = {
            "source": self.backend_name,
            "clock": "perf_counter",
            "realtime": False,
            "seek_mode": "step_screenshot",
            "enabled": self._enabled,
            "trim_pauses": False,
            "supports_pause": False,
            "start_events": [],
            "pause_events": [],
            "resume_events": [],
            "stop_events": [],
        }

    def _env_bool(self, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "y", "on"}:
            return True
        if value in {"0", "false", "no", "n", "off"}:
            return False
        return default

    def _env_float(self, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    def _tune_openadapt_runtime(self):
        if self._openadapt_patched:
            return

        if not self._env_bool("DUCKTRACK_OA_DISABLE_MOUSE_MOVE", True):
            self.video_timing["openadapt_mouse_move"] = "enabled"
        else:
            try:
                import openadapt_capture.recorder as oa_recorder  # type: ignore

                def _drop_mouse_move(event_q, x, y, injected=False):
                    return None

                oa_recorder.on_move = _drop_mouse_move
                self.video_timing["openadapt_mouse_move"] = "disabled"
            except Exception:
                self.video_timing["openadapt_mouse_move"] = "default"

        # Work around occasional stop deadlock in openadapt event_processor:
        # inject one wake-up event when terminate flag is set so queue.get() unblocks.
        try:
            import openadapt_capture.recorder as oa_recorder  # type: ignore

            if not getattr(oa_recorder, "_ducktrack_safe_process_events_patch", False):
                original_process_events = oa_recorder.process_events

                def _safe_process_events(*args, **kwargs):
                    event_q = args[0] if len(args) > 0 else kwargs.get("event_q")
                    terminate_processing = args[8] if len(args) > 8 else kwargs.get("terminate_processing")

                    if event_q is not None and terminate_processing is not None:
                        def _wakeup_on_stop():
                            try:
                                terminate_processing.wait()
                                ts = oa_recorder.utils.get_timestamp()
                                wake_event = oa_recorder.Event(
                                    ts,
                                    "window",
                                    {"title": "__ducktrack_stop__", "window_id": "__ducktrack_stop__"},
                                )
                                event_q.put(wake_event)
                            except Exception:
                                pass

                        threading.Thread(target=_wakeup_on_stop, daemon=True).start()

                    return original_process_events(*args, **kwargs)

                oa_recorder.process_events = _safe_process_events
                oa_recorder._ducktrack_safe_process_events_patch = True
                self.video_timing["openadapt_stop_patch"] = "enabled"
            else:
                self.video_timing["openadapt_stop_patch"] = "already_enabled"
        except Exception:
            self.video_timing["openadapt_stop_patch"] = "default"

        self._openadapt_patched = True

    def _load_recorder_class(self):
        try:
            from openadapt_capture import Recorder  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency `openadapt-capture`. Run `pip install -r requirements.txt` first."
            ) from exc
        if Recorder is None:
            raise RuntimeError(
                "`openadapt-capture` is installed but Recorder is unavailable in this environment."
            )
        return Recorder

    def _build_recorder(self):
        recorder_cls = self._load_recorder_class()
        self._tune_openadapt_runtime()
        task_description = self.metadata.get("task_description") or "DuckTrack recording"

        capture_video = self._env_bool("DUCKTRACK_OA_CAPTURE_VIDEO", True)
        capture_images = self._env_bool("DUCKTRACK_OA_CAPTURE_IMAGES", False)
        capture_audio = self._env_bool("DUCKTRACK_OA_CAPTURE_AUDIO", False)
        capture_full_video = self._env_bool("DUCKTRACK_OA_CAPTURE_FULL_VIDEO", True)
        screen_capture_fps = self._env_float("DUCKTRACK_OA_SCREEN_FPS", 3.0)
        plot_performance = self._env_bool("DUCKTRACK_OA_PLOT_PERFORMANCE", False)

        if not capture_video and not capture_images:
            capture_video = True

        recorder_kwargs = {
            "capture_video": capture_video,
            "capture_audio": capture_audio,
            "capture_images": capture_images,
            "capture_window_data": False,
            "capture_browser_events": False,
            "capture_full_video": capture_full_video,
            "plot_performance": plot_performance,
            "screen_capture_fps": screen_capture_fps,
        }

        self.video_timing["openadapt_config"] = {
            "capture_video": capture_video,
            "capture_audio": capture_audio,
            "capture_images": capture_images,
            "capture_full_video": capture_full_video,
            "screen_capture_fps": screen_capture_fps,
            "plot_performance": plot_performance,
        }
        self.video_timing["realtime"] = bool(capture_video and capture_full_video)
        self.video_timing["seek_mode"] = "video_timeline" if self.video_timing["realtime"] else "step_screenshot"

        try:
            return recorder_cls(
                self.recording_path,
                task_description=task_description,
                **recorder_kwargs,
            )
        except TypeError:
            try:
                return recorder_cls(self.recording_path, task_description=task_description)
            except TypeError:
                # Keep compatibility with older package versions whose constructor
                # may only accept the output directory.
                return recorder_cls(self.recording_path)

    def start_recording(self):
        if self._recorder_context is not None:
            return

        self.video_timing["start_events"].append(time.perf_counter())
        if not self._enabled:
            self.video_timing["status"] = "disabled"
            self.video_timing["disabled_reason"] = (
                "Set DUCKTRACK_OA_ENABLED=1 to enable openadapt video capture."
            )
            return

        self._recorder_context = self._build_recorder()

        enter = getattr(self._recorder_context, "__enter__", None)
        if callable(enter):
            self._recorder_instance = enter()
        else:
            self._recorder_instance = self._recorder_context
            start_fn = getattr(self._recorder_context, "start", None)
            if callable(start_fn):
                start_fn()

    def stop_recording(self):
        if self._recorder_context is None:
            self.video_timing["stop_events"].append(time.perf_counter())
            return

        self.video_timing["stop_events"].append(time.perf_counter())
        stop_fn = getattr(self._recorder_context, "stop", None)
        if callable(stop_fn):
            stop_fn()

        exit_fn = getattr(self._recorder_context, "__exit__", None)
        if callable(exit_fn):
            error_holder: list[str] = []

            def _run_exit():
                try:
                    exit_fn(None, None, None)
                except Exception as exc:
                    error_holder.append(f"{type(exc).__name__}: {exc}")

            t = threading.Thread(target=_run_exit, daemon=True)
            t.start()
            t.join(timeout=20.0)
            if t.is_alive():
                self.video_timing["stop_timeout"] = True
            if error_holder:
                self.video_timing["stop_error"] = error_holder[0]

        self._recorder_instance = None
        self._recorder_context = None

    def pause_recording(self) -> bool:
        pause_fn = getattr(self._recorder_instance, "pause", None)
        if callable(pause_fn):
            pause_fn()
            self.video_timing["pause_events"].append(time.perf_counter())
            self.video_timing["supports_pause"] = True
            self.video_timing["trim_pauses"] = True
            return True
        return False

    def resume_recording(self) -> bool:
        resume_fn = getattr(self._recorder_instance, "resume", None)
        if callable(resume_fn):
            resume_fn()
            self.video_timing["resume_events"].append(time.perf_counter())
            self.video_timing["supports_pause"] = True
            self.video_timing["trim_pauses"] = True
            return True
        return False
