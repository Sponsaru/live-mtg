param(
  [string]$Version = "latest",
  [switch]$NoOnboard,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Invoke-Step([string]$Command, [string[]]$Arguments) {
  if ($DryRun) { Write-Host "+ $Command $($Arguments -join ' ')"; return }
  & $Command @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
}

Write-Host "LiveMTG installer"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Invoke-Step "winget" @("install", "OpenJS.NodeJS.LTS", "--accept-package-agreements", "--accept-source-agreements")
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
  } else { throw "Node.js 20 or later is required: https://nodejs.org/" }
}

$major = [int]((& node -p 'Number(process.versions.node.split(".")[0])').Trim())
if ($major -lt 20) { throw "Node.js 20 or later is required" }

if (-not (Get-Command python -ErrorAction SilentlyContinue) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
  Invoke-Step "winget" @("install", "Python.Python.3.12", "--accept-package-agreements", "--accept-source-agreements")
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
  Invoke-Step "winget" @("install", "Gyan.FFmpeg", "--accept-package-agreements", "--accept-source-agreements")
}

Invoke-Step "npm" @("install", "-g", "live-mtg@$Version")
if (-not $NoOnboard) { Invoke-Step "live-mtg" @("onboard") }
else { Write-Host "Installation complete. Run: live-mtg onboard" }
