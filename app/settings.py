from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def _path_from_env(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    data_dir: Path = Path(os.getenv("YT_ANALYZER_DATA_DIR", str(BASE_DIR / "data"))).expanduser()
    app_access_token: str = os.getenv("APP_ACCESS_TOKEN", "").strip()
    auth_cookie_name: str = os.getenv("AUTH_COOKIE_NAME", "yt_analyzer_session").strip()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    whisper_model: str = os.getenv("WHISPER_MODEL", "small").strip()
    ffmpeg_path: str = os.getenv("FFMPEG_PATH", "ffmpeg").strip()
    ytdlp_cookies_file: Path | None = _path_from_env("YTDLP_COOKIES_FILE")
    ytdlp_cookies_browser: str = os.getenv("YTDLP_COOKIES_BROWSER", "").strip()


settings = Settings()
