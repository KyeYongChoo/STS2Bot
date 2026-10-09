<#
.SYNOPSIS
  Installs the STS2MCP mod (https://github.com/Gennadiyev/STS2MCP) into a
  Slay the Spire 2 install so sts2bot can talk to the game over localhost.

.PARAMETER GameDir
  Path to the Slay the Spire 2 install directory (the folder containing
  SlayTheSpire2.exe). Defaults to the common Steam location.

.PARAMETER Version
  Release tag to install. Defaults to 0.4.0.

.PARAMETER FromBuild
  Install this project's patched build from vendor/STS2MCP/out instead of
  downloading an upstream release. Needed for the dev loader's deck setting
  (dev_set_deck) and the player.deck / run.act_id fields. Build it first
  with vendor/STS2MCP/build.ps1. Close the game before installing -- it
  locks the loaded DLL.
#>
param(
    [string]$GameDir = "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2",
    [string]$Version = "0.4.0",
    [switch]$FromBuild
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path (Join-Path $GameDir "SlayTheSpire2.exe"))) {
    throw "SlayTheSpire2.exe not found under '$GameDir'. Pass -GameDir to point at your install."
}

$modsDir = Join-Path $GameDir "mods"
New-Item -ItemType Directory -Force -Path $modsDir | Out-Null

if ($FromBuild) {
    $vendor = Join-Path $PSScriptRoot "..\vendor\STS2MCP"
    $dll = Join-Path $vendor "out\STS2_MCP\STS2_MCP.dll"
    if (-not (Test-Path $dll)) {
        throw "No build at '$dll'. Run vendor\STS2MCP\build.ps1 -GameDir `"$GameDir`" first."
    }
    Copy-Item $dll (Join-Path $modsDir "STS2_MCP.dll") -Force
    Copy-Item (Join-Path $vendor "mod_manifest.json") (Join-Path $modsDir "STS2_MCP.json") -Force
    $installed = "local build ($dll)"
} else {
    $base = "https://github.com/Gennadiyev/STS2MCP/releases/download/$Version"
    $files = @("STS2_MCP.dll", "STS2_MCP.json")

    foreach ($f in $files) {
        $dest = Join-Path $modsDir $f
        Write-Host "Downloading $f -> $dest"
        Invoke-WebRequest -Uri "$base/$f" -OutFile $dest
    }
    $installed = $Version
}

Write-Host ""
Write-Host "Installed STS2MCP $installed into: $modsDir"
Write-Host "Next steps:"
Write-Host "  1. Launch Slay the Spire 2."
Write-Host "  2. Enable mods in game settings (a consent dialog appears the first time)."
Write-Host "  3. Verify the API is up:  curl.exe http://localhost:15526/api/v1/singleplayer"
Write-Host "  4. Start a run manually (pick a character, get to the map or first combat)."
Write-Host "  5. Run the bot: python run.py"
