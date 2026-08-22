from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .llm import LLMService, format_clip_markdown
from .settings import settings
from .storage import JobStore
from .transcript import (
    Cue,
    cues_to_plain_text,
    detect_language,
    format_timestamp,
    is_probably_chinese,
    parse_caption_file,
    transcribe_with_faster_whisper,
    write_cues,
    write_cues_json,
)
from .youtube import download_caption, download_video, fetch_info, iter_caption_tracks, validate_youtube_url


def run_pipeline(job_id: str, request: dict[str, Any], store: JobStore) -> None:
    job_dir = store.job_dir(job_id)

    def log(message: str, level: str = "info") -> None:
        store.log(job_id, message, level)

    def progress(stage: str, pct: int, message: str | None = None) -> None:
        store.update(job_id, status="running", stage=stage, progress=max(0, min(100, pct)))
        if message:
            log(message)

    try:
        url = validate_youtube_url(str(request.get("url", "")))
        max_clips = int(request.get("max_clips", 5))
        min_clip_seconds = int(request.get("min_clip_seconds", 30))
        max_clip_seconds = int(request.get("max_clip_seconds", 120))
        allow_auto_captions = bool(request.get("allow_auto_captions", True))
        use_captions = bool(request.get("use_captions", True))
        transcribe_if_no_captions = bool(request.get("transcribe_if_no_captions", True))

        progress("metadata", 5, "讀取 YouTube 影片資訊。")
        info = fetch_info(url)
        metadata = {
            "id": info.get("id"),
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "duration": info.get("duration"),
            "webpage_url": info.get("webpage_url"),
            "description": info.get("description"),
        }
        metadata_path = job_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        store.add_artifact(job_id, "影片資訊", metadata_path, "json")

        progress("download", 10, "開始下載完整影片。")

        def download_progress(download_pct: float, message: str) -> None:
            store.update(job_id, status="running", stage="download", progress=10 + int(download_pct * 30))
            if download_pct >= 1.0:
                log(message)

        video_path = download_video(url, job_dir, download_progress)
        store.add_artifact(job_id, "完整影片", video_path, "video")
        progress("captions", 45, "尋找字幕。")

        cues: list[Cue] = []
        transcript_source = ""
        if use_captions:
            tracks = iter_caption_tracks(info, allow_auto=allow_auto_captions)
            if not tracks:
                log("找不到可用字幕。", "warning")
            for track in tracks:
                try:
                    caption_path = download_caption(track, job_dir)
                    cues = parse_caption_file(caption_path)
                    if not cues:
                        raise RuntimeError("字幕檔沒有可解析內容。")
                    store.add_artifact(job_id, f"原始字幕 ({track['language']})", caption_path, "caption")
                    transcript_source = f"{'自動' if track['is_auto'] else '人工'}字幕：{track['language']}"
                    log(f"使用 {transcript_source}，共 {len(cues)} 段。")
                    break
                except Exception as exc:
                    log(f"字幕 {track['language']} 取得失敗，改試下一個：{exc}", "warning")

        if not cues and transcribe_if_no_captions:
            progress("transcribe", 52, "沒有字幕，改用本機 Whisper 轉錄。")
            cues = transcribe_with_faster_whisper(video_path, job_dir)
            transcript_source = "本機 Whisper 轉錄"
            log(f"Whisper 轉錄完成，共 {len(cues)} 段。")

        if not cues:
            raise RuntimeError("沒有取得逐字稿。請開啟自動字幕，或安裝 requirements-transcribe.txt 後再試。")

        progress("transcript", 60, "整理逐字稿。")
        original_md = job_dir / "transcript_original.md"
        original_json = job_dir / "transcript_original.json"
        write_cues(original_md, cues, "原始逐字稿")
        write_cues_json(original_json, cues)
        store.add_artifact(job_id, "原始逐字稿 Markdown", original_md, "markdown")
        store.add_artifact(job_id, "原始逐字稿 JSON", original_json, "json")

        original_text = cues_to_plain_text(cues)
        language = detect_language(original_text)
        zh_cues = cues
        translated_to_zh = is_probably_chinese(original_text)
        llm = LLMService()
        warnings: list[str] = []
        if not translated_to_zh:
            progress("translate", 66, f"偵測語言為 {language}，準備翻譯成繁體中文。")
            if llm.available:
                zh_cues = llm.translate_cues_to_zh(cues, log=log)
                translated_to_zh = True
            else:
                warning = f"未執行翻譯：{llm.unavailable_reason}"
                warnings.append(warning)
                log(warning, "warning")
        else:
            progress("translate", 66, "逐字稿已是中文，略過翻譯。")

        zh_md = job_dir / "transcript_zh.md"
        zh_json = job_dir / "transcript_zh.json"
        write_cues(zh_md, zh_cues, "中文逐字稿" if translated_to_zh else "中文逐字稿（未翻譯，保留原文）")
        write_cues_json(zh_json, zh_cues)
        store.add_artifact(job_id, "中文逐字稿 Markdown", zh_md, "markdown")
        store.add_artifact(job_id, "中文逐字稿 JSON", zh_json, "json")

        zh_text = cues_to_plain_text(zh_cues)
        progress("summary", 74, "產生內容摘要。")
        if llm.available:
            summary = llm.summarize(zh_text)
        else:
            summary = fallback_summary(zh_cues, transcript_source, llm.unavailable_reason)
            handoff_path = write_llm_handoff(job_dir, job_id, "summary_analysis_clips")
            store.add_artifact(job_id, "LLM 交接檔", handoff_path, "markdown")
        summary_path = job_dir / "summary.md"
        summary_path.write_text(summary.strip() + "\n", encoding="utf-8")
        store.add_artifact(job_id, "內容摘要", summary_path, "markdown")

        progress("analysis", 82, "產生深度分析。")
        if llm.available:
            analysis = llm.deep_analysis(zh_text, summary)
        else:
            analysis = fallback_analysis(zh_cues, warnings)
        analysis_path = job_dir / "analysis.md"
        analysis_path.write_text(analysis.strip() + "\n", encoding="utf-8")
        store.add_artifact(job_id, "深度分析", analysis_path, "markdown")

        progress("segments", 88, "規劃長片精華分片。")
        if llm.available:
            clips = llm.recommend_clips(zh_cues, max_clips, min_clip_seconds, max_clip_seconds)
        else:
            clips = fallback_clips(zh_cues, max_clips, min_clip_seconds, max_clip_seconds)
        clips = normalize_clips(clips, float(info.get("duration") or 0), max_clips, min_clip_seconds, max_clip_seconds)

        progress("clips", 93, "輸出精華片段。")
        extracted = extract_clips(video_path, job_dir, clips, log)
        for clip in extracted:
            if clip.get("file"):
                store.add_artifact(job_id, f"精華片段 {clip['index']:02d}", job_dir / clip["file"], "video")

        segments_json = job_dir / "segments.json"
        segments_md = job_dir / "segments.md"
        segments_json.write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")
        segments_md.write_text(format_clip_markdown(extracted), encoding="utf-8")
        store.add_artifact(job_id, "精華分片 JSON", segments_json, "json")
        store.add_artifact(job_id, "精華分片說明", segments_md, "markdown")

        result = {
            "metadata": metadata,
            "transcript_source": transcript_source,
            "detected_language": language,
            "translated_to_zh": translated_to_zh,
            "summary": summary,
            "analysis": analysis,
            "clips": extracted,
            "warnings": warnings,
        }
        store.update(job_id, status="completed", stage="completed", progress=100, result=result, error=None)
        log("處理完成。")
    except Exception as exc:
        store.update(job_id, status="failed", stage="failed", error=str(exc))
        log(str(exc), "error")


