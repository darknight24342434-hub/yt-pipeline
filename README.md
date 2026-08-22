# yt-pipeline

A local FastAPI web app: paste a YouTube URL and it downloads the video, gets a transcript, translates it to Traditional Chinese, and produces a summary, a deep analysis, and cut highlight clips.

Everything runs on your own machine. The only outbound calls are to YouTube (via `yt-dlp`) and, if you configure a key, to the OpenAI API.

## What it does

For each submitted URL the pipeline runs these stages, writing every intermediate result to a per-job folder:

1. **Metadata and download** — `yt-dlp` reads the video info and downloads the full video.
2. **Captions** — prefers YouTube's manual captions, then translated captions, then auto-generated ones.
3. **Transcription fallback** — if no captions exist and the extra requirements are installed, transcribes locally with `faster-whisper` (CPU, int8). No audio leaves the machine.
4. **Translation** — if the transcript is not Chinese and `OPENAI_API_KEY` is set, translates it to Traditional Chinese.
5. **Summary and analysis** — generates a content summary and a deeper analysis of the argument structure.
6. **Highlight segmentation** — picks highlight spans and cuts them to mp4 with `ffmpeg`.

Progress, logs, and every produced artifact are visible in the web UI while the job runs. Jobs execute one at a time on a single background worker thread.

**Without an `OPENAI_API_KEY`** the app still downloads the video, produces transcript files, a basic summary, and rough segmentation. Translation, deep analysis, and precise highlight selection need an LLM. In that case the job folder also gets an `llm_handoff.md` — a prompt file you can hand to a separate LLM session to produce `summary.md`, `analysis.md`, and `segments.md` by hand. See `docs/llm-handoff.md`.

## Requirements

- Windows with PowerShell (the launcher scripts are `.ps1`; the Python app itself is not Windows-specific)
- Python 3.10 or newer
- `ffmpeg` on `PATH`, or set `FFMPEG_PATH`
- An OpenAI API key, for translation, deep analysis, and LLM-driven highlight selection
- Disk space — full source videos are kept alongside the outputs

## Install

```powershell
git clone https://github.com/<your-account>/yt-pipeline.git
cd yt-pipeline
Copy-Item .env.example .env
notepad .env
.\run.ps1
```

`run.ps1` creates `.venv` if it is missing, installs `requirements.txt`, and starts uvicorn on `http://127.0.0.1:8765`.

To enable the local Whisper fallback for videos with no captions:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-transcribe.txt
```

## Configuration

All configuration comes from environment variables, read from `.env` in the repo root. Copy `.env.example` and edit it. `.env` is gitignored; never commit a real one.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | *(empty)* | Enables translation, high-quality summary, deep analysis, and LLM highlight selection. Without it the pipeline degrades to download + transcript + rough segmentation. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for all LLM steps. |
| `APP_ACCESS_TOKEN` | *(empty)* | Any non-empty value turns on login protection. Empty means no auth at all. |
| `AUTH_COOKIE_NAME` | `yt_analyzer_session` | Name of the session cookie set after login. |
| `WHISPER_MODEL` | `small` | `faster-whisper` model size for the local transcription fallback. |
| `FFMPEG_PATH` | `ffmpeg` | Path or command name for ffmpeg if it is not on `PATH`. |
| `YT_ANALYZER_DATA_DIR` | `<repo>/data` | Where job folders are written. Point it at a larger disk if needed. |
| `YTDLP_COOKIES_FILE` | *(unset)* | Path to a `cookies.txt` file passed to `yt-dlp`. |
| `YTDLP_COOKIES_BROWSER` | *(unset)* | Name of a browser (e.g. `chrome`) for `yt-dlp` to read YouTube cookies from directly. |

### A note on the cookie options

`YTDLP_COOKIES_FILE` and `YTDLP_COOKIES_BROWSER` are both commented out by default and the app works without them. They exist because YouTube sometimes refuses a download unless the request carries a logged-in session.

Turning either on means handing your own YouTube session credentials to `yt-dlp`. Doing so — and using it to fetch videos that are otherwise gated — is your own call to make against YouTube's Terms of Service, and it can put your Google account at risk. The default is off, and leaving it off is the safe choice.

Regardless of cookies: only process videos you own, are licensed to use, or are handling within fair use, and respect the terms of YouTube and the original content source.

## Usage

### Local only

```powershell
.\run.ps1
.\run.ps1 -Port 9000
```

Then open `http://127.0.0.1:8765`. Paste a URL, choose the number of highlight clips and the min/max clip length, and submit.

### On your LAN

```powershell
.\run-lan.ps1
```

Binds to `0.0.0.0` on port 8973, prints the LAN URL, and — if `.env` has no `APP_ACCESS_TOKEN` yet — generates a random one and appends it, so the app is never exposed unauthenticated.

