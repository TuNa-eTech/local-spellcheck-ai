<#
.SYNOPSIS
    Rebuild llama-cpp-python with the CUDA backend and stage the CUDA runtime.

.DESCRIPTION
    PyPI only publishes an sdist for llama-cpp-python, so `uv sync` compiles it
    with CMake's defaults - CPU only. This script rebuilds the same locked
    version with `-DGGML_CUDA=on`, installs the wheel over the CPU one, and
    copies the CUDA redistributables into `llama_cpp/lib` so PyInstaller carries
    them into the portable package (no CUDA Toolkit on the user's machine).

    Whether a given RTX card is used is then decided at runtime by llama.cpp:
    the build embeds SASS for the architectures in -Architectures plus PTX for
    the newest one, and a machine with no NVIDIA driver registers no CUDA
    device and quietly stays on CPU.

.PARAMETER Mode
    auto (default) builds with CUDA when a toolkit is present and silently
    stays on CPU otherwise; on requires CUDA and fails without it; off skips.

.PARAMETER Architectures
    CMake CUDA architecture list. Default covers RTX 20/30/40/50 series.
    Entries the installed nvcc does not know are dropped automatically.
#>
param(
    [ValidateSet("auto", "on", "off")]
    [string]$Mode = $(if ($env:SOATVAN_CUDA) { $env:SOATVAN_CUDA } else { "auto" }),
    [string]$Architectures = $(if ($env:SOATVAN_CUDA_ARCHS) { $env:SOATVAN_CUDA_ARCHS } else { "75-real;86-real;89-real;120-real;120-virtual" })
)

$ErrorActionPreference = "Stop"

if ($env:SOATVAN_REQUIRE_CUDA -eq "1" -and $Mode -eq "auto") {
    $Mode = "on"
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[llama-cuda] $Message"
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

function Get-LockedPackage {
    <#
        Read version + sdist url/hash for one package straight out of uv.lock so
        the CUDA wheel is always the exact build `uv sync --locked` resolved.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$LockPath,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $blocks = (Get-Content -LiteralPath $LockPath -Raw) -split '(?m)^\[\[package\]\]\s*$'
    $namePattern = '(?m)^name\s*=\s*"' + [regex]::Escape($Name) + '"\s*$'
    foreach ($block in $blocks) {
        if ($block -notmatch $namePattern) { continue }
        $version = [regex]::Match($block, '(?m)^version\s*=\s*"([^"]+)"\s*$')
        $sdist = [regex]::Match($block, '(?m)^sdist\s*=\s*\{\s*url\s*=\s*"([^"]+)",\s*hash\s*=\s*"sha256:([0-9a-fA-F]+)"')
        if (-not $version.Success -or -not $sdist.Success) {
            throw "Khong doc duoc version/sdist cua '$Name' trong $LockPath."
        }
        return [pscustomobject]@{
            Version = $version.Groups[1].Value
            Url     = $sdist.Groups[1].Value
            Sha256  = $sdist.Groups[2].Value.ToLowerInvariant()
        }
    }
    throw "Khong tim thay package '$Name' trong $LockPath."
}

function Import-VisualStudioEnvironment {
    <#
        The Ninja generator drives cl.exe directly, so it needs the environment
        vcvars64.bat sets. nvcc then finds the same host compiler.
    #>
    if (Get-Command "cl.exe" -ErrorAction SilentlyContinue) {
        Write-Step "MSVC da co san trong PATH"
        return
    }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
        throw "Khong tim thay vswhere.exe - can Visual Studio Build Tools (C++ workload) de bien dich CUDA."
    }
    $installPath = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
    if (-not $installPath) {
        throw "Visual Studio khong co C++ build tools (Microsoft.VisualStudio.Component.VC.Tools.x86.x64)."
    }
    $vcvars = Join-Path $installPath "VC\Auxiliary\Build\vcvars64.bat"
    if (-not (Test-Path -LiteralPath $vcvars -PathType Leaf)) {
        throw "Khong tim thay $vcvars."
    }
    Write-Step "Nap moi truong MSVC tu $vcvars"
    $output = & cmd.exe /c "`"$vcvars`" >nul 2>&1 && set"
    if ($LASTEXITCODE -ne 0) {
        throw "vcvars64.bat that bai voi exit code $LASTEXITCODE."
    }
    foreach ($line in $output) {
        if ($line -match '^([^=]+)=(.*)$') {
            Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] -ErrorAction SilentlyContinue
        }
    }
}

function Select-SupportedArchitectures {
    <#
        Blackwell (120) needs CUDA 12.8+; passing it to an older nvcc is a hard
        error. Keep only what this toolkit can actually emit.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$NvccPath,
        [Parameter(Mandatory = $true)][string]$Requested
    )

    $known = @()
    foreach ($line in (& $NvccPath --list-gpu-arch)) {
        if ($line -match 'compute_(\d+)') { $known += $Matches[1] }
    }
    if ($known.Count -eq 0) {
        Write-Warning "nvcc --list-gpu-arch khong tra ve gi - dung nguyen danh sach '$Requested'."
        return $Requested
    }
    $kept = @()
    $dropped = @()
    foreach ($entry in ($Requested -split ';' | Where-Object { $_ })) {
        $number = [regex]::Match($entry, '^\d+')
        if ($number.Success -and $known -notcontains $number.Value) { $dropped += $entry }
        else { $kept += $entry }
    }
    if ($dropped.Count -gt 0) {
        Write-Warning "CUDA Toolkit nay khong ho tro kien truc: $($dropped -join ', ') - da bo qua."
    }
    if ($kept.Count -eq 0) {
        throw "Khong con kien truc CUDA nao hop le sau khi loc theo nvcc."
    }
    return ($kept -join ';')
}

if ($env:OS -ne "Windows_NT") {
    Write-Step "Khong phai Windows - bo qua (macOS dung Metal, da bat san)."
    return
}
if ($Mode -eq "off") {
    Write-Step "SOATVAN_CUDA=off - giu ban llama.cpp CPU."
    return
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$engineDir = Join-Path $repoRoot "engine"
$lockPath = Join-Path $engineDir "uv.lock"
$venvPython = Join-Path $engineDir ".venv\Scripts\python.exe"
$wheelCacheRoot = Join-Path $repoRoot "dist\wheels"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Chua co venv engine ($venvPython). Chay 'uv sync --project engine' truoc."
}

# --- CUDA Toolkit ---------------------------------------------------------
$cudaRoot = $env:CUDA_PATH
$nvcc = $null
if ($cudaRoot -and (Test-Path -LiteralPath (Join-Path $cudaRoot "bin\nvcc.exe") -PathType Leaf)) {
    $nvcc = Join-Path $cudaRoot "bin\nvcc.exe"
}
else {
    $command = Get-Command "nvcc.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        $nvcc = $command.Source
        $cudaRoot = Split-Path -Parent (Split-Path -Parent $nvcc)
    }
}
if (-not $nvcc) {
    $message = "Khong tim thay CUDA Toolkit (nvcc). Ban build se chi chay CPU."
    if ($Mode -eq "on") {
        throw "$message Dat SOATVAN_CUDA=off neu that su muon ban CPU."
    }
    Write-Warning $message
    return
}

$nvccVersion = "unknown"
$nvccBanner = (& $nvcc --version) -join "`n"
$versionMatch = [regex]::Match($nvccBanner, 'release (\d+\.\d+)')
if ($versionMatch.Success) { $nvccVersion = $versionMatch.Groups[1].Value }
Write-Step "CUDA Toolkit $nvccVersion tai $cudaRoot"

$architectures = Select-SupportedArchitectures -NvccPath $nvcc -Requested $Architectures
Write-Step "Kien truc CUDA: $architectures"

# --- Wheel (cache theo version + toolkit + kien truc) ---------------------
# Wheel cua llama-cpp-python gan tag theo phien ban Python, nen cache phai tach
# theo tag va wheel phai duoc build bang chinh interpreter cua venv engine.
$pythonTag = & $venvPython -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or -not $pythonTag) {
    throw "Khong doc duoc phien ban Python cua venv engine."
}
$package = Get-LockedPackage -LockPath $lockPath -Name "llama-cpp-python"
$architectureTag = ($architectures -replace '[^0-9a-zA-Z]', '')
$cacheKey = "llama-cpp-python-$($package.Version)-$pythonTag-cu$nvccVersion-sm$architectureTag"
$wheelDir = Join-Path $wheelCacheRoot $cacheKey
$wheel = Get-ChildItem -LiteralPath $wheelDir -Filter "*$pythonTag*.whl" -File -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($wheel) {
    Write-Step "Dung lai wheel da cache: $($wheel.Name)"
}
else {
    Write-Step "Bien dich llama-cpp-python $($package.Version) voi CUDA (mat 15-40 phut lan dau)"
    Import-VisualStudioEnvironment
    New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null
    $work = Join-Path ([System.IO.Path]::GetTempPath()) "soatvan-llama-cuda-$PID"
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    try {
        $archive = Join-Path $work "llama-cpp-python.tar.gz"
        Write-Step "Tai sdist $($package.Url)"
        $progress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest -Uri $package.Url -OutFile $archive -UseBasicParsing
        }
        finally {
            $ProgressPreference = $progress
        }
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $package.Sha256) {
            throw "sdist sai hash: cho doi $($package.Sha256), nhan duoc $actual."
        }

        Invoke-Checked "tar" @("-xzf", $archive, "-C", $work)
        $source = Get-ChildItem -LiteralPath $work -Directory |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "pyproject.toml") } |
            Select-Object -First 1
        if (-not $source) {
            throw "Khong tim thay thu muc source trong sdist da giai nen."
        }

        # GGML_NATIVE=off: mot ban phat hanh khong duoc bake -march=native cua
        # may build vao kernel CPU. LLAVA_BUILD=off bo phan multimodal khong dung.
        $cmakeArgs = if ($env:SOATVAN_LLAMA_CMAKE_ARGS) {
            $env:SOATVAN_LLAMA_CMAKE_ARGS
        }
        else {
            "-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=$architectures -DGGML_NATIVE=off -DGGML_CCACHE=off -DLLAVA_BUILD=off"
        }
        Write-Step "CMAKE_ARGS = $cmakeArgs"
        $previousCmakeArgs = $env:CMAKE_ARGS
        $previousGenerator = $env:CMAKE_GENERATOR
        $env:CMAKE_ARGS = $cmakeArgs
        $env:CMAKE_GENERATOR = "Ninja"
        $env:CMAKE_BUILD_PARALLEL_LEVEL = $env:NUMBER_OF_PROCESSORS
        $env:CUDAToolkit_ROOT = $cudaRoot
        $started = Get-Date
        try {
            Invoke-Checked "uv" @(
                "build", "--wheel", "--python", $venvPython,
                $source.FullName, "--out-dir", $wheelDir
            )
        }
        finally {
            $env:CMAKE_ARGS = $previousCmakeArgs
            $env:CMAKE_GENERATOR = $previousGenerator
        }
        Write-Step "Bien dich xong sau $([int]((Get-Date) - $started).TotalMinutes) phut"
        $wheel = Get-ChildItem -LiteralPath $wheelDir -Filter "*$pythonTag*.whl" -File |
            Select-Object -First 1
        if (-not $wheel) {
            throw "uv build khong tao ra wheel $pythonTag nao trong $wheelDir."
        }
    }
    finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# --- Cai de len ban CPU ---------------------------------------------------
