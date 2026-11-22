# Options:
#  -NoBuild     skip the (slow) docker image build
# -BackendOnly  start only the backend
# -Reseed       re-publish web\master_aed_dane_gov.csv as the portal's AED resource (otherwise done once)
# If scripts are blocked: powershell -ExecutionPolicy Bypass -File .\run.ps1
param(
    [switch]$NoBuild,
    [switch]$BackendOnly,
    [switch]$Reseed
)
$ErrorActionPreference = 'Stop'

$Root     = $PSScriptRoot
$Backend  = Join-Path $Root 'backend_latest'
$Frontend = Join-Path $Root 'frontend_latest'
$State    = Join-Path $Root '.initialized'

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments

    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

function Dc { Invoke-Checked 'docker' (@('compose', '-f', 'docker-compose.local.yml') + $args) }

foreach ($cmd in 'docker', 'node', 'npm') {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "Missing required tool: $cmd" }
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker daemon is not running. Start Docker Desktop and retry.' }

# config files
Set-Location $Backend
$utf8 = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path '.env')) {

    Write-Host '>> Creating backend .env'
    $bytes = New-Object byte[] 32

    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)

    $key = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
    $text = [System.IO.File]::ReadAllText((Join-Path $Backend 'env.sample'))
    $text = $text -replace '(?m)^FIELD_ENCRYPTION_KEYS=.*$', "FIELD_ENCRYPTION_KEYS=$key"

    # Discourse image lives in a private registry; disable the forum by default.
    $text = $text -replace '(?m)^DISCOURSE_FORUM_ENABLED=.*$', 'DISCOURSE_FORUM_ENABLED=false'
    [System.IO.File]::WriteAllText((Join-Path $Backend '.env'), $text, $utf8)

}

if (-not (Test-Path 'mcod/settings/local.py')) {
    Write-Host '>> Creating mcod/settings/local.py'
    Copy-Item 'mcod/settings/local.py.sample' 'mcod/settings/local.py'
}

$hostsFile = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
if (-not (Select-String -Path $hostsFile -Pattern 'api\.mcod\.local' -Quiet)) {
    Write-Host '!! The hosts file is missing the mcod.local entries. In an ADMINISTRATOR PowerShell run once:' -ForegroundColor Yellow
    Write-Host "   Add-Content -Path '$hostsFile' -Value '127.0.0.1 mcod.local api.mcod.local admin.mcod.local cms.mcod.local forum.mcod.local mail.mcod.local'"
    exit 1
}

if (-not $NoBuild) {
    Write-Host '>> Building backend images (can take a long time)'
    Dc build
}

Write-Host '>> Starting dependencies'
Invoke-Checked 'docker' @('compose', 'up', '-d', 'mcod-db', 'mcod-elasticsearch', 'mcod-rabbitmq', 'mcod-redis', 'mcod-rdfdb', 'mcod-smtp', 'mcod-nginx')

Write-Host '>> Waiting for Postgres and Elasticsearch'
do { docker exec mcod-db pg_isready -U mcod *> $null; if ($LASTEXITCODE -ne 0) { Start-Sleep 2 } } while ($LASTEXITCODE -ne 0)