### Through a public tunnel

```powershell
.\run-public-tunnel.ps1
```

Starts the server on port 8973 and opens a Cloudflare Quick Tunnel to it, printing a temporary `https://….trycloudflare.com` URL. Like `run-lan.ps1`, it generates an `APP_ACCESS_TOKEN` first if none is set.

**This script needs the `cloudflared` binary.** It is not committed to this repo. On first run the script downloads the current Windows release into `tools/cloudflared.exe` itself (that directory is gitignored). If you would rather supply your own — from your package manager, or Cloudflare's downloads page — put it at `tools\cloudflared.exe` before running, or install it on `PATH` and edit the `$cloudflared` line. Quick Tunnels are throwaway URLs meant for testing; for anything long-lived see `docs/external-access.md`.

### Setting the access token

```powershell
.\set-token.ps1
.\set-token.ps1 -Restart
.\set-token.ps1 -Token "your-token" -Restart
```

With no `-Token`, it prompts without echoing. Passing `-Token` on the command line can leave the value in shell history, so prefer the prompt. `-Restart` kills the running uvicorn process and relaunches it in the background on port 8973 (override with `-Port`), logging to `logs/`.

API clients can authenticate with a header instead of the login cookie:

```http
Authorization: Bearer <APP_ACCESS_TOKEN>
X-Access-Token: <APP_ACCESS_TOKEN>
```

### Running one job from the command line

```powershell
.\.venv\Scripts\python.exe -m app.run_job <job-id>
```

Re-runs the pipeline for an existing job folder that already contains `request.json`.

## Output

Everything lands under `data/jobs/<job-id>/` (or `$YT_ANALYZER_DATA_DIR/jobs/<job-id>/`):

| File | Contents |
|---|---|
| `job.json` | Job status, stage, progress, logs, artifact list |
| `request.json` | The original request parameters |
| `metadata.json` | Video info from `yt-dlp` |
| `source.mp4` | The full downloaded video |
| `transcript_original.md` / `.json` | Transcript in the source language |
| `transcript_zh.md` / `.json` | Traditional Chinese transcript |
| `summary.md` | Content summary |
| `analysis.md` | Deep analysis |
| `segments.md` / `.json` | Highlight segmentation |
| `clips/*.mp4` | The cut highlight clips |
| `llm_handoff.md` | Only when no API key is set — a prompt to hand to an external LLM |

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/login` | Exchange the access token for a session cookie |
| `POST /api/logout` | Clear the session cookie |
| `GET /api/auth` | Whether auth is enabled and whether this request is authenticated |
| `GET /api/health` | Liveness and auth state. The only endpoint reachable without the token, so the LAN and tunnel launchers can poll it for readiness. It also returns `data_dir`, but only to an authenticated caller. |
| `POST /api/jobs` | Create an analysis job |
| `GET /api/jobs` | List jobs |
| `GET /api/jobs/{job_id}` | Job status and results |
| `GET /api/jobs/{job_id}/files/{path}` | Download an artifact from a job folder |

## Limitations

- **Single user, single worker.** Jobs run one at a time on one background thread, held in memory in one process. There is no queue, no cancellation endpoint, and no way to delete a job or reclaim its disk from the UI — you delete job folders by hand. Restarting the server abandons any in-flight job.
- **The access token is the entire security model.** One shared token, compared in constant time, no accounts, no rate limiting, no per-user isolation. Anyone holding it can download videos, read every job's output, and spend your API credits. It is adequate for a tunnel you open for an afternoon; it is not adequate for a service left running on the public internet.
- **Quick Tunnel URLs are ephemeral and public** while open. Anyone with the URL reaches your login page, and can call `GET /api/health` without a token — it answers `ok` and whether auth is on, nothing more.
- **Disk grows without bound.** Full source videos are kept and nothing expires or is cleaned up.
- **LLM output is not verified.** Summaries, analyses, and highlight timestamps come from a language model and can be wrong or invented. Check them against the transcript before relying on them.
- **The launchers are Windows-only.** `run.ps1`, `run-lan.ps1`, `run-public-tunnel.ps1` and `set-token.ps1` assume PowerShell, `.venv\Scripts\python.exe`, and `Get-CimInstance`. The FastAPI app underneath runs anywhere, but you would start uvicorn yourself.
- **YouTube breaks things.** `yt-dlp` needs regular updating, and downloads can fail on age-gated, region-locked, or bot-checked videos.
- **Translation targets Traditional Chinese only**, and parts of the UI, the log messages, and `docs/` are written in Chinese.
- **No tests.**

## License

MIT — see [LICENSE](LICENSE).
