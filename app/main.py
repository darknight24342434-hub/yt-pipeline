from __future__ import annotations

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .pipeline import run_pipeline
from .settings import settings
from .storage import JobStore


class AnalyzeRequest(BaseModel):
    url: str
    use_captions: bool = True
    allow_auto_captions: bool = True
    transcribe_if_no_captions: bool = True
    max_clips: int = Field(default=5, ge=1, le=12)
    min_clip_seconds: int = Field(default=30, ge=10, le=300)
    max_clip_seconds: int = Field(default=120, ge=20, le=600)


class LoginRequest(BaseModel):
    token: str


app = FastAPI(title="YouTube Analyzer")
store = JobStore(settings.data_dir)
executor = ThreadPoolExecutor(max_workers=1)
static_dir = settings.base_dir / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _auth_enabled() -> bool:
    return bool(settings.app_access_token)


def _session_value() -> str:
    return hashlib.sha256(f"yt-analyzer:{settings.app_access_token}".encode("utf-8")).hexdigest()


def _token_matches(token: str) -> bool:
    return bool(token) and hmac.compare_digest(token, settings.app_access_token)


def _request_is_authenticated(request: Request) -> bool:
    if not _auth_enabled():
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer ") and _token_matches(auth_header[7:].strip()):
        return True
    header_token = request.headers.get("x-access-token", "")
    if _token_matches(header_token):
        return True
    cookie_value = request.cookies.get(settings.auth_cookie_name, "")
    return hmac.compare_digest(cookie_value, _session_value())


@app.middleware("http")
async def require_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
    if not _auth_enabled():
        return await call_next(request)

    path = request.url.path
    public_paths = {"/login", "/api/login", "/api/logout", "/api/auth", "/api/health", "/favicon.ico"}
    if path in public_paths or path.startswith("/static/"):
        return await call_next(request)

    if _request_is_authenticated(request):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (static_dir / "index.html").read_text(encoding="utf-8")


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return (static_dir / "login.html").read_text(encoding="utf-8")


@app.post("/api/login")
def login(request: LoginRequest, response: Response) -> dict[str, Any]:
    if not _auth_enabled():
        return {"ok": True, "auth_enabled": False}
    if not _token_matches(request.token):
        raise HTTPException(status_code=401, detail="Access token 錯誤。")
    response.set_cookie(
        settings.auth_cookie_name,
        _session_value(),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return {"ok": True, "auth_enabled": True}


@app.post("/api/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(settings.auth_cookie_name)
    return {"ok": True}


@app.get("/api/auth")
def auth_status(request: Request) -> dict[str, Any]:
    return {"auth_enabled": _auth_enabled(), "authenticated": _request_is_authenticated(request)}


@app.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    # This endpoint is deliberately unauthenticated so the LAN and tunnel launchers can poll
    # it for readiness. data_dir is an absolute path on this machine, so it is only returned
    # to a caller that is already authenticated (which includes every caller when auth is
    # off, i.e. local-only use).
    payload: dict[str, Any] = {"ok": True, "auth_enabled": _auth_enabled()}
    if _request_is_authenticated(request):
        payload["data_dir"] = str(settings.data_dir)
    return payload


@app.post("/api/jobs")
def create_job(request: AnalyzeRequest) -> dict[str, Any]:
    if request.max_clip_seconds < request.min_clip_seconds:
        raise HTTPException(status_code=400, detail="最大片段秒數必須大於最小片段秒數。")
    job = store.create(request.model_dump())
    store.log(job["id"], "工作已建立。")
    executor.submit(run_pipeline, job["id"], request.model_dump(), store)
    return job


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return store.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = store.read(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/files/{filename:path}")
def get_file(job_id: str, filename: str) -> FileResponse:
    job_root = store.job_dir(job_id).resolve()
    target = (job_root / filename).resolve()
    if target != job_root and job_root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target)