while ($true) {
    try { Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:9200/_cluster/health' -TimeoutSec 3 | Out-Null; break }
    catch { Start-Sleep 3 }
}

# Each first-run step is recorded separately in $State, so a failure in a later
# step doesn't make a retry repeat earlier ones (loaddata is not re-runnable).
if (-not (Test-Path $State)) { New-Item -ItemType File -Path $State | Out-Null }
if (-not (Select-String -Path $State -Pattern '^db$' -Quiet)) {
    Write-Host '>> First run: migrations + initial data'
    Dc run --rm --no-deps mcod-local-admin python manage.py init_mcod_db
    Add-Content -Path $State -Value 'db'
}
if (-not (Select-String -Path $State -Pattern '^es$' -Quiet)) {
    Write-Host '>> First run: building Elasticsearch indexes'
    Dc run --rm --no-deps mcod-local-admin python manage.py search_index --rebuild -f
    Add-Content -Path $State -Value 'es'
}

Write-Host '>> Starting backend services'
Dc up -d mcod-local-api mcod-local-admin mcod-local-cms mcod-local-celery

# Publish the AED register as a map-ready portal resource (needs celery + Elasticsearch running).
# Source: web\master_aed_dane_gov.csv (web app running without a database), else the web app's CSV export
# at $env:AED_CSV_URL, e.g.  $env:AED_CSV_URL='https://<your-app>/dane-gov-export.csv'; .\run.ps1 -Reseed
# The resource id is handed to the frontend through assets/aed/config.json.
$SeedCsv    = Join-Path $Root 'web\master_aed_dane_gov.csv'
$SeedTarget = Join-Path $Backend 'data\aed_master.csv'   # ./data is mounted into the containers
if ($Reseed -or -not (Select-String -Path $State -Pattern '^seed$' -Quiet)) {
    $haveSeed = $false
    New-Item -ItemType Directory -Force -Path (Join-Path $Backend 'data') | Out-Null
    if (Test-Path $SeedCsv) {
        Copy-Item $SeedCsv $SeedTarget -Force
        $haveSeed = $true
    } elseif ($env:AED_CSV_URL) {
        Write-Host ">> Downloading the AED register from $($env:AED_CSV_URL)"
        try { Invoke-WebRequest -UseBasicParsing -Uri $env:AED_CSV_URL -OutFile $SeedTarget; $haveSeed = $true }
        catch { Write-Host "!! Could not download $($env:AED_CSV_URL)" -ForegroundColor Yellow }
    } else {
        Write-Host "-- No AED data to publish: set AED_CSV_URL to the web app's /dane-gov-export.csv (or create $SeedCsv), then run with -Reseed."
    }

    if ($haveSeed) {
        Write-Host '>> Publishing the AED register as a portal resource'
        # Native stderr must not become a terminating error while we capture the output.
        $ErrorActionPreference = 'Continue'
        $seedLog = & docker compose -f docker-compose.local.yml run --rm -T --no-deps mcod-local-admin python manage.py seed_aed /usr/src/mcod_backend/data/aed_master.csv 2>&1 | ForEach-Object { "$_" }
        $seedOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = 'Stop'

        if ($seedOk) {
            $seedLog | Where-Object { $_ -match '^(Resource|Processing|Skipped)' } | ForEach-Object { Write-Host $_ }
            $resId = $seedLog | ForEach-Object { if ($_ -match '^Resource [a-z]+: id=(\d+)') { $Matches[1] } } | Select-Object -Last 1
            if ($resId) {
                $aedAssets = Join-Path $Frontend 'src\assets\aed'
                New-Item -ItemType Directory -Force -Path $aedAssets | Out-Null
                [System.IO.File]::WriteAllText((Join-Path $aedAssets 'config.json'), "{`"resourceId`": `"$resId`"}`n", $utf8)
            }
            if (-not (Select-String -Path $State -Pattern '^seed$' -Quiet)) { Add-Content -Path $State -Value 'seed' }
        } else {
            $seedLog | Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
            Write-Host '!! Publishing the AED register failed; the map will show sample data. Retry with -Reseed.' -ForegroundColor Yellow
        }
    }
}

Write-Host ''
Write-Host 'Backend:  https://api.mcod.local  (docs: /doc)'
Write-Host 'Admin:    https://admin.mcod.local  (admin@mcod.local / testadmin123!)'
Write-Host 'Stop:     cd backend_latest; docker compose -f docker-compose.local.yml down'
Write-Host ''

# frontend
if (-not $BackendOnly) {
    Set-Location $Frontend
    if (-not (Test-Path 'node_modules')) { Write-Host '>> npm install'; Invoke-Checked 'npm' @('install') }
    Write-Host '>> Starting frontend on http://localhost:4200'
    Invoke-Checked 'npm' @('run', 'start:local')
}
