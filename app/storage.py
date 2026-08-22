from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "request": request,
            "logs": [],
            "artifacts": [],
            "result": {},
            "error": None,
        }
        self.save(job)
        (job_dir / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        return job

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def job_file(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def save(self, job: dict[str, Any]) -> None:
        with self._lock:
            self.job_dir(job["id"]).mkdir(parents=True, exist_ok=True)
            self.job_file(job["id"]).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    def read(self, job_id: str) -> dict[str, Any] | None:
        path = self.job_file(job_id)
        if not path.exists():
            return None
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for path in sorted(self.jobs_dir.glob("*/job.json"), reverse=True):
            try:
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return jobs

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            job = self.read(job_id)
            if job is None:
                raise KeyError(job_id)
            job.update(fields)
            job["updated_at"] = utc_now()
            self.save(job)
            return job

    def log(self, job_id: str, message: str, level: str = "info") -> None:
        with self._lock:
            job = self.read(job_id)
            if job is None:
                raise KeyError(job_id)
            logs = job.setdefault("logs", [])
            logs.append({"at": utc_now(), "level": level, "message": message})
            job["logs"] = logs[-300:]
            job["updated_at"] = utc_now()
            self.save(job)

    def add_artifact(self, job_id: str, label: str, path: Path, kind: str = "file") -> None:
        with self._lock:
            job = self.read(job_id)
            if job is None:
                raise KeyError(job_id)
            rel = path.relative_to(self.job_dir(job_id)).as_posix()
            artifacts = job.setdefault("artifacts", [])
            if not any(item.get("path") == rel for item in artifacts):
                artifacts.append({"label": label, "path": rel, "kind": kind})
            job["updated_at"] = utc_now()
            self.save(job)

