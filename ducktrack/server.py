import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from .recorder import Recorder
from .util import get_recordings_dir
from .uploader import UploadManager


class RecorderManager:
    def __init__(self):
        self.recorder: Optional[Recorder] = None

    def start(self, recordings_dir: Optional[str] = None):
        if self.recorder and self.recorder.is_alive():
            # Idempotent start: if recording is already active, return current path.
            return self.recorder.recording_path
        self.recorder = Recorder(
            natural_scrolling=False,
            save_screenshots=True,
            recordings_dir=recordings_dir,
        )
        self.recorder.start()
        # Wait briefly so startup-time crashes are surfaced to API callers.
        stable_ticks = 0
        for _ in range(20):
            if self.recorder.last_error:
                err = self.recorder.last_error
                self.recorder = None
                raise RuntimeError(f"Recorder startup failed: {err}")
            if not self.recorder.is_alive():
                self.recorder = None
                raise RuntimeError("Recorder failed to start")
            stable_ticks += 1
            if stable_ticks >= 3:
                return self.recorder.recording_path
            time.sleep(0.05)
        return self.recorder.recording_path

    def pause(self):
        if not self.recorder:
            raise RuntimeError("No active recording")
        if self.recorder.last_error:
            raise RuntimeError(f"Recorder crashed: {self.recorder.last_error}")
        if not self.recorder.is_alive():
            raise RuntimeError("No active recording")
        self.recorder.pause_recording()

    def resume(self):
        if not self.recorder:
            raise RuntimeError("No active recording")
        if self.recorder.last_error:
            raise RuntimeError(f"Recorder crashed: {self.recorder.last_error}")
        if not self.recorder.is_alive():
            raise RuntimeError("No active recording")
        self.recorder.resume_recording()

    def stop(self):
        if not self.recorder:
            raise RuntimeError("No active recording")
        if self.recorder.last_error:
            raise RuntimeError(f"Recorder crashed: {self.recorder.last_error}")
        path = self.recorder.recording_path
        self.recorder.stop_recording()
        if not self.recorder.wait_until_finalized(timeout=60):
            raise RuntimeError("Recording finalization timed out")
        if self.recorder.last_error:
            raise RuntimeError(f"Recorder finalization failed: {self.recorder.last_error}")
        return path


def list_recordings():
    recordings_dir = Path(get_recordings_dir())
    recordings_dir.mkdir(parents=True, exist_ok=True)
    return sorted([p.name for p in recordings_dir.iterdir() if p.is_dir()], reverse=True)


def create_app():
    manager = RecorderManager()
    uploader = UploadManager()
    uploader.resume_pending(get_recordings_dir())
    app = FastAPI(title="DuckTrack API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/recordings")
    def api_list_recordings():
        return {"recordings": list_recordings()}

    @app.post("/api/recordings/start")
    def api_start(payload: Optional[dict] = None):
        try:
            recordings_dir = None
            if payload:
                recordings_dir = payload.get("recordings_dir") or None
            path = manager.start(recordings_dir=recordings_dir)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"recording_path": path}

    @app.post("/api/recordings/pause")
    def api_pause():
        try:
            manager.pause()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"status": "paused"}

    @app.post("/api/recordings/resume")
    def api_resume():
        try:
            manager.resume()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"status": "resumed"}

    @app.post("/api/recordings/stop")
    def api_stop():
        try:
            path = manager.stop()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        upload = uploader.enqueue(path)
        return {"recording_path": path, "upload": upload}

    @app.get("/api/uploads")
    def api_uploads():
        return {"enabled": uploader.enabled, "jobs": uploader.get()}

    @app.get("/api/uploads/{session_id}")
    def api_upload_status(session_id: str):
        return uploader.get(session_id)

    @app.get("/api/recordings/{name}/metadata")
    def api_metadata(name: str):
        path = Path(get_recordings_dir()) / name / "metadata.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="metadata.json not found")
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/recordings/{name}/trajectory")
    def api_trajectory(name: str):
        path = Path(get_recordings_dir()) / name / "trajectory.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="trajectory.json not found")
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/recordings/{name}/events")
    def api_events(name: str):
        path = Path(get_recordings_dir()) / name / "events.jsonl"
        if not path.exists():
            raise HTTPException(status_code=404, detail="events.jsonl not found")
        return Response(path.read_text(encoding="utf-8"), media_type="text/plain")

    @app.get("/api/recordings/{name}/file")
    def api_file(name: str, relpath: str):
        base = Path(get_recordings_dir()) / name
        target = (base / relpath).resolve()
        if base not in target.parents:
            raise HTTPException(status_code=400, detail="invalid path")
        if not target.exists():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(str(target))

    static_dir = Path(__file__).resolve().parent.parent / "web"

    @app.get("/")
    def index():
        path = static_dir / "index.html"
        if not path.exists():
            return {"message": "web UI not built yet"}
        return FileResponse(str(path))

    @app.get("/{path:path}")
    def static_files(path: str):
        candidate = static_dir / path
        if candidate.is_file():
            return FileResponse(str(candidate))
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=404, detail="not found")

    return app