Write-Step "Cai $($wheel.Name) vao venv engine"
Invoke-Checked "uv" @(
    "pip", "install", "--python", $venvPython,
    "--reinstall-package", "llama-cpp-python", "--no-deps", $wheel.FullName
)

# --- CUDA redistributables ------------------------------------------------
# find_spec thay vi import: truoc khi copy CUDA runtime vao day, import that su
# co the fail vi ggml-cuda.dll chua tim thay cudart/cublas.
$libDir = & $venvPython -c "import importlib.util, os; print(os.path.join(os.path.dirname(importlib.util.find_spec('llama_cpp').origin), 'lib'))"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $libDir -PathType Container)) {
    throw "Khong xac dinh duoc thu muc lib cua llama_cpp."
}
if (-not (Test-Path -LiteralPath (Join-Path $libDir "ggml-cuda.dll") -PathType Leaf)) {
    throw "Wheel vua cai khong co ggml-cuda.dll - ban build CUDA that bai."
}
foreach ($pattern in @("cudart64_*.dll", "cublas64_*.dll", "cublasLt64_*.dll")) {
    $redistributable = Get-ChildItem -LiteralPath (Join-Path $cudaRoot "bin") -Filter $pattern -File `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $redistributable) {
        throw "Khong tim thay $pattern trong $cudaRoot\bin (can sub-package cudart/cublas)."
    }
    Copy-Item -LiteralPath $redistributable.FullName -Destination $libDir -Force
    Write-Step "Da dong goi $($redistributable.Name) ($([int]($redistributable.Length / 1MB)) MB)"
}

# --- Kiem chung -----------------------------------------------------------
# Chay that trong venv: neu backend CUDA khong duoc bien dich vao, dung build
# ngay thay vi phat hanh mot ban "CPU nhung tuong la GPU".
$systemInfo = & $venvPython -c "import llama_cpp; print(llama_cpp.llama_print_system_info().decode('utf-8', 'replace'))"
if ($LASTEXITCODE -ne 0) {
    throw "Khong import duoc llama_cpp sau khi cai wheel CUDA."
}
Write-Step "llama.cpp system info: $systemInfo"
if ($systemInfo -notmatch 'CUDA\s*:') {
    throw "llama.cpp da cai KHONG co backend CUDA: $systemInfo"
}
Write-Step "Da bat backend CUDA cho llama.cpp."
