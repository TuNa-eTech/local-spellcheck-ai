param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"
$installer = Get-Item -LiteralPath $InstallerPath
$userName = "SoatVanCI$PID"
$plainPassword = "Sv!$PID-$(New-Guid)"
$securePassword = ConvertTo-SecureString $plainPassword -AsPlainText -Force
$credential = [System.Management.Automation.PSCredential]::new(
    "$env:COMPUTERNAME\$userName",
    $securePassword
)
$testUser = $null
$testUserSid = $null
$profilePath = $null
$publicInstaller = Join-Path $env:PUBLIC "SoatVan-Acceptance-$PID.exe"
$installDir = $null
$hostProcess = $null
$ownedIds = [System.Collections.Generic.HashSet[int]]::new()

try {
    $testUser = New-LocalUser -Name $userName -Password $securePassword `
        -PasswordNeverExpires -UserMayNotChangePassword `
        -Description "Ephemeral SoatVan GitHub Actions acceptance user"
    $testUserSid = $testUser.SID.Value
    $administratorMembers = @(Get-LocalGroupMember -SID "S-1-5-32-544")
    if ($administratorMembers.SID.Value -contains $testUser.SID.Value) {
        throw "Acceptance account unexpectedly belongs to Administrators"
    }

    $profileProbe = Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" `
        -ArgumentList @("/c", "exit 0") -Credential $credential -LoadUserProfile `
        -WorkingDirectory "$env:SystemRoot\System32" -Wait -PassThru
    if ($profileProbe.ExitCode -ne 0) {
        throw "Failed to initialize standard-user profile: $($profileProbe.ExitCode)"
    }
    $profileDeadline = (Get-Date).AddSeconds(10)
    do {
        $profile = Get-CimInstance Win32_UserProfile |
            Where-Object { $_.SID -eq $testUserSid } |
            Select-Object -First 1
        if (-not $profile) {
            Start-Sleep -Milliseconds 250
        }
    } while (-not $profile -and (Get-Date) -lt $profileDeadline)
    if (-not $profile -or -not $profile.LocalPath) {
        throw "Standard-user profile was not created"
    }
    $profilePath = $profile.LocalPath
    $installDir = Join-Path $profilePath "AppData\Local\Programs\SoatVan-Acceptance"
    Copy-Item -LiteralPath $installer.FullName -Destination $publicInstaller
    $install = Start-Process -FilePath $publicInstaller `
        -ArgumentList @("/S", "/D=$installDir") -Credential $credential -LoadUserProfile `
        -WorkingDirectory "$env:SystemRoot\System32" -Wait -PassThru
    if ($install.ExitCode -ne 0) {
        throw "NSIS install failed: $($install.ExitCode)"
    }
    $application = Get-ChildItem $installDir -Recurse -Filter "soatvan-desktop.exe" |
        Select-Object -First 1
    if (-not $application) {
        $installedExecutables = @(Get-ChildItem $installDir -Recurse -Filter "*.exe" |
            Select-Object -ExpandProperty FullName)
        throw "Installed application not found. Executables: $($installedExecutables -join ', ')"
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

    $hostProcess = Start-Process -FilePath $application.FullName `
        -Credential $credential -LoadUserProfile -WorkingDirectory $installDir -PassThru
    Start-Sleep -Seconds 8
    if ($hostProcess.HasExited) {
        throw "Installed application exited during startup"
    }
    $processSnapshot = @(Get-CimInstance Win32_Process)
    [void]$ownedIds.Add($hostProcess.Id)
    do {
        $previousCount = $ownedIds.Count
        foreach ($process in $processSnapshot) {
            if ($ownedIds.Contains([int]$process.ParentProcessId)) {
                [void]$ownedIds.Add([int]$process.ProcessId)
            }
        }
    } while ($ownedIds.Count -ne $previousCount)
    $ownedProcesses = @($processSnapshot | Where-Object {
        $ownedIds.Contains([int]$_.ProcessId)
    })
    $engineProcesses = @($ownedProcesses | Where-Object { $_.Name -eq "soatvan-engine.exe" })
    if ($engineProcesses.Count -eq 0) {
        throw "Packaged sidecar did not start"
    }
    foreach ($process in $ownedProcesses) {
        $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwner
        if ($owner.ReturnValue -ne 0 -or $owner.User -ne $userName) {
            throw "Packaged process did not run as standard user: $($process.ProcessId)"
        }
    }
    $connections = @(
        Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
            Where-Object { $ownedIds.Contains([int]$_.OwningProcess) }
    )
    if ($connections.Count -ne 0) {
        throw "Unexpected application egress: $($connections | Out-String)"
    }

    Stop-Process -Id $hostProcess.Id -Force
    $hostProcess = $null
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $remaining = @(Get-CimInstance Win32_Process | Where-Object {
            $ownedIds.Contains([int]$_.ProcessId)
        })
    } while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)
    if ($remaining.Count -gt 0) {
        throw "Packaged process tree remained after host exit: $($remaining.ProcessId -join ', ')"
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
    if ($ownedIds.Count -gt 0) {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $ownedIds.Contains([int]$_.ProcessId) } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $publicInstaller -Force -ErrorAction SilentlyContinue
    if ($installDir) {
        Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($testUserSid) {
        Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue |
            Where-Object { $_.SID -eq $testUserSid } |
            Remove-CimInstance -ErrorAction SilentlyContinue
    }
    if ($testUser) {
        Remove-LocalUser -Name $userName -ErrorAction SilentlyContinue
    }
    $plainPassword = $null
}
