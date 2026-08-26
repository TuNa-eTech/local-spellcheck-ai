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

function Get-SignToolPath {
    $command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Source
    if ($command) {
        return $command
    }

    $command = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" `
        -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $command) {
        throw "Khong tim thay signtool.exe trong Windows SDK."
    }
    return $command
}

function Add-CertificateToStore {
    param(
        [Parameter(Mandatory = $true)]
        [System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
        [Parameter(Mandatory = $true)][string]$StoreName
    )

    $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
        $StoreName,
        [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
    )
    try {
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        $matches = $store.Certificates.Find(
            [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
            $Certificate.Thumbprint,
            $false
        )
        if ($matches.Count -gt 0) {
            return $false
        }
        $store.Add($Certificate)
        return $true
    }
    finally {
        $store.Close()
    }
}

function Remove-CertificateFromStore {
    param(
        [Parameter(Mandatory = $true)][string]$Thumbprint,
        [Parameter(Mandatory = $true)][string]$StoreName
    )

    $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
        $StoreName,
        [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
    )
    try {
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        $matches = $store.Certificates.Find(
            [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
            $Thumbprint,
            $false
        )
        foreach ($match in $matches) {
            $store.Remove($match)
        }
    }
    finally {
        $store.Close()
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
$certificate = $null
$certificateThumbprint = $null
$temporarySigningPfx = $null
$addedCertificateStores = @()

Push-Location $repoRoot
try {
    Write-Host "[1/6] Dong bo dependency Python"
    Invoke-Checked "uv" @("sync", "--project", "engine", "--extra", "dev", "--extra", "model", "--locked")

    if ($env:SOATVAN_PYINSTALLER_CACHE_HIT -eq "true") {
        $engineBuildDir = Join-Path $engineDir "build\soatvan-engine"
        if (Test-Path -LiteralPath $engineBuildDir -PathType Container) {
            Get-ChildItem -LiteralPath $engineBuildDir -Recurse -Force | ForEach-Object {
                $_.LastWriteTimeUtc = [DateTime]::UtcNow
            }
        }
    }

    Write-Host "[2/6] Build Python sidecar onedir"
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

    Write-Host "[3/6] Tai certificate ky code tu secret"
    if ([string]::IsNullOrWhiteSpace($env:SOATVAN_WINDOWS_CERT_PASSWORD)) {
        throw "Thieu SOATVAN_WINDOWS_CERT_PASSWORD."
    }
    $signingPfxPath = $env:SOATVAN_WINDOWS_CERT_PFX
    if ([string]::IsNullOrWhiteSpace($signingPfxPath)) {
        if ([string]::IsNullOrWhiteSpace($env:SOATVAN_WINDOWS_CERT_PFX_BASE64)) {
            throw "Thieu SOATVAN_WINDOWS_CERT_PFX hoac SOATVAN_WINDOWS_CERT_PFX_BASE64."
        }
        $temporarySigningPfx = Join-Path ([System.IO.Path]::GetTempPath()) `
            "soatvan-signing-$PID-$([Guid]::NewGuid().ToString('N')).pfx"
        try {
            $pfxBytes = [Convert]::FromBase64String($env:SOATVAN_WINDOWS_CERT_PFX_BASE64)
            [System.IO.File]::WriteAllBytes($temporarySigningPfx, $pfxBytes)
        }
        catch {
            throw "SOATVAN_WINDOWS_CERT_PFX_BASE64 khong hop le."
        }
        $signingPfxPath = $temporarySigningPfx
    }
    if (-not (Test-Path -LiteralPath $signingPfxPath -PathType Leaf)) {
        throw "Khong tim thay PFX ky code."
    }

    $keyFlags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet -bor `
        [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::PersistKeySet
    try {
        $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
            $signingPfxPath,
            $env:SOATVAN_WINDOWS_CERT_PASSWORD,
            $keyFlags
        )
    }
    catch {
        throw "Khong the mo PFX ky code bang password da cau hinh."
    }
    $codeSigningOid = "1.3.6.1.5.5.7.3.3"
    $enhancedKeyUsage = $certificate.Extensions |
        Where-Object { $_.Oid.Value -eq "2.5.29.37" } |
        Select-Object -First 1
    $ekuOids = @($enhancedKeyUsage.EnhancedKeyUsages | ForEach-Object { $_.Value })
    if (-not $certificate.HasPrivateKey -or
        $certificate.NotBefore -gt (Get-Date) -or
        $certificate.NotAfter -le (Get-Date).AddDays(30) -or
        $ekuOids -notcontains $codeSigningOid) {
        throw "PFX khong phai certificate code-signing con hieu luc va co private key."
    }
    $certificateThumbprint = $certificate.Thumbprint
    foreach ($storeName in @("My", "Root", "TrustedPublisher")) {
        if (Add-CertificateToStore -Certificate $certificate -StoreName $storeName) {
            $addedCertificateStores += $storeName
        }
    }
    $signToolPath = Get-SignToolPath

    $engineFilesToSign = Get-ChildItem -LiteralPath $engineDistDir -Recurse -File |
        Where-Object { $_.Extension -in @(".exe", ".dll") }
    foreach ($engineFile in $engineFilesToSign) {
        Invoke-Checked $signToolPath @(
            "sign", "/fd", "SHA256", "/sha1", $certificateThumbprint,
            "/s", "My", $engineFile.FullName
        )
        Invoke-Checked $signToolPath @("verify", "/pa", "/all", $engineFile.FullName)
    }

    New-Item -ItemType Directory -Force -Path $tauriTargetDir | Out-Null
    $personalSigningConfigPath = Join-Path $tauriTargetDir "personal-signing.json"
    $personalSigningConfig = @{
        bundle = @{
            windows = @{
                certificateThumbprint = $certificateThumbprint
                digestAlgorithm = "sha256"
            }
        }
    } | ConvertTo-Json -Depth 4
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($personalSigningConfigPath, $personalSigningConfig, $utf8NoBom)

    Write-Host "[4/6] Dong bo sidecar va dependency frontend"
    New-Item -ItemType Directory -Force -Path $engineResourceDir | Out-Null
    Get-ChildItem -Force -LiteralPath $engineResourceDir |
        Where-Object { $_.Name -ne "README.txt" } |
        Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $engineDistDir "*") -Destination $engineResourceDir -Recurse -Force
    if (-not (Test-Path (Join-Path $repoRoot "apps\desktop\node_modules"))) {
        Invoke-Checked "npm" @("--prefix", "apps/desktop", "ci")
    }

    Write-Host "[5/6] Build va ky Tauri NSIS installer"
    Invoke-Checked "npx" @(
        "--prefix", "apps/desktop", "tauri",
        "build", "--bundles", "nsis", "--config", $personalSigningConfigPath
    )

    $installers = @(Get-ChildItem -LiteralPath $installerDir -Filter "*.exe" -File -ErrorAction SilentlyContinue)
    if ($installers.Count -eq 0) {
        throw "Khong tim thay NSIS installer trong $installerDir."
    }

    foreach ($installer in $installers) {
        Invoke-Checked $signToolPath @("verify", "/pa", "/all", $installer.FullName)
    }

    $publicCertificatePath = Join-Path $installerDir "SoatVan-Personal-CodeSigning.cer"
    $publicCertificateBytes = $certificate.Export(
        [System.Security.Cryptography.X509Certificates.X509ContentType]::Cert
    )
    [System.IO.File]::WriteAllBytes($publicCertificatePath, $publicCertificateBytes)

    Write-Host "[6/6] Dong goi ban Portable va cap nhat dist"
    $portableDir = Join-Path $repoRoot "dist\SoatVan-Portable"
    if (Test-Path -LiteralPath $portableDir) { Remove-Item -LiteralPath $portableDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $portableDir | Out-Null

    $desktopExe = Join-Path $repoRoot "apps\desktop\src-tauri\target\release\soatvan-desktop.exe"
    $portableExe = Join-Path $portableDir "SoatVan.exe"
    Copy-Item -LiteralPath $desktopExe -Destination $portableExe -Force
    Invoke-Checked $signToolPath @(
        "sign", "/fd", "SHA256", "/sha1", $certificateThumbprint,
        "/s", "My", $portableExe
    )
    Invoke-Checked $signToolPath @("verify", "/pa", "/all", $portableExe)

    $portableEngineDir = Join-Path $portableDir "engine"
    New-Item -ItemType Directory -Force -Path $portableEngineDir | Out-Null
    Copy-Item -Path (Join-Path $engineDistDir "*") -Destination $portableEngineDir -Recurse -Force

    $desktopVersion = (Get-Content -LiteralPath (Join-Path $repoRoot "apps\desktop\package.json") `
        -Raw | ConvertFrom-Json).version
    $portableZip = Join-Path $repoRoot "dist\SoatVan-v$desktopVersion-Windows-x64-Portable.zip"
    if (Test-Path -LiteralPath $portableZip) { Remove-Item -LiteralPath $portableZip -Force }
    Compress-Archive -Path (Join-Path $portableDir "*") -DestinationPath $portableZip -CompressionLevel Optimal

    Write-Host "Build thanh cong:"
    $installers.FullName | ForEach-Object { Write-Host $_ }
    Write-Host $publicCertificatePath
    Write-Host $portableZip
}
finally {
    if ($certificateThumbprint) {
        foreach ($storeName in $addedCertificateStores) {
            Remove-CertificateFromStore -Thumbprint $certificateThumbprint -StoreName $storeName
        }
    }
    if ($temporarySigningPfx) {
        Remove-Item -LiteralPath $temporarySigningPfx -Force -ErrorAction SilentlyContinue
    }
    if ($certificate) {
        $certificate.Dispose()
    }
    Pop-Location
}
