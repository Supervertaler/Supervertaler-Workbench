Param(
  [switch]$Clean
)

$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

function Get-Version {
  $content = Get-Content -Raw -Encoding UTF8 .\pyproject.toml
  $m = [regex]::Match($content, 'version\s*=\s*"([^"]+)"')
  if ($m.Success) { return $m.Groups[1].Value }
  return 'unknown'
}

function Ensure-Venv($path, $basePython) {
  if (!(Test-Path $path)) {
    & $basePython -m venv $path
  }
}

# Every package is held to the version in build-constraints.txt, so a build
# ships what was tested rather than whatever was published that morning.
function Install-Build-Tooling($py) {
  & $py -m pip install -q -U pip
  & $py -m pip install -q -c build-constraints.txt setuptools wheel pyinstaller pyinstaller-hooks-contrib
}

$version = Get-Version
Write-Host ("Supervertaler version: v$version") -ForegroundColor Green

# Find base Python
$basePython = $null
if (Test-Path .\.venv\Scripts\python.exe) {
  $basePython = Resolve-Path .\.venv\Scripts\python.exe
} else {
  $basePython = (Get-Command python).Source
}

$venvDir = '.venv-build'

if ($Clean -and (Test-Path $venvDir)) {
  Write-Host "Cleaning build venv..." -ForegroundColor Yellow
  Remove-Item -Recurse -Force $venvDir
}

Ensure-Venv $venvDir $basePython
$py = Resolve-Path (Join-Path $venvDir 'Scripts\python.exe')

Install-Build-Tooling $py

# Install Supervertaler (core only - no local-whisper to avoid PyTorch/PyInstaller conflicts)
# Voice commands run offline with Vosk
Write-Host "Installing Supervertaler with all dependencies..." -ForegroundColor Cyan
& $py -m pip install -q -c build-constraints.txt -e "."
if ($LASTEXITCODE -ne 0) { throw "pip could not install the pinned packages (build-constraints.txt)" }

# Clean build outputs
if ($Clean) {
  if (Test-Path build) { Remove-Item -Recurse -Force build }
}

# Ensure the output folder isn't locked by a running EXE from a previous build
$distDir = 'dist\Supervertaler'
Get-Process Supervertaler -ErrorAction SilentlyContinue | Stop-Process -Force
if (Test-Path $distDir) {
  Remove-Item -Recurse -Force $distDir
}

Write-Host "=== Building Supervertaler EXE via Supervertaler.spec ===" -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean .\Supervertaler.spec
if ($LASTEXITCODE -ne 0) {
  Write-Host "ERROR: PyInstaller failed (exit code $LASTEXITCODE). Aborting build." -ForegroundColor Red
  Write-Host "Check the stderr output above for the underlying cause." -ForegroundColor Yellow
  Write-Host "Common cause: a stale dist/Supervertaler/ directory containing a node_modules/" -ForegroundColor Yellow
  Write-Host "or other deeply-nested tree that Windows can't rmtree. Delete it manually and retry." -ForegroundColor Yellow
  exit $LASTEXITCODE
}

# Copy user_data directly next to the EXE (not inside _internal/)
# This makes dictionaries, prompts, and settings easily accessible to users.
Write-Host "=== Copying user_data to $distDir ===" -ForegroundColor Cyan
$userDataSrc = "user_data"
$userDataDest = Join-Path $distDir "user_data"
if (Test-Path $userDataDest) { Remove-Item -Recurse -Force $userDataDest }

# Create user_data structure with only the files we want to ship
New-Item -ItemType Directory -Path $userDataDest -Force | Out-Null

# Copy dictionaries (Hunspell files)
if (Test-Path "$userDataSrc\dictionaries") {
  Copy-Item -Recurse "$userDataSrc\dictionaries" "$userDataDest\dictionaries"
  Write-Host "  - Copied dictionaries"
}

# Copy Prompt_Library (default prompts)
if (Test-Path "$userDataSrc\Prompt_Library") {
  Copy-Item -Recurse "$userDataSrc\Prompt_Library" "$userDataDest\Prompt_Library"
  Write-Host "  - Copied Prompt_Library"
}

# Copy Translation_Resources
if (Test-Path "$userDataSrc\Translation_Resources") {
  Copy-Item -Recurse "$userDataSrc\Translation_Resources" "$userDataDest\Translation_Resources"
  Write-Host "  - Copied Translation_Resources"
}

