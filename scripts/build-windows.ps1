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
    Invoke-Checked "uv" @("sync", "--project", "engine", "--extra", "dev", "--locked")

    Write-Host "[2/5] Build Python sidecar onedir"
    Push-Location $engineDir
    try {
        Invoke-Checked "uv" @("run", "pyinstaller", "--noconfirm", "soatvan-engine.spec")
    }
    finally {
        Pop-Location
    }

    $engineExecutable = Join-Path $engineDistDir "soatvan-engine.exe"
    if (-not (Test-Path -LiteralPath $engineExecutable -PathType Leaf)) {
        throw "PyInstaller khong tao executable $engineExecutable."
    }

    Write-Host "[3/5] Tao hoac tai certificate ca nhan"
    $certificate = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
        Where-Object {
            $_.Subject -eq $personalCertificateSubject -and
            $_.HasPrivateKey -and
            $_.NotAfter -gt (Get-Date).AddDays(30)
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
    Invoke-Checked "npm" @("--prefix", "apps/desktop", "ci")

    Write-Host "[5/5] Build va ky Tauri NSIS installer"
    Invoke-Checked "npm" @(
        "--prefix", "apps/desktop", "run", "tauri", "--",
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

    Write-Host "Build thanh cong:"
    $installers.FullName | ForEach-Object { Write-Host $_ }
    Write-Host $publicCertificatePath
}
finally {
    Pop-Location
}
