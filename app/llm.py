from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any, Callable

from .settings import settings
from .transcript import Cue, cues_to_plain_text, format_timestamp


class LLMService:
    def __init__(self) -> None:
        self.model = settings.openai_model
        self.client: Any | None = None
        self.unavailable_reason = ""
        if not settings.openai_api_key:
            self.unavailable_reason = "未設定 OPENAI_API_KEY。"
            return
        try:
            from openai import OpenAI

            self.client = OpenAI(api_key=settings.openai_api_key)
        except Exception as exc:
            self.unavailable_reason = f"OpenAI SDK 初始化失敗：{exc}"

    @property
    def available(self) -> bool:
        return self.client is not None

    def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        if self.client is None:
            raise RuntimeError(self.unavailable_reason)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    def translate_cues_to_zh(self, cues: list[Cue], log: Callable[[str], None] | None = None) -> list[Cue]:
        translated = [replace(cue) for cue in cues]
        for batch in _batch_cues(translated, max_chars=9000):
            payload = [{"i": index, "text": cue.text} for index, cue in batch]
            content = self.complete(
                "你是精準字幕翻譯員。把字幕翻譯成繁體中文，保留語意、語氣與專有名詞。只回傳 JSON array。",
                "請翻譯以下字幕。回傳格式必須是 [{\"i\": 0, \"text\": \"翻譯\"}]，不要加入解釋。\n\n"
                + json.dumps(payload, ensure_ascii=False),
                temperature=0.1,
            )
            rows = _extract_json(content)
            mapping = {int(item["i"]): str(item["text"]).strip() for item in rows if "i" in item and "text" in item}
            for index, cue in batch:
                if index in mapping and mapping[index]:
                    translated[index].text = mapping[index]
            if log:
                log(f"已翻譯 {batch[0][0] + 1}-{batch[-1][0] + 1} 段字幕。")
        return translated

    def summarize(self, text: str) -> str:
        chunks = _chunk_text(text, max_chars=14000)
        partials: list[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            partials.append(
                self.complete(
                    "你是影片內容摘要助理。請用繁體中文輸出清楚、可掃讀的 Markdown。",
                    f"這是影片逐字稿第 {idx}/{len(chunks)} 段。請整理重點摘要、核心論點、關鍵資訊與可行動項目。\n\n{chunk}",
                    temperature=0.2,
                )
            )
        if len(partials) == 1:
            return partials[0].strip()
        return self.complete(
            "你是影片內容摘要助理。請用繁體中文整合多段摘要，避免重複。",
            "請把以下分段摘要整合成完整影片摘要，包含：總覽、主要章節、關鍵洞察、可行動項目。\n\n"
            + "\n\n---\n\n".join(partials),
            temperature=0.2,
        ).strip()

    def deep_analysis(self, text: str, summary: str) -> str:
        clipped = text[:50000]
        return self.complete(
            "你是資深內容策略分析師。請用繁體中文輸出結構化 Markdown，指出可以直接用於二次創作與決策的洞察。",
            "請根據影片逐字稿做深度分析。請包含：\n"
            "1. 內容定位與目標受眾\n"
            "2. 主張、證據、推論鏈\n"
            "3. 亮點、弱點、缺口與可查證點\n"
            "4. 可拆成短影音的主題與鉤子\n"
            "5. 標題、縮圖、文案角度建議\n"
            "6. 後續延伸研究方向\n\n"
            f"摘要：\n{summary}\n\n逐字稿：\n{clipped}",
            temperature=0.25,
        ).strip()

    def recommend_clips(self, cues: list[Cue], max_count: int, min_seconds: int, max_seconds: int) -> list[dict[str, Any]]:
        transcript = cues_to_plain_text(cues)
        transcript = transcript[:60000]
        content = self.complete(
            "你是長片精華剪輯企劃。你會根據逐字稿找出適合短影音的完整片段。",
            "請從逐字稿挑出精華片段。每段要有清楚開頭與結尾，時長限制要遵守。\n"
            f"最多 {max_count} 段，每段 {min_seconds}-{max_seconds} 秒。\n"
            "只回傳 JSON array。格式：\n"
            "[{\"title\":\"片段標題\",\"start\":\"00:01:20\",\"end\":\"00:02:05\",\"reason\":\"為何值得剪\",\"hook\":\"短影音開場鉤子\",\"caption\":\"一句繁中字幕文案\",\"tags\":[\"tag\"]}]\n\n"
            f"逐字稿：\n{transcript}",
            temperature=0.25,
        )
        rows = _extract_json(content)
        clips: list[dict[str, Any]] = []
        for row in rows:
            start = _time_to_seconds(str(row.get("start", "0")))
            end = _time_to_seconds(str(row.get("end", "0")))
            if end <= start:
                continue
            duration = end - start
            if duration < min_seconds:
                end = start + min_seconds
            if duration > max_seconds:
                end = start + max_seconds
            clips.append(
                {
                    "title": str(row.get("title") or "精華片段").strip(),
                    "start": start,
                    "end": end,
                    "reason": str(row.get("reason") or "").strip(),
                    "hook": str(row.get("hook") or "").strip(),
                    "caption": str(row.get("caption") or "").strip(),
                    "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
                }
            )
        return clips[:max_count]


def _batch_cues(cues: list[Cue], max_chars: int) -> list[list[tuple[int, Cue]]]:
    batches: list[list[tuple[int, Cue]]] = []
    current: list[tuple[int, Cue]] = []
    total = 0
    for index, cue in enumerate(cues):
        size = len(cue.text) + 20
        if current and total + size > max_chars:
            batches.append(current)
            current = []
            total = 0
        current.append((index, cue))
        total += size
    if current:
        batches.append(current)
    return batches


def _chunk_text(text: str, max_chars: int) -> list[str]:
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    total = 0
    for line in lines:
        if current and total + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current = []
            total = 0
        current.append(line)
        total += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks or [text[:max_chars]]


def _extract_json(content: str) -> list[dict[str, Any]]:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError("LLM did not return a JSON array.")
    return [item for item in data if isinstance(item, dict)]


def _time_to_seconds(value: str) -> float:
    value = value.strip()
    if not value:
        return 0.0
    if re.match(r"^\d+(\.\d+)?$", value):
        return float(value)
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return 0.0
    return 0.0


def format_clip_markdown(clips: list[dict[str, Any]]) -> str:
    lines = ["# 精華片段建議", ""]
    for idx, clip in enumerate(clips, start=1):
        lines.append(f"## {idx}. {clip.get('title', '精華片段')}")
        lines.append(f"- 時間：{format_timestamp(float(clip['start']))} - {format_timestamp(float(clip['end']))}")
        if clip.get("reason"):
            lines.append(f"- 理由：{clip['reason']}")
        if clip.get("hook"):
            lines.append(f"- 鉤子：{clip['hook']}")
        if clip.get("caption"):
            lines.append(f"- 字幕文案：{clip['caption']}")
        if clip.get("file"):
            lines.append(f"- 片段檔：{clip['file']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
