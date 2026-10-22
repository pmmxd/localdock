param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$toolDir = Join-Path $PSScriptRoot ".build-tools"

if (-not $SkipInstall) {
    python -m pip install `
        --disable-pip-version-check `
        --target $toolDir `
        -r build-requirements.txt
}

$env:PYTHONPATH = "$toolDir;$env:PYTHONPATH"
python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name LocalDock `
    --add-data "web;web" `
    app.py

$portableDir = Join-Path $PSScriptRoot "dist\LocalDock"
Copy-Item -LiteralPath README.md -Destination $portableDir -Force
Copy-Item -LiteralPath README.zh-CN.md -Destination $portableDir -Force

$zipPath = Join-Path $PSScriptRoot "dist\LocalDock-portable.zip"
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $portableDir -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "Portable build: $zipPath"
