from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from yt_dlp import YoutubeDL

from .settings import settings


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"}


def validate_youtube_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("請輸入 http 或 https YouTube 網址。")
    if host not in YOUTUBE_HOSTS and not host.endswith(".youtube.com"):
        raise ValueError("目前只接受 YouTube 影片網址。")
    return url.strip()


def _base_ydl_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "windowsfilenames": True,
        "restrictfilenames": False,
    }
    if settings.ytdlp_cookies_file:
        options["cookiefile"] = str(settings.ytdlp_cookies_file)
    if settings.ytdlp_cookies_browser:
        options["cookiesfrombrowser"] = (settings.ytdlp_cookies_browser,)
    return options


def fetch_info(url: str) -> dict[str, Any]:
    options = _base_ydl_options()
    # These flags make yt-dlp expose translated automatic captions in the info
    # payload even when a video also has manual captions.
    options.update(
        {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["zh-Hant", "zh-Hans", "zh", "en", "ja", "ko"],
        }
    )
    with YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def download_video(url: str, job_dir: Path, progress: Callable[[float, str], None] | None = None) -> Path:
    def hook(status: dict[str, Any]) -> None:
        if progress is None:
            return
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            downloaded = status.get("downloaded_bytes", 0)
            if total:
                pct = max(0.0, min(1.0, downloaded / total))
                progress(pct, f"下載影片中 {pct:.0%}")
        elif status.get("status") == "finished":
            progress(1.0, "影片下載完成，正在合併封裝。")

    options = _base_ydl_options()
    options.update(
        {
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": str(job_dir / "source.%(ext)s"),
            "progress_hooks": [hook],
        }
    )
    with YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)

    candidates = [
        path
        for path in job_dir.glob("source.*")
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl", ".temp"}
    ]
    if not candidates:
        raise RuntimeError("yt-dlp 沒有產出影片檔。")
    return max(candidates, key=lambda item: item.stat().st_size)


def iter_caption_tracks(info: dict[str, Any], allow_auto: bool = True, preferred: list[str] | None = None) -> list[dict[str, Any]]:
    preferred = preferred or ["zh-Hant", "zh-Hans", "zh", "zh-TW", "zh-CN", "en", "ja", "ko"]

    def language_rank(lang: str) -> tuple[int, int, str]:
        lower = lang.lower()
        zh_preferred = [item.lower() for item in preferred if item.lower().startswith("zh")]
        for index, wanted_lower in enumerate(zh_preferred):
            if lower == wanted_lower:
                return index, 0, lang
        for index, wanted_lower in enumerate(zh_preferred):
            if lower.startswith(f"{wanted_lower}-"):
                if lower.endswith("-en"):
                    return 10 + index, 0, lang
                return 30 + index, 0, lang
        if lower in {"en", "en-us", "en-gb"}:
            return 20, 0, lang
        if lower.startswith("en"):
            return 21, 0, lang
        if lower.startswith("zh"):
            return 35, 0, lang
        if lower.startswith("ja"):
            return 40, 0, lang
        if lower.startswith("ko"):
            return 41, 0, lang
        return 90, 0, lang

    def pick_format(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not entries:
            return None
        ranked = sorted(
            entries,
            key=lambda item: (
                0 if item.get("ext") == "vtt" else 1 if item.get("ext") == "json3" else 2,
                0 if str(item.get("url", "")).startswith("http") else 1,
            ),
        )
        return ranked[0]

    candidates: list[tuple[tuple[int, str], int, str, bool, dict[str, Any]]] = []
    for is_auto, source in [(False, info.get("subtitles") or {}), (True, info.get("automatic_captions") or {})]:
        if is_auto and not allow_auto:
            continue
        for lang in source.keys():
            chosen = pick_format(source.get(lang) or [])
            if chosen and chosen.get("url"):
                candidates.append((language_rank(lang), 1 if is_auto else 0, lang, is_auto, chosen))
    if not candidates:
        return []
    return [
        {"language": lang, "is_auto": is_auto, "format": chosen}
        for _rank, _source_rank, lang, is_auto, chosen in sorted(candidates, key=lambda item: (item[0], item[1]))
    ]


def choose_caption_track(info: dict[str, Any], allow_auto: bool = True, preferred: list[str] | None = None) -> dict[str, Any] | None:
    tracks = iter_caption_tracks(info, allow_auto=allow_auto, preferred=preferred)
    return tracks[0] if tracks else None


def download_caption(track: dict[str, Any], job_dir: Path) -> Path:
    fmt = track["format"]
    ext = re.sub(r"[^a-zA-Z0-9]+", "", fmt.get("ext") or "vtt") or "vtt"
    lang = re.sub(r"[^a-zA-Z0-9_-]+", "_", track.get("language") or "caption")
    path = job_dir / f"captions.{lang}.{ext}"
    response = requests.get(fmt["url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path
