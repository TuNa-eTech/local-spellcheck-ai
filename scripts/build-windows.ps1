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

function Invoke-SignToolBatch {
    param(
        [Parameter(Mandatory = $true)][string]$SignToolPath,
        [Parameter(Mandatory = $true)][string]$CertificateThumbprint,
        [Parameter(Mandatory = $true)][string[]]$Files
    )

    if ($Files.Count -eq 0) {
        return
    }
    Invoke-Checked $SignToolPath (@(
        "sign", "/fd", "SHA256", "/sha1", $CertificateThumbprint, "/s", "My"
    ) + $Files)
    Invoke-Checked $SignToolPath (@("verify", "/pa", "/all") + $Files)
}

function Invoke-SignToolForFiles {
    param(
        [Parameter(Mandatory = $true)][string]$SignToolPath,
        [Parameter(Mandatory = $true)][string]$CertificateThumbprint,
        [Parameter(Mandatory = $true)][System.IO.FileInfo[]]$Files
    )

    $batch = @()
    $batchLength = 0
    foreach ($file in $Files) {
        if ($batch.Count -ge 20 -or ($batchLength + $file.FullName.Length) -gt 6000) {
            Invoke-SignToolBatch -SignToolPath $SignToolPath `
                -CertificateThumbprint $CertificateThumbprint -Files $batch
            $batch = @()
            $batchLength = 0
        }
        $batch += $file.FullName
        $batchLength += $file.FullName.Length + 3
    }
    Invoke-SignToolBatch -SignToolPath $SignToolPath `
        -CertificateThumbprint $CertificateThumbprint -Files $batch
}

foreach ($commandName in @("uv", "npm", "cargo", "certutil.exe")) {
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
$temporaryRootCertificate = $null
$rootCertificateAdded = $false
$addedCertificateStores = @()
$generatedTempCertThumbprint = $null
$personalCertificateSubject = if ([string]::IsNullOrWhiteSpace($env:SOATVAN_WINDOWS_CERT_SUBJECT)) {
    "CN=SoatVan Personal Use"
}
else {
    $env:SOATVAN_WINDOWS_CERT_SUBJECT
}

Push-Location $repoRoot
try {
    Write-Host "[1/6] Dong bo dependency Python"
    Invoke-Checked "uv" @("sync", "--project", "engine", "--extra", "dev", "--extra", "model", "--extra", "seq2seq", "--locked")

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

    Write-Host "[3/6] Tai certificate ky code tu secret hoac tao self-signed fallback"
    $signingPfxPath = $env:SOATVAN_WINDOWS_CERT_PFX
    if ([string]::IsNullOrWhiteSpace($signingPfxPath) -and -not [string]::IsNullOrWhiteSpace($env:SOATVAN_WINDOWS_CERT_PFX_BASE64)) {
        try {
            $base64Text = $env:SOATVAN_WINDOWS_CERT_PFX_BASE64.Trim("`" `' `r`n`t ")
            $base64Text = $base64Text -replace '(?m)^-----.*?-----$', ''
            $base64Text = ($base64Text -replace '\s+', '')
            $base64Text = $base64Text.Replace('-', '+').Replace('_', '/')
            if ($base64Text.Length % 4 -eq 2) {
                $base64Text += "=="
            }
            elseif ($base64Text.Length % 4 -eq 3) {
                $base64Text += "="
            }
            $pfxBytes = [Convert]::FromBase64String($base64Text)
            $temporarySigningPfx = Join-Path ([System.IO.Path]::GetTempPath()) `
                "soatvan-signing-$PID-$([Guid]::NewGuid().ToString('N')).pfx"
            [System.IO.File]::WriteAllBytes($temporarySigningPfx, $pfxBytes)
            $signingPfxPath = $temporarySigningPfx
        }
        catch {
            Write-Warning "Khong the giai ma SOATVAN_WINDOWS_CERT_PFX_BASE64: $($_.Exception.Message)"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($signingPfxPath) -and (Test-Path -LiteralPath $signingPfxPath -PathType Leaf) -and -not [string]::IsNullOrWhiteSpace($env:SOATVAN_WINDOWS_CERT_PASSWORD)) {
        $keyFlags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet -bor `
            [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::PersistKeySet
        try {
            $candidateCert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
                $signingPfxPath,
                $env:SOATVAN_WINDOWS_CERT_PASSWORD,
                $keyFlags
            )
            $codeSigningOid = "1.3.6.1.5.5.7.3.3"
            $enhancedKeyUsage = $candidateCert.Extensions |
                Where-Object { $_.Oid.Value -eq "2.5.29.37" } |
                Select-Object -First 1
            $ekuOids = @($enhancedKeyUsage.EnhancedKeyUsages | ForEach-Object { $_.Value })
            if ($candidateCert.HasPrivateKey -and
                $candidateCert.NotBefore -le (Get-Date) -and
                $candidateCert.NotAfter -gt (Get-Date).AddDays(30) -and
                $ekuOids -contains $codeSigningOid) {
                $certificate = $candidateCert
                Write-Host "Da load certificate tu secret thanh cong (Thumbprint: $($certificate.Thumbprint))"
            }
            else {
                Write-Warning "Certificate tu secret khong hop le hoac khong phai code-signing."
            }
        }
        catch {
            Write-Warning "Khong the mo PFX bang password: $($_.Exception.Message)"
        }
    }

    if (-not $certificate) {
        Write-Host "Dang tao self-signed certificate cho build ca nhan: $personalCertificateSubject"
        $certPassword = [System.Guid]::NewGuid().ToString("N")
        $temporarySigningPfx = Join-Path ([System.IO.Path]::GetTempPath()) `
            "soatvan-generated-$PID-$([Guid]::NewGuid().ToString('N')).pfx"

        $created = $false
        try {
            Add-Type -AssemblyName System.Core, System.Security -ErrorAction SilentlyContinue
            $distinguishedName = [System.Security.Cryptography.X509Certificates.X500DistinguishedName]::new($personalCertificateSubject)
            $rsa = [System.Security.Cryptography.RSA]::Create(3072)
            $req = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
                $distinguishedName,
                $rsa,
                [System.Security.Cryptography.HashAlgorithmName]::SHA256,
                [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
            )

            $ekuOids = [System.Security.Cryptography.OidCollection]::new()
            $ekuOids.Add([System.Security.Cryptography.Oid]::new("1.3.6.1.5.5.7.3.3"))
            $req.CertificateExtensions.Add(
                [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($ekuOids, $false)
            )

            $req.CertificateExtensions.Add(
                [System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
                    [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature,
                    $true
                )
            )

            $now = [System.DateTimeOffset]::UtcNow
            $certWithKey = $req.CreateSelfSigned($now, $now.AddYears(5))
            $pfxBytes = $certWithKey.Export(
                [System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx,
                $certPassword
            )
            [System.IO.File]::WriteAllBytes($temporarySigningPfx, $pfxBytes)
            $certWithKey.Dispose()
            $rsa.Dispose()
            $created = $true
        }
        catch {
            Write-Warning "CertificateRequest that bai ($($_.Exception.Message)), thu bang openssl..."
        }

        if (-not $created) {
            $openssl = (Get-Command "openssl.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
            if (-not $openssl -and (Test-Path "C:\Program Files\Git\usr\bin\openssl.exe")) {
                $openssl = "C:\Program Files\Git\usr\bin\openssl.exe"
            }
            if ($openssl) {
                $tempKey = Join-Path ([System.IO.Path]::GetTempPath()) "soatvan-temp-$PID.key"
                $tempCrt = Join-Path ([System.IO.Path]::GetTempPath()) "soatvan-temp-$PID.crt"
                try {
                    & $openssl req -x509 -newkey rsa:3072 -keyout $tempKey -out $tempCrt -days 1825 -nodes `
                        -subj "/CN=SoatVan Personal Use" `
                        -addext "extendedKeyUsage = codeSigning"
                    & $openssl pkcs12 -export -out $temporarySigningPfx -inkey $tempKey -in $tempCrt `
                        -passout "pass:$certPassword"
                    $created = $true
                }
                finally {
                    Remove-Item -LiteralPath $tempKey -Force -ErrorAction SilentlyContinue
                    Remove-Item -LiteralPath $tempCrt -Force -ErrorAction SilentlyContinue
                }
            }
        }

        if (-not $created -or -not (Test-Path -LiteralPath $temporarySigningPfx -PathType Leaf)) {
            throw "Khong the tao self-signed certificate bang ca CertificateRequest va OpenSSL."
        }

        $keyFlags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet -bor `
            [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::PersistKeySet -bor `
            [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable
        $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
            $temporarySigningPfx,
            $certPassword,
            $keyFlags
        )
        Write-Host "Da tao certificate self-signed thanh cong (Thumbprint: $($certificate.Thumbprint))"
    }
    $certificateThumbprint = $certificate.Thumbprint
    Write-Host "Dang dang ky certificate vao CurrentUser\My"
    if (Add-CertificateToStore -Certificate $certificate -StoreName "My") {
        $addedCertificateStores += "My"
    }
    Write-Host "Da dang ky certificate vao CurrentUser\My"

    $temporaryRootCertificate = Join-Path ([System.IO.Path]::GetTempPath()) `
        "soatvan-root-$PID-$([Guid]::NewGuid().ToString('N')).cer"
    [System.IO.File]::WriteAllBytes(
        $temporaryRootCertificate,
        $certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
    )
    Write-Host "Dang trust public certificate vao Root (LocalMachine)"
    try {
        Import-Certificate -FilePath $temporaryRootCertificate -CertStoreLocation "Cert:\LocalMachine\Root" -ErrorAction Stop | Out-Null
        $rootCertificateAdded = $true
    }
    catch {
        & "certutil.exe" -f -addstore Root $temporaryRootCertificate | Out-Null
        $rootCertificateAdded = $true
    }
    Write-Host "Da trust public certificate vao Root"
    $signToolPath = Get-SignToolPath

    $engineFilesToSign = Get-ChildItem -LiteralPath $engineDistDir -Recurse -File |
        Where-Object { $_.Extension -in @(".exe", ".dll") }
    Write-Host "Ky $($engineFilesToSign.Count) binary engine theo batch"
    Invoke-SignToolForFiles -SignToolPath $signToolPath `
        -CertificateThumbprint $certificateThumbprint -Files $engineFilesToSign

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
    if ($rootCertificateAdded -and $certificateThumbprint) {
        Get-ChildItem -Path "Cert:\LocalMachine\Root" -ErrorAction SilentlyContinue |
            Where-Object { $_.Thumbprint -eq $certificateThumbprint } |
            Remove-Item -Force -ErrorAction SilentlyContinue
        & "certutil.exe" -f -delstore Root $certificateThumbprint | Out-Null
    }
    if ($certificateThumbprint) {
        foreach ($storeName in $addedCertificateStores) {
            Remove-CertificateFromStore -Thumbprint $certificateThumbprint -StoreName $storeName
        }
    }
    if ($generatedTempCertThumbprint) {
        Remove-CertificateFromStore -Thumbprint $generatedTempCertThumbprint -StoreName "My"
    }
    if ($temporarySigningPfx) {
        Remove-Item -LiteralPath $temporarySigningPfx -Force -ErrorAction SilentlyContinue
    }
    if ($temporaryRootCertificate) {
        Remove-Item -LiteralPath $temporaryRootCertificate -Force -ErrorAction SilentlyContinue
    }
    if ($certificate) {
        $certificate.Dispose()
    }
    Pop-Location
}
