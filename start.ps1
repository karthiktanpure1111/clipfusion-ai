# ClipFusion AI - start everything on this PC (Windows).
#   Right-click > Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File start.ps1
# Optional:  -Dev     run the website in development mode (slower, reloads on code changes)
#            -Celery  use Redis + Celery workers instead of the built-in worker
#            -ApiPort the ClipFusion API port (default 8010; port 8000 is often used by other programs)
param([switch]$Dev, [switch]$Celery, [int]$ApiPort = 8010, [int]$WebPort = 3000)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$data = Join-Path $backend 'data'
$logs = Join-Path $data 'logs'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$envFile = Join-Path $root '.env'
$apiBase = "http://localhost:$ApiPort"
$apiUrl = "$apiBase/api/v1"
$env:Path = "$env:USERPROFILE\scoop\shims;$env:Path"
New-Item -ItemType Directory -Force $logs | Out-Null

function Test-Port([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try { $client.Connect('127.0.0.1', $Port); return $true } catch { return $false } finally { $client.Dispose() }
}

function Wait-Port([int]$Port, [string]$Name, [int]$Seconds = 60) {
    for ($i = 0; $i -lt $Seconds; $i++) { if (Test-Port $Port) { Write-Host "  $Name is ready"; return }; Start-Sleep 1 }
    throw "$Name did not start. See the log files in $logs"
}

function Start-Hidden([string]$Exe, [string[]]$Arguments, [string]$Dir, [string]$Log) {
    Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $Dir -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "$Log.log") -RedirectStandardError (Join-Path $logs "$Log.err.log")
}

# Name the program that listens on a port (for clear error messages; nothing is stopped).
function Get-PortOwner([int]$Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction Stop
        $path = try { $proc.Path } catch { $null }
        $name = "$($proc.ProcessName) (PID $($proc.Id))"
        if ($proc.ProcessName -match 'com\.docker|docker|wslrelay|vpnkit') { $name += ' - a Docker container' }
        if ($path) { $name += " - $path" }
        return $name
    } catch { return 'another program' }
}

# True when the API on $Port really is ClipFusion (its capability report has worker_mode).
function Test-ClipFusionApi([int]$Port) {
    try {
        $caps = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/system/capabilities" -TimeoutSec 5
        return ($null -ne $caps.worker_mode) -and (($null -eq $caps.app) -or ($caps.app -eq 'clipfusion'))
    } catch { return $false }
}

# Set a non-secret KEY=value line in .env (added when missing; the old :8000 default is moved).
function Set-EnvLine([string]$Key, [string]$Value, [string]$ReplacePattern) {
    $lines = @(Get-Content $envFile -Encoding UTF8)
    $found = $false
    $changed = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$Key\s*=") {
            $found = $true
            if ($ReplacePattern -and $lines[$i] -match $ReplacePattern) { $lines[$i] = "$Key=$Value"; $changed = $true }
        }
    }
    if (-not $found) { $lines += "$Key=$Value"; $changed = $true }
    if ($changed) {
        [IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
        Write-Host "  .env: $Key=$Value"
    }
}

foreach ($tool in 'ffmpeg', 'pg_ctl', 'node') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool was not found. See README.md for the setup steps." }
}
if (-not (Test-Path $python)) { throw 'backend\.venv is missing. See README.md for the setup steps.' }
if (-not (Test-Path $envFile)) {
    @('ENVIRONMENT=development', 'AUTH_REQUIRED=false',
      'DATABASE_URL=postgresql+psycopg://clipfusion@localhost:5433/clipfusion',
      'WORKER_MODE=local', 'STORAGE_BACKEND=local', 'WHISPER_MODEL=small') |
        Set-Content -Encoding ascii $envFile
}
# The API moved to port 8010: OAuth redirect URIs and signed media URLs follow BACKEND_PUBLIC_URL.
Set-EnvLine 'BACKEND_PUBLIC_URL' $apiBase '^\s*BACKEND_PUBLIC_URL\s*=\s*http://(localhost|127\.0\.0\.1):8000/?\s*$'

# Refuse early when another program holds the API or website port.
if ((Test-Port $ApiPort) -and -not (Test-ClipFusionApi $ApiPort)) {
    throw "Port $ApiPort is used by $(Get-PortOwner $ApiPort), not by ClipFusion. Stop that program or start ClipFusion on another port: start.ps1 -ApiPort 8020 (then update BACKEND_PUBLIC_URL in .env and your Google OAuth redirect URI)."
}

