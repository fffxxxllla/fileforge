param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DepsDir = Join-Path $ProjectDir '.build_deps'
$DistDir = Join-Path $ProjectDir 'dist'
$BuildDir = Join-Path $ProjectDir 'build'

if ($Clean) {
    if (Test-Path -LiteralPath $BuildDir) {
        Remove-Item -LiteralPath $BuildDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $DistDir) {
        Remove-Item -LiteralPath $DistDir -Recurse -Force
    }
}

$env:PYTHONPATH = $DepsDir
python (Join-Path $ProjectDir 'tools\make_icon.py')
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name FileForge `
    --icon (Join-Path $ProjectDir 'assets\fileforge.ico') `
    --version-file (Join-Path $ProjectDir 'version_info.txt') `
    --add-data "$(Join-Path $ProjectDir 'assets\fileforge.ico');assets" `
    --paths $DepsDir `
    --distpath $DistDir `
    --workpath $BuildDir `
    --specpath $BuildDir `
    (Join-Path $ProjectDir 'fileforge_app.py')

Write-Host "Built: $(Join-Path $DistDir 'FileForge.exe')"
