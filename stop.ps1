# ClipFusion AI - stop everything started by start.ps1. Only ClipFusion's own processes are touched:
# its API (uvicorn on the ClipFusion port, default 8010), its Celery workers, the website run from
# this folder, and the database stored in backend\data\pg. Other programs (e.g. a Docker app on port
# 8000) are never stopped.
param([int]$ApiPort = 8010)
$root = $PSScriptRoot
$env:Path = "$env:USERPROFILE\scoop\shims;$env:Path"

Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
        ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'uvicorn app\.main:app' -and $_.CommandLine -match "--port $ApiPort(\s|$)") -or
        ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'celery -A app\.workers\.celery_app') -or
        ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'scripts[\\/]https_callback\.py') -or
        ($_.Name -eq 'node.exe' -and $_.CommandLine.ToLower().Contains((Join-Path $root 'frontend').ToLower()) -and $_.CommandLine -match 'next')
    )
} | ForEach-Object {
    Write-Host "Stopping $($_.Name) ($($_.ProcessId))"
    try { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction Stop } catch { }
}
$pg = Join-Path $root 'backend\data\pg'
if (Test-Path (Join-Path $pg 'postmaster.pid')) { & pg_ctl -D $pg stop -m fast }
Write-Host 'ClipFusion AI is stopped. (Redis, if it was started with -Celery, keeps running: redis-cli shutdown)'
