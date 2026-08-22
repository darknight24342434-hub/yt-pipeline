from __future__ import annotations

import html
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .settings import settings


TIMING_RE = re.compile(
    r"(?P<start>(?:\d+:)?\d{2}:\d{2}[\.,]\d{3})\s+-->\s+(?P<end>(?:\d+:)?\d{2}:\d{2}[\.,]\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def parse_timestamp(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Invalid timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_timestamp(seconds: float, with_ms: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    if with_ms:
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{whole:02d}.{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{whole:02d}"


def clean_caption_text(lines: Iterable[str]) -> str:
    cleaned: list[str] = []
    for line in lines:
        line = TAG_RE.sub("", line)
        line = html.unescape(line)
        line = line.replace("\u200b", "").strip()
        if line and line not in cleaned:
            cleaned.append(line)
    return " ".join(cleaned).strip()


def parse_vtt(path: Path) -> list[Cue]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    cues: list[Cue] = []
    index = 0
    while index < len(lines):
        match = TIMING_RE.search(lines[index])
        if not match:
            index += 1
            continue
        start = parse_timestamp(match.group("start"))
        end = parse_timestamp(match.group("end"))
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index])
            index += 1
        text = clean_caption_text(text_lines)
        if text:
            if cues and start <= cues[-1].end + 0.8 and text.startswith(cues[-1].text) and len(text) > len(cues[-1].text):
                cues[-1].end = max(cues[-1].end, end)
                cues[-1].text = text
            elif cues and cues[-1].text == text:
                cues[-1].end = max(cues[-1].end, end)
            else:
                cues.append(Cue(start=start, end=end, text=text))
        index += 1
    return cues


def parse_caption_file(path: Path) -> list[Cue]:
    if path.suffix.lower() == ".json3":
        return parse_json3(path)
    return parse_vtt(path)


def parse_json3(path: Path) -> list[Cue]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    cues: list[Cue] = []
    for event in payload.get("events", []):
        segments = event.get("segs") or []
        text = clean_caption_text(segment.get("utf8", "") for segment in segments)
        if not text:
            continue
        start = float(event.get("tStartMs", 0)) / 1000
        end = start + float(event.get("dDurationMs", 0)) / 1000
        cues.append(Cue(start=start, end=end, text=text))
    return cues


def cues_to_plain_text(cues: list[Cue]) -> str:
    return "\n".join(f"[{format_timestamp(cue.start)}] {cue.text}" for cue in cues)


def write_cues(path: Path, cues: list[Cue], title: str) -> None:
    path.write_text(f"# {title}\n\n{cues_to_plain_text(cues)}\n", encoding="utf-8")


def write_cues_json(path: Path, cues: list[Cue]) -> None:
    path.write_text(json.dumps([asdict(cue) for cue in cues], ensure_ascii=False, indent=2), encoding="utf-8")


def detect_language(text: str) -> str:
    sample = text[:5000].strip()
    if not sample:
        return "unknown"
    if is_probably_chinese(sample):
        return "zh"
    try:
        from langdetect import detect

        return detect(sample)
    except Exception:
        return "unknown"


def is_probably_chinese(text: str) -> bool:
    cjk = len(CJK_RE.findall(text))
    letters = sum(1 for char in text if char.isalpha())
    return cjk > 20 and cjk / max(letters, 1) > 0.25


def extract_audio(video_path: Path, audio_path: Path) -> None:
    command = [
        settings.ffmpeg_path,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 音訊擷取失敗：{result.stderr[-1000:]}")


def transcribe_with_faster_whisper(video_path: Path, job_dir: Path) -> list[Cue]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("找不到 faster-whisper。請執行：.\\.venv\\Scripts\\python.exe -m pip install -r requirements-transcribe.txt") from exc

    audio_path = job_dir / "audio.wav"
    extract_audio(video_path, audio_path)
    model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    cues = [Cue(start=float(segment.start), end=float(segment.end), text=segment.text.strip()) for segment in segments if segment.text.strip()]
    if not cues:
        raise RuntimeError("Whisper 沒有產出逐字稿。")
    return cues
