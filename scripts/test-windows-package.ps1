param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"
$installer = Get-Item -LiteralPath $InstallerPath
$installDir = Join-Path $env:RUNNER_TEMP "SoatVan-Acceptance-$PID"
$hostProcess = $null

try {
    $install = Start-Process -FilePath $installer.FullName `
        -ArgumentList @("/S", "/D=$installDir") -Wait -PassThru
    if ($install.ExitCode -ne 0) {
        throw "NSIS install failed: $($install.ExitCode)"
    }
    $application = Get-ChildItem $installDir -Recurse -Filter "*.exe" |
        Where-Object { $_.Name -notmatch "uninstall|soatvan-engine" } |
        Select-Object -First 1
    if (-not $application) {
        throw "Installed application not found"
    }

    $manifestPath = Join-Path $env:RUNNER_TEMP "soatvan-app.manifest"
    $manifestTool = Get-Command mt.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if (-not $manifestTool) {
        $manifestTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\mt.exe" `
            -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $manifestTool) {
        throw "Windows manifest tool not found"
    }
    $installerManifestPath = Join-Path $env:RUNNER_TEMP "soatvan-installer.manifest"
    & $manifestTool -nologo "-inputresource:$($installer.FullName);#1" "-out:$installerManifestPath"
    if ($LASTEXITCODE -ne 0 -or -not (Select-String -LiteralPath $installerManifestPath -Pattern 'level="asInvoker"' -Quiet)) {
        throw "Installer manifest is not standard-user/asInvoker"
    }
    & $manifestTool -nologo "-inputresource:$($application.FullName);#1" "-out:$manifestPath"
    if ($LASTEXITCODE -ne 0 -or -not (Select-String -LiteralPath $manifestPath -Pattern 'level="asInvoker"' -Quiet)) {
        throw "Application manifest is not standard-user/asInvoker"
    }

    $hostProcess = Start-Process -FilePath $application.FullName -PassThru
    Start-Sleep -Seconds 8
    if ($hostProcess.HasExited) {
        throw "Installed application exited during startup"
    }
    $engineProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'soatvan-engine.exe'")
    if ($engineProcesses.Count -eq 0) {
        throw "Packaged sidecar did not start"
    }
    $ownedIds = @($hostProcess.Id) + @($engineProcesses.ProcessId)
    $connections = @(
        Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
            Where-Object { $ownedIds -contains $_.OwningProcess }
    )
    if ($connections.Count -ne 0) {
        throw "Unexpected application egress: $($connections | Out-String)"
    }

    Stop-Process -Id $hostProcess.Id -Force
    $hostProcess = $null
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $remaining = @(Get-CimInstance Win32_Process -Filter "Name = 'soatvan-engine.exe'")
    } while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)
    if ($remaining.Count -gt 0) {
        throw "Orphan sidecar remained after host exit"
    }

    $scanner = Join-Path $env:ProgramFiles "Windows Defender\MpCmdRun.exe"
    if (-not (Test-Path $scanner)) {
        $scanner = Get-ChildItem "$env:ProgramData\Microsoft\Windows Defender\Platform\*\MpCmdRun.exe" `
            -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $scanner -or -not (Test-Path $scanner)) {
        throw "Microsoft Defender CLI not found"
    }
    & $scanner -Scan -ScanType 3 -File $installer.FullName -DisableRemediation
    if ($LASTEXITCODE -ne 0) {
        throw "Microsoft Defender scan failed: $LASTEXITCODE"
    }
}
finally {
    if ($hostProcess -and -not $hostProcess.HasExited) {
        Stop-Process -Id $hostProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
