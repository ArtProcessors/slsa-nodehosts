# ============================================================================
#  update-server.ps1  -  pull + install the latest SLSA Play node server build
#  from CircleCI, WITHOUT starting it (the Nodel "SLSA PLay Node Server" node
#  owns starting/stopping the server via start-server.bat).
#
#  Invoked by this node's custom_update.py ("Check For Update" / "Deploy
#  Update" actions). Run it standalone too if you want a manual update without
#  a server start.
#
#  What a deploy does: CircleCI artifact discovery (build-and-package job on
#  $Branch) -> download zip -> wipe + extract into <DeployDir>\server ->
#  copy <DeployDir>\config\.env in -> write <DeployDir>\.current-version ->
#  `corepack yarn workspaces focus slsa-play-node --production` (the zip ships
#  dist/, public/, package.json, yarn.lock, ui/package.json and .nvmrc but NO
#  node_modules - runtime deps incl. natives like sharp are installed on this
#  box; the UI is pre-built into public\ui so its toolchain isn't needed).
#
#  Output convention: prints `KEY=VALUE` lines that custom_update.py parses:
#    DEPLOYED_VERSION=<.current-version contents, or empty>
#    LATEST_VERSION=<latest successful CircleCI build version, or empty>
#    UPDATE_AVAILABLE=True|False
#    UPDATE_COMPLETE=<version>           (only on a successful deploy)
#  Exit codes: 0 = ok / nothing to do / deploy succeeded ; 10 = (with -CheckOnly) update available ; non-zero = error.
# ============================================================================
[CmdletBinding()]
param(
  [string]$DeployDir      = "C:\Content",
  [string]$Branch         = "main",
  [string]$ProjectSlug    = "github/ArtProcessors/slsa-play-node",
  [string]$ArtifactPrefix = "slsa-play-node",
  [switch]$CheckOnly      # only report deployed-vs-latest; don't download/install
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ServerDir   = Join-Path $DeployDir "server"
$ConfigDir   = Join-Path $DeployDir "config"
$VersionFile = Join-Path $DeployDir ".current-version"

function Get-CircleToken {
  if ($env:CIRCLE_TOKEN) { return $env:CIRCLE_TOKEN }
  $tokenFile = Join-Path $DeployDir ".circle-token"
  if (Test-Path $tokenFile) { return (Get-Content $tokenFile -Raw).Trim() }
  throw "No CIRCLE_TOKEN env var and no .circle-token file in $DeployDir"
}

function Get-LatestArtifact {
  param([string]$Token)
  $headers = @{ "Circle-Token" = $Token }
  $base = "https://circleci.com/api/v2"
  $pipelines = Invoke-RestMethod -Uri "$base/project/$ProjectSlug/pipeline?branch=$Branch" -Headers $headers
  if (-not $pipelines.items -or $pipelines.items.Count -eq 0) { return $null }
  foreach ($pipeline in $pipelines.items) {
    $workflows = Invoke-RestMethod -Uri "$base/pipeline/$($pipeline.id)/workflow" -Headers $headers
    $ok = $workflows.items | Where-Object { $_.status -eq "success" } | Select-Object -First 1
    if (-not $ok) { continue }
    $jobs = Invoke-RestMethod -Uri "$base/workflow/$($ok.id)/job" -Headers $headers
    $build = $jobs.items | Where-Object { $_.name -eq "build-and-package" -and $_.status -eq "success" } | Select-Object -First 1
    if (-not $build) { continue }
    $arts = Invoke-RestMethod -Uri "$base/project/$ProjectSlug/$($build.job_number)/artifacts" -Headers $headers
    $zip = $arts.items | Where-Object { $_.path -like "*.zip" } | Select-Object -First 1
    if (-not $zip) { continue }
    $fn = Split-Path $zip.path -Leaf
    $ver = $fn -replace "^$ArtifactPrefix-", "" -replace "\.zip$", ""
    return @{ Url = $zip.url; Version = $ver; Filename = $fn }
  }
  return $null
}

function Get-DeployedVersion {
  if (Test-Path $VersionFile) { return (Get-Content $VersionFile -Raw).Trim() }
  return $null
}

# --- discovery / version check ---------------------------------------------
$token    = Get-CircleToken
$deployed = Get-DeployedVersion
$artifact = Get-LatestArtifact -Token $token

Write-Host "DEPLOYED_VERSION=$deployed"
Write-Host "LATEST_VERSION=$($artifact.Version)"
$updateAvailable = [bool]($artifact -and $artifact.Version -and $artifact.Version -ne $deployed)
Write-Host "UPDATE_AVAILABLE=$updateAvailable"

if ($CheckOnly) { if ($updateAvailable) { exit 10 } else { exit 0 } }
if (-not $updateAvailable) { Write-Host "Nothing to do - already on '$deployed'."; exit 0 }

# --- deploy the new build (no server start) ----------------------
# Load fnm into this session so `node` / `corepack` (and therefore yarn) resolve.
if (Get-Command fnm -ErrorAction SilentlyContinue) {
  fnm env --shell powershell | Out-String | Invoke-Expression
}

# Stage under the deploy dir - NOT $env:TEMP: under the Nodel host, TEMP can be
# an 8.3 short path (C:\Users\ARTPRO~1\...) whose "~" PowerShell's item cmdlets
# mis-handle ("An object at the specified path ... does not exist"). External
# tools (robocopy, IWR's .NET internals) don't care, but staying on a clean
# long path avoids the whole class of problem.
$StagingDir = Join-Path $DeployDir ".staging"
if (Test-Path $StagingDir) { Remove-Item $StagingDir -Recurse -Force }
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

$tempZip = Join-Path $StagingDir $artifact.Filename
Write-Host "Downloading $($artifact.Filename) ..."
Invoke-WebRequest -Uri $artifact.Url -Headers @{ "Circle-Token" = $token } -OutFile $tempZip

# Stop any running node processes (the App Launcher node should already have stopped its child).
$np = Get-Process -Name node -ErrorAction SilentlyContinue
if ($np) { Write-Host "Stopping running node processes..."; $np | Stop-Process -Force; Start-Sleep -Seconds 2 }

# Wipe the server directory (robocopy /MIR handles long node_modules paths on Windows).
if (Test-Path $ServerDir) {
  Write-Host "Clearing $ServerDir ..."
  $empty = Join-Path $StagingDir "empty"
  New-Item -ItemType Directory -Path $empty -Force | Out-Null
  robocopy $empty $ServerDir /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
  Remove-Item $empty -Force
  Remove-Item $ServerDir -Force
}
New-Item -ItemType Directory -Path $ServerDir -Force | Out-Null

Write-Host "Extracting ..."
Expand-Archive -Path $tempZip -DestinationPath $ServerDir -Force
Remove-Item $StagingDir -Recurse -Force

foreach ($c in @(".env")) {
  $src = Join-Path $ConfigDir $c
  if (Test-Path $src) { Copy-Item $src -Destination $ServerDir; Write-Host "Copied $c" }
  elseif ($c -eq ".env") { Write-Host "WARNING: no .env in $ConfigDir" }
}

$artifact.Version | Out-File -FilePath $VersionFile -NoNewline -Encoding utf8
Write-Host "Version updated to: $($artifact.Version)"

# Install runtime deps for the server workspace only - the UI is pre-built into
# public\ui so its React/Vite toolchain isn't needed. Resolves strictly from
# the shipped yarn.lock; the exact Yarn version comes from package.json's
# "packageManager" field (corepack fetches it silently - no prompt, thanks to
# COREPACK_ENABLE_DOWNLOAD_PROMPT=0).
Write-Host "Installing dependencies (yarn workspaces focus --production) ..."
$env:CI = "1"
$env:COREPACK_ENABLE_DOWNLOAD_PROMPT = "0"

# Guard for artifacts built before .yarnrc.yml shipped in the zip: without it
# Yarn 4 installs as Plug'n'Play (no node_modules) on a fresh extract, and
# `node dist\index.js` then can't resolve modules ("Cannot find module 'ws'").
$yarnrc = Join-Path $ServerDir ".yarnrc.yml"
if (-not (Test-Path $yarnrc)) {
  Set-Content -Path $yarnrc -Value "nodeLinker: node-modules"
  Write-Host "Wrote .yarnrc.yml (nodeLinker: node-modules)"
}

Push-Location $ServerDir
try {
  if (Get-Command fnm -ErrorAction SilentlyContinue) { fnm use --install-if-missing }  # reads server\.nvmrc (shipped in the zip)
  corepack yarn workspaces focus slsa-play-node --production
  if ($LASTEXITCODE -ne 0) { throw "yarn install failed (exit $LASTEXITCODE)" }
} finally {
  Pop-Location
}

Write-Host "UPDATE_COMPLETE=$($artifact.Version)"
exit 0
