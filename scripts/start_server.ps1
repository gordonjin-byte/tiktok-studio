# Launches the TikTok Studio server (used by the Scheduled Task and manually).
$AppDir  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir  = Join-Path $env:USERPROFILE "TikTokStudio\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# kill a previous instance on the port so restarts are clean
$existing = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $existing | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}

Set-Location $AppDir
& "$AppDir\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765 `
    *>> (Join-Path $LogDir "server.log")