Write-Host 'Starting the database...'
$pg = Join-Path $data 'pg'
$newCluster = -not (Test-Path $pg)
if ($newCluster) { & initdb -D $pg -U clipfusion --auth=trust -E UTF8 --locale=C | Out-Null }
if (-not (Test-Port 5433)) {
    Start-Hidden 'pg_ctl' @('-D', "`"$pg`"", '-o', '"-p 5433 -c listen_addresses=localhost"', '-l', "`"$logs\postgres.log`"", 'start') $backend 'pg_ctl'
}
Wait-Port 5433 'Database'
for ($i = 0; $i -lt 30; $i++) { & pg_isready -h localhost -p 5433 -q; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 1 }
$exists = & psql -h localhost -p 5433 -U clipfusion -d postgres -Atc "select 1 from pg_database where datname='clipfusion'"
if ($exists -ne '1') { & createdb -h localhost -p 5433 -U clipfusion clipfusion }

# Alembic logs to stderr, which Windows PowerShell 5.1 would treat as an error: run it through cmd.
Push-Location $backend
& cmd /c "`"$python`" -m alembic upgrade head > `"$logs\migrate.log`" 2>&1"
$migrated = $LASTEXITCODE
Pop-Location
if ($migrated -ne 0) { throw "Database update failed. See $logs\migrate.log" }

$env:WORKER_MODE = if ($Celery) { 'celery' } else { 'local' }
$env:BACKEND_PUBLIC_URL = $apiBase
if ($Celery) {
    Write-Host 'Starting Redis and the workers...'
    New-Item -ItemType Directory -Force (Join-Path $data 'redis') | Out-Null
    if (-not (Test-Port 6379)) { Start-Hidden 'redis-server' @('--port', '6379', '--bind', '127.0.0.1', '--appendonly', 'yes') (Join-Path $data 'redis') 'redis' }
    Wait-Port 6379 'Redis'
    Start-Hidden $python @('-m', 'celery', '-A', 'app.workers.celery_app', 'worker', '-Q', 'analysis,render,publish,default', '--pool=threads', '-c', '2', '--loglevel', 'INFO') $backend 'worker'
    Start-Hidden $python @('-m', 'celery', '-A', 'app.workers.celery_app', 'beat', '--loglevel', 'INFO', '--schedule', "`"$data\celerybeat-schedule`"") $backend 'beat'
}

Write-Host "Starting the API on port $ApiPort..."
if (Test-ClipFusionApi $ApiPort) { Write-Host '  ClipFusion API is already running' }
else {
    Start-Hidden $python @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$ApiPort") $backend 'api'
    Wait-Port $ApiPort 'API' 90
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) { if (Test-ClipFusionApi $ApiPort) { $ok = $true; break }; Start-Sleep 1 }
    if (-not $ok) {
        throw "Something answers on port $ApiPort but it is not the ClipFusion API ($(Get-PortOwner $ApiPort)). See $logs\api.err.log"
    }
}

# HTTPS forwarder for Meta sign-in redirects (only when INSTAGRAM_REDIRECT_URI is https://localhost:<port>/...)
$httpsCallback = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'scripts[\\/]https_callback\.py' }
if (-not $httpsCallback) { Start-Hidden $python @('scripts\https_callback.py') $backend 'https-callback' }

Write-Host 'Starting the website...'
$env:NEXT_PUBLIC_API_URL = $apiUrl
if (Test-Port $WebPort) {
    $owner = Get-PortOwner $WebPort
    if ($owner -notmatch 'node') { throw "Port $WebPort is used by $owner, not by the ClipFusion website. Stop that program or use start.ps1 -WebPort 3001." }
    Write-Host '  Website is already running'
}
else {
    if (-not (Test-Path (Join-Path $frontend 'node_modules'))) { Push-Location $frontend; & cmd /c 'npm ci'; Pop-Location }
    if (-not $Dev) {
        $dist = if ($env:NEXT_DIST_DIR) { $env:NEXT_DIST_DIR } else { '.next' }
        $buildId = Join-Path $frontend "$dist\BUILD_ID"
        $stamp = Join-Path $frontend "$dist\clipfusion-api-url.txt"
        $reason = $null
        if (-not (Test-Path $buildId)) { $reason = 'first run' }
        elseif (-not (Test-Path $stamp) -or ((Get-Content $stamp -Raw).Trim() -ne $apiUrl)) { $reason = "API address is now $apiUrl" }
        else {
            $built = (Get-Item $buildId).LastWriteTimeUtc
            $newer = Get-ChildItem -Path (Join-Path $frontend 'src'), (Join-Path $frontend 'public') -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTimeUtc -gt $built } | Select-Object -First 1
            if (-not $newer) {
                $newer = Get-ChildItem -Path $frontend -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match '^(package\.json|package-lock\.json|next\.config\.\w+|tsconfig\.json|tailwind\.config\.\w+|postcss\.config\.\w+)$' -and $_.LastWriteTimeUtc -gt $built } |
                    Select-Object -First 1
            }
            if ($newer) { $reason = 'the website code changed' }
        }
        if ($reason) {
            Write-Host "  Building the website ($reason)..."
            Push-Location $frontend; & cmd /c "npm run build > `"$logs\web-build.log`" 2>&1"; $buildExit = $LASTEXITCODE; Pop-Location
            if ($buildExit -ne 0) { throw "Website build failed. See $logs\web-build.log" }
            Set-Content -Encoding ascii -Path $stamp -Value $apiUrl
        }
    }
    $mode = if ($Dev) { 'dev' } else { 'start' }
    Start-Hidden 'cmd' @('/c', "npx next $mode -H 127.0.0.1 -p $WebPort") $frontend 'web'
}
Wait-Port $WebPort 'Website' 120

Write-Host ''
Write-Host "ClipFusion AI is running:  http://localhost:$WebPort/app   (API: $apiUrl)" -ForegroundColor Green
Write-Host 'To stop it, run stop.ps1'
Start-Process "http://localhost:$WebPort/app"
