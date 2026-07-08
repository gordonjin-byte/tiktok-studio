# TikTok Studio

Local, single-user web app that turns raw teleprompter talking-head recordings into
TikTok-ready edits automatically: drop in a video → get a fully edited 1080×1920
vertical video in **3 hook treatments**, with retakes removed, dead air cut, animated
captions burned in, punch-in zooms, SFX, optional ducked music, and loudness
normalized to -14 LUFS.

Runs at **http://127.0.0.1:8765** (launchd service, auto-starts on login).

## How it works

```
drop video (UI or ~/TikTokStudio/inbox)
  → ingest        sha256 dedupe, probe, extract audio
  → analyze       RMS energy envelope + silence map (cached per video)
  → transcribe    whisper.cpp small.en on silence-stripped "dense" audio
                  → word-accurate timestamps mapped back to source (cached)
  → brain         `claude -p` decides: which retake to keep, caption keywords,
                  hook/banner/CTA text (heuristic fallback if CLI fails; cached)
  → edl           settings + facts → cut list, zoom schedule, caption chunks
  → render ×3     ffmpeg filter_complex: video-only zoom cuts over continuous
                  audio spans, ASS captions, SFX, music ducking, grade, loudnorm
  → qc            re-transcribe output, word-diff vs intended script;
                  auto-widen clipped splices and re-render (max 2 retries)
```

Analysis and brain results are cached: **changing any setting and re-rendering
skips transcription entirely** (seconds of Python + one ffmpeg encode).

## Layout

- Code: `~/dev/tiktok-studio` (FastAPI + vanilla Preact, no build step)
- Data: `~/TikTokStudio` (originals, analysis artifacts, renders, music/, sfx/, sqlite db)
- Music library: drop `.wav/.mp3/.m4a` files into `~/TikTokStudio/music` — they appear
  in the Music → Track selector. Ships with 2 synthesized ambient loops + whoosh/pop SFX.

## Hook variants

| variant | treatment |
|---|---|
| Cold Open | speech at t=0, oversized centered first caption, punch-in |
| Title Card | 0.8s darkened freeze-frame with AI-written title, whoosh into speech |
| Question Hook | AI-written question in a yellow banner for the first 3s, rapid captions |

## Windows setup

Requirements: Python 3.11+, winget (standard on Win 10/11), and optionally
[Claude Code for Windows](https://claude.com/claude-code) signed in (for AI edit
decisions; falls back to heuristics without it).

```powershell
git clone <repo-url> tiktok-studio
cd tiktok-studio
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The installer sets up the venv, installs ffmpeg via winget (Gyan build, includes
libass), downloads whisper.cpp binaries + model into `%USERPROFILE%\TikTokStudio\tools`,
seeds SFX/music, and registers a **Scheduled Task** so the server starts at logon.
Open http://127.0.0.1:8765. Manual start: `run.bat`. Restart after code changes:
`Start-ScheduledTask -TaskName TikTokStudio` (it kills the old instance first).
Logs: `%USERPROFILE%\TikTokStudio\logs\server.log`.

Tool paths are auto-detected (PATH, then `~/TikTokStudio/tools`); override with env
vars `TIKTOKSTUDIO_FFMPEG`, `TIKTOKSTUDIO_WHISPER`, `TIKTOKSTUDIO_CLAUDE`. Check
resolution at `/api/stats` → `binaries`.

## macOS operations

```bash
# install / redeploy service
scripts/install.sh

# restart after code changes
launchctl kickstart -k gui/$(id -u)/com.gordonjin.tiktokstudio

# logs
tail -f ~/TikTokStudio/logs/launchd.err

# headless pipeline (no web UI)
.venv/bin/python -m app.pipeline.run VIDEO.mp4 [--no-brain] [--force-analyze] [--force-brain] [--variants hook_a]
```

## Notes

- The `claude` CLI is invoked non-interactively (`claude -p`) and uses your existing
  Claude subscription; if it times out or errors, the pipeline degrades to heuristic
  edit decisions and the video is badged "heuristic edit" with a ↻ regenerate button.
- Everything runs locally; the server binds 127.0.0.1 only.
- QC "warnings" status means the output transcript didn't fully match the intended
  script after retries — check the QC panel's missing-words list and stills.
