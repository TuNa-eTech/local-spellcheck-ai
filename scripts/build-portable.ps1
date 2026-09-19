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
Write-Host "    BUILD SOATVAN PORTABLE (WINDOWS X64)  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Kiem tra cong cu
foreach ($cmd in @("uv", "npm", "cargo")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "Khong tim thay cong cu '$cmd'. Vui long kiem tra PATH."
    }
}

# `npm --prefix apps/desktop` / `npx --prefix apps/desktop` dung duong dan
# tuong doi, nen moi thao tac phai chay tu goc repo bat ke CWD goi script.
Push-Location $repoRoot
try {
    # `option_env!("SOATVAN_MODEL_PUBLIC_KEY")` duoc doc luc bien dich Rust. Neu
    # thieu, ban build van import duoc file .gguf tho (trust local_unverified),
    # nhung KHONG xac minh duoc goi .svmodel co chu ky phat hanh.
    if ([string]::IsNullOrWhiteSpace($env:SOATVAN_MODEL_PUBLIC_KEY)) {
        Write-Warning "SOATVAN_MODEL_PUBLIC_KEY chua duoc set — ban build se KHONG import duoc goi .svmodel ky phat hanh. Import file .gguf tho van hoat dong binh thuong."
    }

    # 2. Dong bo Python & Build PyInstaller sidecar
    Write-Host "`n[1/5] Dong bo dependency va build Python engine..." -ForegroundColor Yellow
    Invoke-Checked "uv" @("sync", "--project", "engine", "--extra", "dev", "--extra", "model", "--extra", "seq2seq", "--locked")

    # `uv sync` chi bien dich llama.cpp cho CPU. Buoc nay cai de len ban CUDA va
    # goi kem CUDA runtime, nho do ban Portable chay duoc GPU tren may co RTX ma
    # khong can cai CUDA Toolkit. Khong co Toolkit luc build thi giu ban CPU.
    & (Join-Path $PSScriptRoot "build-llama-cuda.ps1")

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

    # 3. Smoke-test sidecar da dong goi — chay engine exe that voi PATH toi thieu
    #    va khong co PYTHONPATH/PYTHONHOME, dam bao PyInstaller da gom du DLL,
    #    hidden-import va VC++ runtime truoc khi ton thoi gian build Rust.
    Write-Host "`n[2/5] Kiem tra sidecar dong goi khoi dong va chay job that..." -ForegroundColor Yellow
    $acceptanceDir = Join-Path $distDir "portable-acceptance"
    $acceptanceArgs = @(
        "run", "--project", "engine", "python",
        (Join-Path $engineDir "tests\windows_frozen_acceptance.py"),
        "--engine", $engineExecutable,
        "--artifacts", $acceptanceDir
    )
    # Ban phat hanh phai bao cao backend CUDA; ban build may khong co Toolkit thi
    # chi in ra bao cao de doi chieu.
    if ($env:SOATVAN_REQUIRE_CUDA -eq "1") {
        $acceptanceArgs += "--expect-cuda"
    }
    # Neu co model seq2seq that, kiem tra luon ca duong torch/transformers/
    # sentencepiece trong ban dong goi (bang khong thi buoc nay bo qua seq2seq).
    if (-not [string]::IsNullOrWhiteSpace($env:SOATVAN_SEQ2SEQ_MODEL_DIR)) {
        if (Test-Path -LiteralPath (Join-Path $env:SOATVAN_SEQ2SEQ_MODEL_DIR "config.json") -PathType Leaf) {
            $acceptanceArgs += @("--seq2seq-model-dir", $env:SOATVAN_SEQ2SEQ_MODEL_DIR)
        }
        else {
            Write-Warning "SOATVAN_SEQ2SEQ_MODEL_DIR khong co config.json — bo qua kiem tra seq2seq frozen."
        }
    }
    else {
        Write-Warning "SOATVAN_SEQ2SEQ_MODEL_DIR chua set — KHONG kiem tra duoc duong seq2seq trong ban dong goi. Dat bien nay tro toi thu muc model de bat kiem tra."
    }
    Invoke-Checked "uv" $acceptanceArgs

    # 4. Dong bo resource va build Tauri Desktop
    Write-Host "`n[3/5] Dong bo sidecar vao Tauri va build release desktop..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $engineResourceDir | Out-Null
    Get-ChildItem -Force -LiteralPath $engineResourceDir |
        Where-Object { $_.Name -ne "README.txt" } |
        Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $engineDistDir "*") -Destination $engineResourceDir -Recurse -Force

    if (-not (Test-Path (Join-Path $desktopDir "node_modules"))) {
        Invoke-Checked "npm" @("--prefix", "apps/desktop", "ci")
    }
    Invoke-Checked "npx" @("--prefix", "apps/desktop", "tauri", "build")

    # 5. Dong goi thu muc Portable
    Write-Host "`n[4/5] Dong goi thu muc Portable..." -ForegroundColor Yellow
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

    # Giai nen ban moi DE LEN thu muc cu la merge chu khong phai replace: file mo
    # coi cua ban cu o lai, va neu ban cu dang chay thi Windows khoa SoatVan.exe
    # nen exe bi bo qua. Dau moc nay cho app tu phat hien va ghi canh bao vao log.
    $stampVersion = (Get-Content -LiteralPath (Join-Path $desktopDir "package.json") -Raw | ConvertFrom-Json).version
    Set-Content -LiteralPath (Join-Path $portableDir "VERSION.txt") -Value $stampVersion -NoNewline -Encoding UTF8

    # 6. Nen Zip
    Write-Host "`n[5/5] Nen file zip Portable..." -ForegroundColor Yellow
    $desktopVersion = (Get-Content -LiteralPath (Join-Path $desktopDir "package.json") -Raw | ConvertFrom-Json).version
    # CUDA runtime lam thu muc Portable nang them ~650 MB; Compress-Archive
    # khong tao duoc zip lon hon 2 GB nen canh bao truoc khi nen.
    $portableBytes = (Get-ChildItem -LiteralPath $portableDir -Recurse -File |
        Measure-Object -Property Length -Sum).Sum
    Write-Host ("Ban Portable truoc khi nen: {0:N0} MB" -f [math]::Round($portableBytes / 1MB))
    if ($portableBytes -gt 3GB) {
        Write-Warning "Thu muc Portable rat lon — neu Compress-Archive that bai (gioi han 2 GB), dung SOATVAN_CUDA=off hoac nen bang 7-Zip."
    }
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
}
finally {
    Pop-Location
}