# Copy voice_scripts
if (Test-Path "$userDataSrc\voice_scripts") {
  Copy-Item -Recurse "$userDataSrc\voice_scripts" "$userDataDest\voice_scripts"
  Write-Host "  - Copied voice_scripts"
}

# Copy individual files
@("shortcuts.json", "voice_commands.json", "translation_memory.db") | ForEach-Object {
  if (Test-Path "$userDataSrc\$_") {
    Copy-Item "$userDataSrc\$_" "$userDataDest\$_"
    Write-Host "  - Copied $_"
  }
}

# Copy translations/ folder next to the .exe so the Language dropdown
# in Settings → General finds the .xlf files. v1.10.208+ Workbench i18n.
# PyInstaller already bundles a copy via the spec file's `datas` (into
# _internal/translations), but mirroring it next to the exe makes the
# folder visible to end users — a technically-inclined translator can
# drop in additional locale .xlf files without re-extracting the bundle.
Write-Host "=== Copying translations next to the EXE ===" -ForegroundColor Cyan
if (Test-Path "translations") {
  Copy-Item -Recurse "translations" "$distDir\translations"
  $xlfCount = (Get-ChildItem "$distDir\translations" -Filter "*.xlf" | Measure-Object).Count
  Write-Host "  - Copied translations/ ($xlfCount .xlf files)"
} else {
  Write-Host "  - WARNING: translations/ folder missing in source tree" -ForegroundColor Yellow
}

# Copy Start Menu shortcut creation script + double-clickable wrapper.
# End users see the friendly "Add Supervertaler to Start Menu.cmd" in the
# extracted ZIP — double-clicking it runs the .ps1 with -ExecutionPolicy
# Bypass so they don't have to fiddle with right-click "Run with PowerShell"
# or change any system policy.
Write-Host "=== Copying Start Menu shortcut helpers ===" -ForegroundColor Cyan
# These two live in scripts/ in source, but ship at the ROOT of the ZIP so
# end users see them immediately on extraction (no digging into a subfolder
# to find the install helper).
if (Test-Path "scripts\create_start_menu_shortcut.ps1") {
  Copy-Item "scripts\create_start_menu_shortcut.ps1" "$distDir\create_start_menu_shortcut.ps1"
  Write-Host "  - Copied create_start_menu_shortcut.ps1"
}
if (Test-Path "scripts\Add Supervertaler to Start Menu.cmd") {
  Copy-Item "scripts\Add Supervertaler to Start Menu.cmd" "$distDir\Add Supervertaler to Start Menu.cmd"
  Write-Host "  - Copied 'Add Supervertaler to Start Menu.cmd'"
}

$zipPath = "dist\Supervertaler-v$version-Windows.zip"

Write-Host "=== Zipping to $zipPath ===" -ForegroundColor Cyan
& $py .\create_release_zip.py --dist-dir $distDir --output-zip $zipPath --flavor unified

Write-Host "DONE. Release asset: $zipPath" -ForegroundColor Green

# The second release asset: the Claude Desktop extension that connects an AI
# assistant to a running Workbench. The Help page links to it by this exact
# name under releases/latest, so every release must carry it. It needs the
# .NET SDK and the Supervertaler-for-Trados checkout next to this one (the
# MCP server's source lives there); see tools\build_mcpb.py.
Write-Host "=== Building the MCP Server extension (.mcpb) ===" -ForegroundColor Cyan
& $py .\tools\build_mcpb.py --version $version
if ($LASTEXITCODE -eq 0) {
  Write-Host "DONE. Release asset: dist\Supervertaler-Workbench-MCP-Server.mcpb" -ForegroundColor Green
} else {
  Write-Host "WARNING: the MCP Server extension was NOT built. The ZIP above is fine, but attach" -ForegroundColor Red
  Write-Host "  dist\Supervertaler-Workbench-MCP-Server.mcpb to the release too, or the download link" -ForegroundColor Red
  Write-Host "  on docs.supervertaler.com/workbench/ai-translation/mcp-server/ will not work." -ForegroundColor Red
  Write-Host "  Fix the error above and run: python tools\build_mcpb.py --version $version" -ForegroundColor Red
}
