$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Build NSIS .exe phai chay tren Windows."
}

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

foreach ($commandName in @("uv", "npm", "cargo")) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Khong tim thay lenh '$commandName'."
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$engineDir = Join-Path $repoRoot "engine"
$engineDistDir = Join-Path $engineDir "dist\soatvan-engine"
$engineResourceDir = Join-Path $repoRoot "apps\desktop\src-tauri\resources\engine"
$installerDir = Join-Path $repoRoot "apps\desktop\src-tauri\target\release\bundle\nsis"

Push-Location $repoRoot
try {
    Write-Host "[1/4] Dong bo dependency Python"
    Invoke-Checked "uv" @("sync", "--project", "engine", "--extra", "dev", "--locked")

    Write-Host "[2/4] Build Python sidecar onedir"
    Push-Location $engineDir
    try {
        Invoke-Checked "uv" @("run", "pyinstaller", "--clean", "--noconfirm", "soatvan-engine.spec")
    }
    finally {
        Pop-Location
    }

    $engineExecutable = Join-Path $engineDistDir "soatvan-engine.exe"
    if (-not (Test-Path -LiteralPath $engineExecutable -PathType Leaf)) {
        throw "PyInstaller khong tao executable $engineExecutable."
    }

    Write-Host "[3/4] Dong bo sidecar va dependency frontend"
    New-Item -ItemType Directory -Force -Path $engineResourceDir | Out-Null
    Get-ChildItem -Force -LiteralPath $engineResourceDir |
        Where-Object { $_.Name -ne "README.txt" } |
        Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $engineDistDir "*") -Destination $engineResourceDir -Recurse -Force
    Invoke-Checked "npm" @("--prefix", "apps/desktop", "ci")

    Write-Host "[4/4] Build Tauri NSIS installer"
    Invoke-Checked "npm" @("--prefix", "apps/desktop", "run", "tauri", "--", "build", "--bundles", "nsis")

    $installers = @(Get-ChildItem -LiteralPath $installerDir -Filter "*.exe" -File -ErrorAction SilentlyContinue)
    if ($installers.Count -eq 0) {
        throw "Khong tim thay NSIS installer trong $installerDir."
    }

    Write-Host "Build thanh cong:"
    $installers.FullName | ForEach-Object { Write-Host $_ }
}
finally {
    Pop-Location
}