def fallback_summary(cues: list[Cue], transcript_source: str, reason: str) -> str:
    duration = cues[-1].end if cues else 0
    preview = "\n".join(f"- {cue.text}" for cue in cues[:12])
    return (
        "# 內容摘要\n\n"
        f"- 逐字稿來源：{transcript_source or '未知'}\n"
        f"- 影片長度：約 {format_timestamp(duration)}\n"
        f"- 自動 LLM 尚未啟用，因此這是基本摘要。原因：{reason}\n\n"
        "## 前段重點預覽\n\n"
        f"{preview}\n\n"
        "## 建議\n\n"
        "- 不設定 API key 也可以：把這個 job 交給任一個外部 LLM 對話處理，請它讀取 `llm_handoff.md`。\n"
        "- 若未來要讓網頁或手機 App 自動完成 LLM 分析，才需要接 API key 或本機模型服務。\n"
    )


def fallback_analysis(cues: list[Cue], warnings: list[str]) -> str:
    total_chars = sum(len(cue.text) for cue in cues)
    warning_text = "\n".join(f"- {warning}" for warning in warnings) or "- 無"
    return (
        "# 深度分析\n\n"
        "目前未啟用 LLM，所以只產生技術層級檢查。\n\n"
        f"- 字幕段落數：{len(cues)}\n"
        f"- 逐字稿字元數：約 {total_chars}\n"
        f"- 警告：\n{warning_text}\n\n"
        "要用外部 LLM 對話的額度完成內容定位、論點拆解、短影音鉤子與文案角度，請把這個 job 的 `llm_handoff.md` 交給它處理。\n"
    )


