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
$tauriTargetDir = Join-Path $repoRoot "apps\desktop\src-tauri\target"
$personalCertificateSubject = if ([string]::IsNullOrWhiteSpace($env:SOATVAN_WINDOWS_CERT_SUBJECT)) {
    "CN=SoatVan Personal Use"
}
else {
    $env:SOATVAN_WINDOWS_CERT_SUBJECT
}

Push-Location $repoRoot
try {
    Write-Host "[1/5] Dong bo dependency Python"
    Invoke-Checked "uv" @("sync", "--project", "engine", "--extra", "dev", "--extra", "model", "--locked")

    if ($env:SOATVAN_PYINSTALLER_CACHE_HIT -eq "true") {
        $engineBuildDir = Join-Path $engineDir "build\soatvan-engine"
        if (Test-Path -LiteralPath $engineBuildDir -PathType Container) {
            Get-ChildItem -LiteralPath $engineBuildDir -Recurse -Force | ForEach-Object {
                $_.LastWriteTimeUtc = [DateTime]::UtcNow
            }
        }
    }

    Write-Host "[2/5] Build Python sidecar onedir"
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

    Write-Host "[3/5] Tao hoac tai certificate ca nhan"
    $codeSigningOid = "1.3.6.1.5.5.7.3.3"
    $certificate = Get-ChildItem -Path Cert:\CurrentUser\My |
        Where-Object {
            $_.Subject -eq $personalCertificateSubject -and
            $_.HasPrivateKey -and
            $_.NotAfter -gt (Get-Date).AddDays(30) -and
            @($_.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId.Value }) -contains $codeSigningOid
        } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1

    if (-not $certificate) {
        $certificate = New-SelfSignedCertificate `
            -Type CodeSigningCert `
            -Subject $personalCertificateSubject `
            -FriendlyName "SoatVan Personal Code Signing" `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -KeyAlgorithm RSA `
            -KeyLength 3072 `
            -HashAlgorithm SHA256 `
            -KeyExportPolicy NonExportable `
            -NotAfter (Get-Date).AddYears(5)
    }

    $engineFilesToSign = Get-ChildItem -LiteralPath $engineDistDir -Recurse -File |
        Where-Object { $_.Extension -in @(".exe", ".dll") }
    foreach ($engineFile in $engineFilesToSign) {
        $signature = Set-AuthenticodeSignature `
            -LiteralPath $engineFile.FullName `
            -Certificate $certificate `
            -HashAlgorithm SHA256
        if (-not $signature.SignerCertificate -or $signature.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
            throw "Khong the ky $($engineFile.FullName)."
        }
    }

    New-Item -ItemType Directory -Force -Path $tauriTargetDir | Out-Null
    $personalSigningConfigPath = Join-Path $tauriTargetDir "personal-signing.json"
    $personalSigningConfig = @{
        bundle = @{
            windows = @{
                certificateThumbprint = $certificate.Thumbprint
                digestAlgorithm = "sha256"
            }
        }
    } | ConvertTo-Json -Depth 4
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($personalSigningConfigPath, $personalSigningConfig, $utf8NoBom)

    Write-Host "[4/5] Dong bo sidecar va dependency frontend"
    New-Item -ItemType Directory -Force -Path $engineResourceDir | Out-Null
    Get-ChildItem -Force -LiteralPath $engineResourceDir |
        Where-Object { $_.Name -ne "README.txt" } |
        Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $engineDistDir "*") -Destination $engineResourceDir -Recurse -Force
    if (-not (Test-Path (Join-Path $repoRoot "apps\desktop\node_modules"))) {
        Invoke-Checked "npm" @("--prefix", "apps/desktop", "ci")
    }

    Write-Host "[5/5] Build va ky Tauri NSIS installer"
    Invoke-Checked "npx" @(
        "--prefix", "apps/desktop", "tauri",
        "build", "--bundles", "nsis", "--config", $personalSigningConfigPath
    )

    $installers = @(Get-ChildItem -LiteralPath $installerDir -Filter "*.exe" -File -ErrorAction SilentlyContinue)
    if ($installers.Count -eq 0) {
        throw "Khong tim thay NSIS installer trong $installerDir."
    }

    foreach ($installer in $installers) {
        $signature = Get-AuthenticodeSignature -LiteralPath $installer.FullName
        if (-not $signature.SignerCertificate -or $signature.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
            throw "NSIS installer $($installer.FullName) khong duoc ky bang certificate mong doi."
        }
    }

    $publicCertificatePath = Join-Path $installerDir "SoatVan-Personal-CodeSigning.cer"
    Export-Certificate -Cert $certificate -FilePath $publicCertificatePath -Type CERT | Out-Null

    Write-Host "[6/6] Dong goi ban Portable va cap nhat dist"
    $portableDir = Join-Path $repoRoot "dist\SoatVan-Portable"
    if (Test-Path -LiteralPath $portableDir) { Remove-Item -LiteralPath $portableDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $portableDir | Out-Null

    $desktopExe = Join-Path $repoRoot "apps\desktop\src-tauri\target\release\soatvan-desktop.exe"
    $portableExe = Join-Path $portableDir "SoatVan.exe"
    Copy-Item -LiteralPath $desktopExe -Destination $portableExe -Force
    Set-AuthenticodeSignature -LiteralPath $portableExe -Certificate $certificate -HashAlgorithm SHA256 | Out-Null

    $portableEngineDir = Join-Path $portableDir "engine"
    New-Item -ItemType Directory -Force -Path $portableEngineDir | Out-Null
    Copy-Item -Path (Join-Path $engineDistDir "*") -Destination $portableEngineDir -Recurse -Force

    $portableZip = Join-Path $repoRoot "dist\SoatVan-v0.1.1-Windows-x64-Portable.zip"
    if (Test-Path -LiteralPath $portableZip) { Remove-Item -LiteralPath $portableZip -Force }
    Compress-Archive -Path (Join-Path $portableDir "*") -DestinationPath $portableZip -CompressionLevel Optimal

    Write-Host "Build thanh cong:"
    $installers.FullName | ForEach-Object { Write-Host $_ }
    Write-Host $publicCertificatePath
    Write-Host $portableZip
}
finally {
    Pop-Location
}
