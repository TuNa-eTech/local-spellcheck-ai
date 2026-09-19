# Kiem tra va thiet lap moi truong build CUDA cho SoatVan tren may local.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  KIEM TRA MOI TRUONG BUILD CUDA (LOCAL)  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Kiem tra CMake & Ninja
Write-Host "`n[1/3] Kiem tra CMake va Ninja..." -ForegroundColor Yellow
$cmake = Get-Command "cmake" -ErrorAction SilentlyContinue
$ninja = Get-Command "ninja" -ErrorAction SilentlyContinue

if (-not $cmake) {
    Write-Host "Chua tim thay cmake, dang cai dat qua uv..." -ForegroundColor Yellow
    & uv tool install cmake
} else {
    Write-Host "  + CMake: OK ($(& cmake --version | Select-Object -First 1))" -ForegroundColor Green
}

if (-not $ninja) {
    Write-Host "Chua tim thay ninja, dang cai dat qua uv..." -ForegroundColor Yellow
    & uv tool install ninja
} else {
    Write-Host "  + Ninja: OK ($(& ninja --version | Select-Object -First 1))" -ForegroundColor Green
}

# 2. Kiem tra Visual Studio C++ Build Tools
Write-Host "`n[2/3] Kiem tra Visual Studio C++ Build Tools..." -ForegroundColor Yellow
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($vsPath) {
        Write-Host "  + MSVC C++ Build Tools: OK ($vsPath)" -ForegroundColor Green
    } else {
        Write-Warning "Khong tim thay MSVC C++ Build Tools. Can cai component Microsoft.VisualStudio.Component.VC.Tools.x86.x64."
    }
} else {
    Write-Warning "Khong tim thay vswhere.exe tai $vswhere"
}

# 3. Kiem tra CUDA Toolkit (nvcc)
Write-Host "`n[3/3] Kiem tra NVIDIA CUDA Toolkit..." -ForegroundColor Yellow
$nvcc = Get-Command "nvcc" -ErrorAction SilentlyContinue
$cudaPath = $env:CUDA_PATH

if (-not $nvcc -and -not $cudaPath) {
    $cudaPath = [Environment]::GetEnvironmentVariable("CUDA_PATH", "Machine")
    if (-not $cudaPath) {
        $cudaPath = [Environment]::GetEnvironmentVariable("CUDA_PATH", "User")
    }
}

if (-not $nvcc -and -not $cudaPath) {
    foreach ($cand in @("D:\CUDA_TOOLKIT", "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*")) {
        $found = Get-Item -Path $cand -ErrorAction SilentlyContinue | Select-Object -Last 1
        if ($found -and (Test-Path (Join-Path $found.FullName "bin\nvcc.exe"))) {
            $cudaPath = $found.FullName
            break
        }
    }
}

if ($cudaPath) {
    $env:CUDA_PATH = $cudaPath
    $env:Path = "$cudaPath\bin;$cudaPath\bin\x64;$env:Path"
    Write-Host "  + Tim thay CUDA Toolkit tai: $cudaPath (da them vao PATH phien lam viec nay)" -ForegroundColor Green
}

if ($nvcc -or $cudaPath) {
    $ver = & nvcc --version | Select-String "release"
    Write-Host "  + NVIDIA CUDA Toolkit: OK ($ver)" -ForegroundColor Green
    Write-Host "`n==========================================" -ForegroundColor Green
    Write-Host "  MOI TRUONG DA SAN SANG DE BUILD CUDA!   " -ForegroundColor Green
    Write-Host "==========================================" -ForegroundColor Green
    Write-Host "De bat dau build ban Portable co CUDA, chay lenh:" -ForegroundColor Cyan
    Write-Host "    npm run build:portable:cuda" -ForegroundColor White
} else {
    Write-Host "  - Chua tim thay CUDA Toolkit (nvcc.exe)." -ForegroundColor Red
    Write-Host "`nHuong dan cai dat CUDA Toolkit de build tren may nay:" -ForegroundColor Yellow
    Write-Host "  Cach 1 (Qua winget - chay terminal Administrator):"
    Write-Host "      winget install Nvidia.CUDA --accept-source-agreements --accept-package-agreements" -ForegroundColor White
    Write-Host "  Cach 2 (Tai installer chinh thuc tu NVIDIA):"
    Write-Host "      https://developer.nvidia.com/cuda-downloads" -ForegroundColor White
    Write-Host "      (Chon Windows x86_64 -> exe local/network, cai dat cac component: nvcc, runtime, libraries)"
    Write-Host "`nSau khi cai dat xong, chay lai script nay hoac chay truc tiep: npm run build:portable:cuda" -ForegroundColor Cyan
}