def write_llm_handoff(job_dir: Path, job_id: str, task: str) -> Path:
    path = job_dir / "llm_handoff.md"
    text = f"""# LLM 交接檔

Job ID: `{job_id}`
Job folder: `{job_dir}`
Task: `{task}`

請用外部 LLM 對話的模型能力處理這個 job，不需要 `OPENAI_API_KEY`。

請讀取：

- `transcript_zh.md`
- `transcript_original.md`
- `metadata.json`
- `segments.json`

請產出並覆寫：

- `summary.md`：繁體中文內容摘要
- `analysis.md`：繁體中文深度分析
- `segments.md`：重新整理後的精華片段說明

如果逐字稿不是中文，請先在分析中以繁體中文整理，不需要逐段完整翻譯，除非使用者要求。
"""
    path.write_text(text, encoding="utf-8")
    return path


def fallback_clips(cues: list[Cue], max_count: int, min_seconds: int, max_seconds: int) -> list[dict[str, Any]]:
    if not cues:
        return []
    bucket_size = max(min(max_seconds, 90), min_seconds)
    buckets: dict[int, dict[str, Any]] = {}
    for cue in cues:
        bucket = int(cue.start // bucket_size)
        item = buckets.setdefault(bucket, {"score": 0, "start": bucket * bucket_size, "end": (bucket + 1) * bucket_size, "texts": []})
        item["score"] += len(cue.text)
        item["texts"].append(cue.text)
    selected = sorted(buckets.values(), key=lambda item: item["score"], reverse=True)[:max_count]
    selected = sorted(selected, key=lambda item: item["start"])
    clips: list[dict[str, Any]] = []
    for idx, item in enumerate(selected, start=1):
        sample = " ".join(item["texts"])[:80]
        clips.append(
            {
                "title": f"自動候選片段 {idx}",
                "start": float(item["start"]),
                "end": float(item["end"]),
                "reason": "未啟用 LLM，先以逐字稿密度挑選資訊量較高的時間段。",
                "hook": sample,
                "caption": sample,
                "tags": [],
            }
        )
    return clips


def normalize_clips(clips: list[dict[str, Any]], duration: float, max_count: int, min_seconds: int, max_seconds: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for clip in clips[:max_count]:
        start = max(0.0, float(clip.get("start", 0)))
        end = max(start + 1, float(clip.get("end", start + min_seconds)))
        if duration:
            end = min(end, duration)
        if end - start < min_seconds and (not duration or start + min_seconds <= duration):
            end = start + min_seconds
        if end - start > max_seconds:
            end = start + max_seconds
        normalized.append(
            {
                **clip,
                "start": start,
                "end": end,
            }
        )
    return normalized


def extract_clips(video_path: Path, job_dir: Path, clips: list[dict[str, Any]], log: Callable[[str, str], None]) -> list[dict[str, Any]]:
    ffmpeg = shutil.which(settings.ffmpeg_path) or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not ffmpeg:
        log("找不到 ffmpeg，已保留精華時間表但未輸出影片片段。", "warning")
        return [{**clip, "index": idx} for idx, clip in enumerate(clips, start=1)]

    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)
    extracted: list[dict[str, Any]] = []
    for idx, clip in enumerate(clips, start=1):
        start = float(clip["start"])
        duration = max(1.0, float(clip["end"]) - start)
        filename = f"clips/clip_{idx:02d}_{slugify(str(clip.get('title') or 'highlight'))}.mp4"
        output = job_dir / filename
        command = [
            ffmpeg,
            "-y",
            "-ss",
            format_timestamp(start, with_ms=True),
            "-i",
            str(video_path),
            "-t",
            format_timestamp(duration, with_ms=True),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            retry = [
                ffmpeg,
                "-y",
                "-ss",
                format_timestamp(start, with_ms=True),
                "-i",
                str(video_path),
                "-t",
                format_timestamp(duration, with_ms=True),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(output),
            ]
            result = subprocess.run(retry, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
            extracted.append({**clip, "index": idx, "file": filename})
        else:
            log(f"片段 {idx} 輸出失敗：{result.stderr[-500:]}", "warning")
            extracted.append({**clip, "index": idx, "file": None})
    return extracted


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", value).strip("_")
    return value[:40] or "highlight"
