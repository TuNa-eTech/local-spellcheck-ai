$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Lenh '$Command' that bai voi exit code $LASTEXITCODE."
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$engineDir = Join-Path $repoRoot "engine"
$engineDistDir = Join-Path $engineDir "dist\soatvan-engine"
$engineResourceDir = Join-Path $repoRoot "apps\desktop\src-tauri\resources\engine"
$desktopDir = Join-Path $repoRoot "apps\desktop"
$distDir = Join-Path $repoRoot "dist"
$portableDir = Join-Path $distDir "SoatVan-Portable"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "    BUILD SOÁTVĂN PORTABLE (WINDOWS X64)  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Kiem tra cong cu
foreach ($cmd in @("uv", "npm", "cargo")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "Khong tim thay cong cu '$cmd'. Vui long kiem tra PATH."
    }
}

# 2. Dong bo Python & Build PyInstaller sidecar
Write-Host "`n[1/4] Dong bo dependency va build Python engine..." -ForegroundColor Yellow
Invoke-Checked "uv" @("sync", "--project", "engine", "--extra", "dev", "--extra", "model", "--locked")

Push-Location $engineDir
try {
    Invoke-Checked "uv" @("run", "pyinstaller", "--noconfirm", "--clean", "soatvan-engine.spec")
}
finally {
    Pop-Location
}

$engineExecutable = Join-Path $engineDistDir "soatvan-engine.exe"
if (-not (Test-Path -LiteralPath $engineExecutable -PathType Leaf)) {
    throw "PyInstaller khong tao executable $engineExecutable."
}

# 3. Dong bo resource va build Tauri Desktop
Write-Host "`n[2/4] Dong bo sidecar vao Tauri va build release desktop..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $engineResourceDir | Out-Null
Get-ChildItem -Force -LiteralPath $engineResourceDir |
    Where-Object { $_.Name -ne "README.txt" } |
    Remove-Item -Recurse -Force
Copy-Item -Path (Join-Path $engineDistDir "*") -Destination $engineResourceDir -Recurse -Force

if (-not (Test-Path (Join-Path $desktopDir "node_modules"))) {
    Invoke-Checked "npm" @("--prefix", "apps/desktop", "ci")
}
Invoke-Checked "npx" @("--prefix", "apps/desktop", "tauri", "build")

# 4. Dong goi thu muc Portable
Write-Host "`n[3/4] Dong goi thu muc Portable..." -ForegroundColor Yellow
if (Test-Path -LiteralPath $portableDir) {
    Remove-Item -LiteralPath $portableDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $portableDir | Out-Null

$desktopExe = Join-Path $repoRoot "apps\desktop\src-tauri\target\release\soatvan-desktop.exe"
$portableExe = Join-Path $portableDir "SoatVan.exe"
Copy-Item -LiteralPath $desktopExe -Destination $portableExe -Force

$portableEngineDir = Join-Path $portableDir "engine"
New-Item -ItemType Directory -Force -Path $portableEngineDir | Out-Null
Copy-Item -Path (Join-Path $engineDistDir "*") -Destination $portableEngineDir -Recurse -Force

# 5. Nen Zip
Write-Host "`n[4/4] Nen file zip Portable..." -ForegroundColor Yellow
$desktopVersion = (Get-Content -LiteralPath (Join-Path $desktopDir "package.json") -Raw | ConvertFrom-Json).version
$portableZip = Join-Path $distDir "SoatVan-v$desktopVersion-Windows-x64-Portable.zip"
if (Test-Path -LiteralPath $portableZip) {
    Remove-Item -LiteralPath $portableZip -Force
}
Compress-Archive -Path (Join-Path $portableDir "*") -DestinationPath $portableZip -CompressionLevel Optimal

Write-Host "`n==========================================" -ForegroundColor Green
Write-Host " BUILD PORTABLE THANH CONG!              " -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host "Thu muc Portable : $portableDir\SoatVan.exe" -ForegroundColor Cyan
Write-Host "File Zip phan phoi: $portableZip" -ForegroundColor Cyan
