import hashlib
import json
import os
import re
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .qwen_annotator import QwenAnnotator
from .subtask_splitter import SubtaskSplitError, SubtaskSplitter


SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _safe_session_id(value: str) -> str:
    if not SESSION_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid session id")
    return value


class CloudSessionStore:
    def __init__(self):
        self.root = Path(os.environ.get("DUCKTRACK_CLOUD_STORAGE", "./cloud-data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.annotator_url = os.environ.get("DUCKTRACK_ANNOTATOR_URL", "").strip()
        self.qwen = QwenAnnotator()
        self.subtask_splitter = SubtaskSplitter()
        self.qwen_concurrency = max(1, int(os.environ.get("DUCKTRACK_QWEN_CONCURRENCY", "1")))
        self._qwen_semaphore = threading.Semaphore(self.qwen_concurrency)
        self.public_base_url = os.environ.get("DUCKTRACK_PUBLIC_BASE_URL", "http://127.0.0.1:18865").rstrip("/")

    @property
    def annotator_configured(self) -> bool:
        return self.qwen.configured or bool(self.annotator_url)

    @property
    def subtask_splitter_configured(self) -> bool:
        return self.subtask_splitter.configured

    def session_dir(self, session_id: str) -> Path:
        return self.root / _safe_session_id(session_id)

    def read_status(self, session_id: str) -> dict:
        path = self.session_dir(session_id) / "cloud_status.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="session not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_sessions(self) -> list[dict]:
        sessions = []
        for status_path in self.root.glob("*/cloud_status.json"):
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            recording = status_path.parent / "recording"
            status["has_trajectory"] = (recording / "trajectory.json").is_file()
            status["has_annotation"] = (recording / "trajectory.annotated.json").is_file()
            status["has_review"] = (recording / "trajectory.reviewed.json").is_file()
            sessions.append(status)
        return sorted(sessions, key=lambda item: item.get("session_id", ""), reverse=True)

    def load_trajectory(self, session_id: str) -> dict:
        recording = self.session_dir(session_id) / "recording"
        candidates = [
            ("reviewed", recording / "trajectory.reviewed.json"),
            ("annotated", recording / "trajectory.annotated.json"),
            ("raw", recording / "trajectory.json"),
        ]
        for source, path in candidates:
            if path.is_file():
                metadata_path = recording / "metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
                return {
                    "session_id": session_id,
                    "source": source,
                    "trajectory": json.loads(path.read_text(encoding="utf-8")),
                    "metadata": metadata,
                    "status": self.read_status(session_id),
                }
        raise HTTPException(status_code=404, detail="trajectory not found")

    def save_reviewed_trajectory(self, session_id: str, trajectory: dict) -> Path:
        if not isinstance(trajectory, dict) or not isinstance(trajectory.get("trajectory"), list):
            raise HTTPException(status_code=400, detail="invalid trajectory payload")
        recording = self.session_dir(session_id) / "recording"
        if not recording.is_dir():
            raise HTTPException(status_code=404, detail="session recording not found")
        trajectory["review_metadata"] = {
            "saved_at_unix": time.time(),
            "source": "ducktrack_unified_ui",
        }
        path = recording / "trajectory.reviewed.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(trajectory, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        self.write_status(session_id, review_state="completed", review_file=path.name)
        return path

    def split_subtasks(self, session_id: str, trajectory: dict | None = None) -> dict:
        """One-click whole-trajectory sub-task split for a cloud session."""
        if not self.subtask_splitter_configured:
            raise HTTPException(
                status_code=503,
                detail="未配置划分模型（请设置 DUCKTRACK_QWEN_URL 或 DUCKTRACK_SUBTASK_URL）",
            )
        recording = self.session_dir(session_id) / "recording"
        if not recording.is_dir():
            raise HTTPException(status_code=404, detail="session recording not found")

        if isinstance(trajectory, dict) and isinstance(trajectory.get("trajectory"), list):
            import copy

            payload = copy.deepcopy(trajectory)
            try:
                updated = self.subtask_splitter.split_trajectory_payload(
                    payload, response_dir=recording / "model_responses"
                )
            except SubtaskSplitError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            # Persist as reviewed so cloud download prefers it
            path = recording / "trajectory.reviewed.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            raw = recording / "trajectory.json"
            raw.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            self.write_status(
                session_id,
                subtask_split_state="completed",
                subtask_split_file=path.name,
                review_file=path.name,
            )
            return updated

        try:
            updated = self.subtask_splitter.split_recording(recording)
        except SubtaskSplitError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        self.write_status(session_id, subtask_split_state="completed")
        return updated

    def write_status(self, session_id: str, **updates) -> dict:
        directory = self.session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "cloud_status.json"
        status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"session_id": session_id}
        status.update(updates)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return status

    def unpack(self, session_id: str, bundle: Path):
        destination = self.session_dir(session_id) / "recording"
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle) as archive:
            for entry in archive.infolist():
                target = (destination / entry.filename).resolve()
                if destination != target and destination not in target.parents:
                    raise ValueError("unsafe path in bundle")
            archive.extractall(destination)

    def annotate(self, session_id: str):
        if self.qwen.configured:
            self._annotate_with_qwen(session_id)
            return
        if not self.annotator_url:
            self.write_status(session_id, annotation_state="awaiting_annotator")
            return
        try:
            self.write_status(session_id, annotation_state="running")
            recording = self.session_dir(session_id) / "recording"
            trajectory_path = recording / "trajectory.json"
            trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
            payload = {
                "session_id": session_id,
                "trajectory": trajectory,
                "files_base_url": f"{self.public_base_url}/api/v1/sessions/{session_id}/files/",
                "auxiliary_files": ["metadata.json", "events.jsonl", "frames/changes.jsonl"],
            }
            request = urllib.request.Request(
                self.annotator_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=1800) as response:
                result = json.loads(response.read().decode("utf-8"))
            annotated = result.get("trajectory", result)
            if isinstance(annotated, dict):
                self._maybe_split_subtasks(recording, annotated)
            output = recording / "trajectory.annotated.json"
            output.write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding="utf-8")
            self.write_status(session_id, annotation_state="completed", annotation_file="trajectory.annotated.json")
        except Exception as exc:
            self.write_status(session_id, annotation_state="failed", annotation_error=f"{type(exc).__name__}: {exc}")

    def _annotate_with_qwen(self, session_id: str):
        self.write_status(
            session_id,
            annotation_state="queued",
            annotation_provider="qwen_chat_completions",
            annotation_model=self.qwen.model,
        )
        with self._qwen_semaphore:
            self._annotate_with_qwen_locked(session_id)

    def _annotate_with_qwen_locked(self, session_id: str):
        try:
            self.write_status(
                session_id,
                annotation_state="running",
                annotation_provider="qwen_chat_completions",
                annotation_model=self.qwen.model,
                annotation_completed_steps=0,
                annotation_total_steps=0,
                annotation_progress_percent=0.0,
                annotation_error=None,
            )
            recording = self.session_dir(session_id) / "recording"

            def update_progress(completed: int, total: int, step_index: int, state: str):
                self.write_status(
                    session_id,
                    annotation_state=state,
                    annotation_completed_steps=completed,
                    annotation_total_steps=total,
                    annotation_current_step=step_index,
                    annotation_progress_percent=round(completed * 100.0 / max(1, total), 1),
                )

            annotated = self.qwen.annotate_trajectory(recording, update_progress)
            # Preserve Feature-1 sub_tasks if the annotator payload already has them.
            self._maybe_split_subtasks(recording, annotated)
            output = recording / "trajectory.annotated.json"
            output.write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding="utf-8")
            metadata = annotated.get("annotation_metadata", {})
            final_state = "completed_with_errors" if metadata.get("failed_steps") else "completed"
            self.write_status(
                session_id,
                annotation_state=final_state,
                annotation_file="trajectory.annotated.json",
                annotation_completed_steps=metadata.get("completed_steps", 0),
                annotation_total_steps=metadata.get("total_steps", 0),
                annotation_failed_steps=metadata.get("failed_steps", 0),
                annotation_progress_percent=100.0,
            )
        except Exception as exc:
            self.write_status(
                session_id,
                annotation_state="failed",
                annotation_error=f"{type(exc).__name__}: {exc}",
            )

    def _maybe_split_subtasks(self, recording: Path, annotated: dict) -> None:
        """Feature-2 placeholder: auto split after step annotation when no manual boundaries.

        Current behavior: ensure sub_tasks from the original trajectory.json are retained
        on the annotated payload when the step annotator did not drop them.
        """
        if not isinstance(annotated, dict):
            return
        if annotated.get("sub_tasks"):
            return
        source_path = recording / "trajectory.json"
        if not source_path.is_file():
            return
        try:
            original = json.loads(source_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(original.get("sub_tasks"), list):
            annotated["sub_tasks"] = original.get("sub_tasks")
        if isinstance(original.get("sub_task_metadata"), dict):
            annotated["sub_task_metadata"] = original.get("sub_task_metadata")
        # TODO(feature-2): if manual_boundary_count == 0, run subtask_splitter here.

    def resume_pending_annotations(self):
        for status_path in self.root.glob("*/cloud_status.json"):
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            resumable_states = {"queued", "running"}
            if self.annotator_configured:
                resumable_states.add("awaiting_annotator")
            if status.get("annotation_state") in resumable_states:
                threading.Thread(
                    target=self.annotate,
                    args=(status_path.parent.name,),
                    daemon=True,
                ).start()


def create_cloud_app() -> FastAPI:
    app = FastAPI(title="DuckTrack Cloud", version="0.1.0")
    store = CloudSessionStore()
    store.resume_pending_annotations()
    token = os.environ.get("DUCKTRACK_CLOUD_TOKEN", "")

    def authorize(request: Request):
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="invalid token")

    @app.put("/api/v1/sessions/{session_id}/bundle", status_code=202)
    async def upload_bundle(session_id: str, request: Request):
        authorize(request)
        session_id = _safe_session_id(session_id)
        directory = store.session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        bundle = directory / "bundle.zip"
        digest = hashlib.sha256()
        with open(bundle, "wb") as stream:
            async for chunk in request.stream():
                digest.update(chunk)
                stream.write(chunk)
        expected = request.headers.get("X-DuckTrack-SHA256")
        actual_digest = digest.hexdigest()
        if expected and actual_digest != expected:
            bundle.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="bundle checksum mismatch")
        try:
            previous = store.read_status(session_id)
            if previous.get("bundle_sha256") == actual_digest and previous.get("upload_state") == "completed":
                return {"session_id": session_id, "state": "already_accepted"}
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        try:
            store.unpack(session_id, bundle)
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        store.write_status(
            session_id,
            upload_state="completed",
            bundle_sha256=actual_digest,
            annotation_state="queued",
        )
        threading.Thread(target=store.annotate, args=(session_id,), daemon=True).start()
        return {"session_id": session_id, "state": "accepted"}

    @app.get("/api/v1/sessions/{session_id}")
    def session_status(session_id: str, request: Request):
        authorize(request)
        return JSONResponse(store.read_status(session_id))

    @app.get("/api/v1/sessions")
    def sessions(request: Request):
        authorize(request)
        return {"sessions": store.list_sessions()}

    @app.get("/api/v1/sessions/{session_id}/trajectory")
    def session_trajectory(session_id: str, request: Request):
        authorize(request)
        return JSONResponse(store.load_trajectory(session_id))

    @app.put("/api/v1/sessions/{session_id}/reviewed")
    async def save_reviewed(session_id: str, request: Request):
        authorize(request)
        payload = await request.json()
        path = store.save_reviewed_trajectory(session_id, payload)
        return {"session_id": session_id, "state": "saved", "file": path.name}

    @app.post("/api/v1/sessions/{session_id}/annotate", status_code=202)
    def start_annotation(session_id: str, request: Request):
        authorize(request)
        status = store.read_status(session_id)
        if not store.annotator_configured:
            raise HTTPException(status_code=503, detail="annotator is not configured")
        if status.get("annotation_state") == "running":
            return {"session_id": session_id, "state": "already_running"}
        store.write_status(session_id, annotation_state="queued", annotation_error=None)
        threading.Thread(target=store.annotate, args=(session_id,), daemon=True).start()
        return {"session_id": session_id, "state": "queued"}

    @app.post("/api/v1/sessions/{session_id}/split_subtasks")
    async def split_subtasks(session_id: str, request: Request):
        authorize(request)
        trajectory = None
        try:
            body = await request.json()
        except Exception:
            body = None
        if isinstance(body, dict) and isinstance(body.get("trajectory"), list):
            trajectory = body
        updated = store.split_subtasks(session_id, trajectory=trajectory)
        return JSONResponse(updated)

    @app.get("/api/v1/sessions/{session_id}/files/{relpath:path}")
    def session_file(session_id: str, relpath: str, request: Request):
        authorize(request)
        base = (store.session_dir(session_id) / "recording").resolve()
        target = (base / relpath).resolve()
        if base != target and base not in target.parents:
            raise HTTPException(status_code=400, detail="invalid path")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "annotator_configured": store.annotator_configured,
            "subtask_splitter_configured": store.subtask_splitter_configured,
            "annotation_provider": "qwen" if store.qwen.configured else (
                "webhook" if store.annotator_url else None
            ),
            "annotation_model": store.qwen.model if store.qwen.configured else None,
            "annotation_concurrency": store.qwen_concurrency if store.qwen.configured else None,
        }

    return app
