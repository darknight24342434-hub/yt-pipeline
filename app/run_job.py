from __future__ import annotations

import json
import sys

from .pipeline import run_pipeline
from .settings import settings
from .storage import JobStore


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m app.run_job <job_id>", file=sys.stderr)
        return 2
    job_id = sys.argv[1]
    store = JobStore(settings.data_dir)
    request_path = store.job_dir(job_id) / "request.json"
    if not request_path.exists():
        print(f"Missing request file: {request_path}", file=sys.stderr)
        return 1
    request = json.loads(request_path.read_text(encoding="utf-8"))
    run_pipeline(job_id, request, store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

