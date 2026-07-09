# TikTok Studio — Windows installer.
# Run from PowerShell in the repo root:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# Sets up: data dirs, python venv, ffmpeg (winget), whisper.cpp binary + model,
# seed SFX/music, and a logon Scheduled Task so the server auto-starts.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # 10x faster Invoke-WebRequest on PS 5.1
$AppDir  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DataDir = Join-Path $env:USERPROFILE "TikTokStudio"
$Model   = Join-Path $DataDir "models\ggml-small.en.bin"
$WhisperDir = Join-Path $DataDir "tools\whisper"

Write-Host "-> data directories"
foreach ($d in @("inbox","originals","artifacts","renders","music","sfx","models","tools","tmp","logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $d) | Out-Null
}

Write-Host "-> python venv"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python 3.11+ not found. Install from https://python.org or 'winget install Python.Python.3.12' and re-run." }
if (-not (Test-Path "$AppDir\.venv\Scripts\python.exe")) {
    python -m venv "$AppDir\.venv"
}
& "$AppDir\.venv\Scripts\pip.exe" install -q -r "$AppDir\requirements.txt"

Write-Host "-> ffmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "   installing via winget (Gyan.FFmpeg includes libass for captions)..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Write-Host "   NOTE: restart this PowerShell window if ffmpeg is still not found (PATH refresh)."
}

Write-Host "-> whisper.cpp"
if (-not (Test-Path (Join-Path $WhisperDir "whisper-cli.exe")) -and -not (Test-Path (Join-Path $WhisperDir "main.exe"))) {
    New-Item -ItemType Directory -Force -Path $WhisperDir | Out-Null
    $zip = Join-Path $env:TEMP "whisper-bin-x64.zip"
    Write-Host "   downloading whisper.cpp release binaries..."
    Invoke-WebRequest -Uri "https://github.com/ggerganov/whisper.cpp/releases/latest/download/whisper-bin-x64.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $WhisperDir -Force
    Remove-Item $zip
}

Write-Host "-> whisper model (488MB)"
if (-not (Test-Path $Model)) {
    Invoke-WebRequest -Uri "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin" -OutFile $Model
}

Write-Host "-> claude CLI check"
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "   WARN: claude CLI not found - AI edit decisions will use the heuristic fallback."
    Write-Host "   Install Claude Code for Windows and sign in, then restart the server."
}

Write-Host "-> seed sfx/music"
if (-not (Test-Path (Join-Path $DataDir "sfx\whoosh.wav"))) {
    & "$AppDir\.venv\Scripts\python.exe" "$AppDir\scripts\seed_assets.py" (Join-Path $DataDir "sfx") (Join-Path $DataDir "music")
}

Write-Host "-> scheduled task (auto-start at logon)"
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$AppDir\scripts\start_server.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "TikTokStudio" -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null

Write-Host "-> starting server"
Start-ScheduledTask -TaskName "TikTokStudio"
Start-Sleep -Seconds 5
try {
    Invoke-RestMethod "http://127.0.0.1:8765/api/stats" | Out-Null
    Write-Host "OK - TikTok Studio running at http://127.0.0.1:8765" -ForegroundColor Green
} catch {
    Write-Host "Server not responding yet - check $DataDir\logs\server.log" -ForegroundColor Yellow
}
