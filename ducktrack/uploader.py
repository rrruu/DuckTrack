import hashlib
import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile


class UploadManager:
    """Best-effort background uploader; recording never waits for the network."""

    def __init__(self):
        self.cloud_url = os.environ.get("DUCKTRACK_CLOUD_URL", "").rstrip("/")
        self.token = os.environ.get("DUCKTRACK_CLOUD_TOKEN", "")
        self.max_bytes_per_second = max(
            1024 * 1024,
            float(os.environ.get("DUCKTRACK_UPLOAD_MBPS", "8")) * 1024 * 1024,
        )
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.cloud_url)

    def enqueue(self, recording_path: str) -> dict:
        session_id = Path(recording_path).name
        status = {
            "session_id": session_id,
            "recording_path": recording_path,
            "state": "queued" if self.enabled else "disabled",
            "attempts": 0,
            "error": None,
            "uploaded_bytes": 0,
            "total_bytes": 0,
            "progress_percent": 0.0,
        }
        self._write_status(recording_path, status)
        with self._lock:
            self._jobs[session_id] = status
        if self.enabled:
            threading.Thread(target=self._upload_with_retry, args=(status,), daemon=True).start()
        return dict(status)

    def resume_pending(self, recordings_dir: str):
        if not self.enabled:
            return
        for path in Path(recordings_dir).glob("recording-*"):
            status_path = path / "upload_status.json"
            if not (path / "trajectory.json").exists():
                continue
            if status_path.exists():
                try:
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "uploaded":
                        continue
                except (OSError, ValueError):
                    pass
            with self._lock:
                if path.name in self._jobs:
                    continue
            self.enqueue(str(path))

    def get(self, session_id: str | None = None):
        with self._lock:
            if session_id:
                return dict(self._jobs.get(session_id, {"session_id": session_id, "state": "unknown"}))
            return [dict(item) for item in self._jobs.values()]

    def _write_status(self, recording_path: str, status: dict):
        path = Path(recording_path) / "upload_status.json"
        path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    def _make_bundle(self, recording_path: str) -> tuple[str, str]:
        temp = NamedTemporaryFile(prefix="ducktrack-", suffix=".zip", delete=False)
        temp.close()
        digest = hashlib.sha256()
        # Images are already WebP encoded; storing avoids a large CPU spike while
        # the next instrument session is being captured.
        with zipfile.ZipFile(temp.name, "w", compression=zipfile.ZIP_STORED) as archive:
            base = Path(recording_path)
            for path in base.rglob("*"):
                if path.is_file() and path.name != "upload_status.json":
                    archive.write(path, path.relative_to(base).as_posix())
        with open(temp.name, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return temp.name, digest.hexdigest()

    def _upload_once(self, status: dict):
        bundle, digest = self._make_bundle(status["recording_path"])
        try:
            url = f"{self.cloud_url}/api/v1/sessions/{status['session_id']}/bundle"
            parsed = urllib.parse.urlsplit(url)
            connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            connection = connection_cls(parsed.hostname, parsed.port, timeout=120)
            path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
            size = os.path.getsize(bundle)
            status["total_bytes"] = size
            status["uploaded_bytes"] = 0
            status["progress_percent"] = 0.0
            connection.putrequest("PUT", path)
            connection.putheader("Content-Type", "application/zip")
            connection.putheader("Content-Length", str(size))
            connection.putheader("X-DuckTrack-SHA256", digest)
            if self.token:
                connection.putheader("Authorization", f"Bearer {self.token}")
            connection.endheaders()
            upload_started = time.monotonic()
            last_status_write = upload_started
            uploaded = 0
            with open(bundle, "rb") as stream:
                for chunk in iter(lambda: stream.read(256 * 1024), b""):
                    connection.send(chunk)
                    uploaded += len(chunk)
                    status["uploaded_bytes"] = uploaded
                    status["progress_percent"] = round(uploaded * 100.0 / max(1, size), 1)
                    if time.monotonic() - last_status_write >= 1.0:
                        self._write_status(status["recording_path"], status)
                        last_status_write = time.monotonic()
                    expected_elapsed = uploaded / self.max_bytes_per_second
                    remaining = expected_elapsed - (time.monotonic() - upload_started)
                    if remaining > 0:
                        time.sleep(min(remaining, 0.25))
            response = connection.getresponse()
            response.read()
            connection.close()
            if response.status not in {200, 201, 202}:
                raise RuntimeError(f"cloud returned HTTP {response.status}")
        finally:
            Path(bundle).unlink(missing_ok=True)

    def _upload_with_retry(self, status: dict):
        delay = 5.0
        while status["attempts"] < 8:
            status["attempts"] += 1
            status["state"] = "uploading"
            self._write_status(status["recording_path"], status)
            try:
                self._upload_once(status)
                status["state"] = "uploaded"
                status["error"] = None
                status["progress_percent"] = 100.0
                self._write_status(status["recording_path"], status)
                return
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                status["state"] = "retrying"
                status["error"] = f"{type(exc).__name__}: {exc}"
                self._write_status(status["recording_path"], status)
                time.sleep(delay)
                delay = min(delay * 2, 300.0)
        status["state"] = "failed"
        self._write_status(status["recording_path"], status)
