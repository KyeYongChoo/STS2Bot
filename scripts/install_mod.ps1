<#
.SYNOPSIS
  Installs the STS2MCP mod (https://github.com/Gennadiyev/STS2MCP) into a
  Slay the Spire 2 install so sts2bot can talk to the game over localhost.

.PARAMETER GameDir
  Path to the Slay the Spire 2 install directory (the folder containing
  SlayTheSpire2.exe). Defaults to the common Steam location.

.PARAMETER Version
  Release tag to install. Defaults to 0.4.0.
#>
param(
    [string]$GameDir = "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2",
    [string]$Version = "0.4.0"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path (Join-Path $GameDir "SlayTheSpire2.exe"))) {
    throw "SlayTheSpire2.exe not found under '$GameDir'. Pass -GameDir to point at your install."
}

$modsDir = Join-Path $GameDir "mods"
New-Item -ItemType Directory -Force -Path $modsDir | Out-Null

$base = "https://github.com/Gennadiyev/STS2MCP/releases/download/$Version"
$files = @("STS2_MCP.dll", "STS2_MCP.json")

foreach ($f in $files) {
    $dest = Join-Path $modsDir $f
    Write-Host "Downloading $f -> $dest"
    Invoke-WebRequest -Uri "$base/$f" -OutFile $dest
}

Write-Host ""
Write-Host "Installed STS2MCP $Version into: $modsDir"
Write-Host "Next steps:"
Write-Host "  1. Launch Slay the Spire 2."
Write-Host "  2. Enable mods in game settings (a consent dialog appears the first time)."
Write-Host "  3. Verify the API is up:  curl.exe http://localhost:15526/api/v1/singleplayer"
Write-Host "  4. Start a run manually (pick a character, get to the map or first combat)."
Write-Host "  5. Run the bot: python run.py"
