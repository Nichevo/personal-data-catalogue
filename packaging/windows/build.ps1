# Builds the optional no-install Windows version: a zip holding a complete Python
# environment (made with conda-pack) and a "Start catalogue" launcher.
# Colleagues unzip it anywhere and double-click the launcher; nothing is installed.
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Version 2.0.0
#
# Needs conda (Miniconda or Miniforge). Run from the repository root.
param([string]$Version = "dev")
$ErrorActionPreference = "Stop"

$root = (Get-Location).Path
$build = Join-Path $root "build\windows"
$envDir = Join-Path $build "env"
$name = "pdcat-windows-$Version"
$out = Join-Path $root "dist\$name"

if (Test-Path $build) { Remove-Item -Recurse -Force $build }
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force -Path $build, $out | Out-Null

Write-Host "Creating the environment..."
conda env create --prefix $envDir --file environment.yml --yes
if ($LASTEXITCODE -ne 0) { throw "conda env create failed" }
conda run --prefix $envDir python -m pip install --no-deps .
if ($LASTEXITCODE -ne 0) { throw "installing pdcat failed" }
conda run --prefix $envDir python -c "import pdcat.cli, streamlit, pandas, pyodbc; print('environment ok')"
if ($LASTEXITCODE -ne 0) { throw "the environment doesn't import" }

Write-Host "Packing..."
conda install --name base --channel conda-forge conda-pack --yes
conda-pack --prefix $envDir --output (Join-Path $build "env.zip") --format zip --ignore-editable-packages
if ($LASTEXITCODE -ne 0) { throw "conda-pack failed" }
Expand-Archive -Path (Join-Path $build "env.zip") -DestinationPath (Join-Path $out "env")

Copy-Item (Join-Path $PSScriptRoot "Start catalogue.bat") $out
Copy-Item (Join-Path $PSScriptRoot "README.txt") $out
Set-Content -Path (Join-Path $out "VERSION.txt") -Value $Version

$zip = Join-Path $root "dist\$name.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Built $zip"
