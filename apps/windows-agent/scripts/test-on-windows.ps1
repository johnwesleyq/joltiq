<#
.SYNOPSIS Entry point for the JoltIQ Windows installer and Outlook PDF acceptance suite.
.DESCRIPTION Run only on authorized, disposable Windows 10/11 64-bit VMs. Evidence is written outside the repository.
#>
[CmdletBinding(DefaultParameterSetName = 'Validate')]
param(
    [Parameter(ParameterSetName = 'Validate')][switch]$ValidateHarness,
    [Parameter(ParameterSetName = 'List')][switch]$List,
    [Parameter(ParameterSetName = 'Lifecycle', Mandatory)][switch]$InstallerLifecycle,
    [Parameter(ParameterSetName = 'Run', Mandatory)][string]$CaseId,
    [Parameter(ParameterSetName = 'Run')][string]$RunId = (Get-Date -Format 'yyyyMMdd-HHmmss'),
    [Parameter(ParameterSetName = 'Run')][string]$SnapshotName,
    [Parameter(ParameterSetName = 'Run')][switch]$Finalize,
    [Parameter(ParameterSetName = 'Run')][Parameter(ParameterSetName = 'Lifecycle', Mandatory)][string]$Msi,
    [Parameter(ParameterSetName = 'Lifecycle')][string]$PreviousMsi,
    [Parameter(ParameterSetName = 'Lifecycle')][string]$LifecycleEvidencePath,
    [Parameter(ParameterSetName = 'Run')][string]$ServerCommit,
    [Parameter(ParameterSetName = 'Run')][string]$OutlookVersion,
    [Parameter(ParameterSetName = 'Run')][switch]$OutlookLicenseConfirmed,
    [Parameter(ParameterSetName = 'Run')][switch]$DisposableProfile,
    [string]$EvidenceRoot = (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'JoltIQ\AcceptanceEvidence')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'acceptance\Invoke-OutlookPdfMvp.ps1'
$gate = Join-Path $PSScriptRoot 'BuildGate.psm1'

if ($List) { & $runner -List; exit $LASTEXITCODE }
if ($ValidateHarness -or $PSCmdlet.ParameterSetName -eq 'Validate') { & $runner -ValidateHarness; exit $LASTEXITCODE }

if ($InstallerLifecycle) {
    if (-not $IsWindows) { throw 'Installer lifecycle validation requires a clean Windows VM.' }
    Import-Module $gate -Force
    $Msi = (Resolve-Path $Msi).Path
    if ($PreviousMsi) { $PreviousMsi = (Resolve-Path $PreviousMsi).Path }
    $artifactRoot = Split-Path $Msi -Parent
    $buildManifestPath = Join-Path $artifactRoot 'build-manifest.json'
    Assert-JoltIqFile -Path $buildManifestPath -Purpose 'build manifest'
    $buildManifest = Get-Content -LiteralPath $buildManifestPath -Raw | ConvertFrom-Json
    $signature = Get-AuthenticodeSignature -FilePath $Msi
    $internalMarker = Test-Path -LiteralPath (Join-Path $artifactRoot 'INTERNAL-ONLY.txt')
    $results = [ordered]@{ started_at = (Get-Date).ToUniversalTime().ToString('o'); distribution = $buildManifest.distribution; msi = Split-Path $Msi -Leaf; previous_msi = if ($PreviousMsi) { Split-Path $PreviousMsi -Leaf } else { $null } }

    function Invoke-Msi([string]$Arguments, [string]$Operation, [int[]]$AllowedExitCodes = @(0, 3010)) {
        $process = Start-Process msiexec.exe -ArgumentList $Arguments -Wait -PassThru
        if ($process.ExitCode -notin $AllowedExitCodes) { throw "$Operation failed with Windows Installer exit code $($process.ExitCode)." }
        return $process.ExitCode
    }
    function Get-JoltIqInstalledProduct {
        $roots = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
        @(Get-ItemProperty $roots -ErrorAction SilentlyContinue | Where-Object DisplayName -eq 'JoltIQ Companion') | Select-Object -First 1
    }
    function Assert-Installed {
        $product = Get-JoltIqInstalledProduct
        if (-not $product) { throw 'JoltIQ Companion is absent from installed-product metadata.' }
        $exe = Join-Path $env:ProgramFiles 'JoltIQ Companion\JoltIQCompanion.exe'
        Assert-JoltIqFile -Path $exe -Purpose 'installed tray executable'
        $shortcut = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\JoltIQ\JoltIQ Companion.lnk'
        Assert-JoltIqFile -Path $shortcut -Purpose 'Start menu shortcut'
        $service = Get-Service JoltIQBroker -ErrorAction Stop
        if ($service.Status -ne 'Running') { throw 'JoltIQBroker service is not running.' }
        $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='JoltIQBroker'"
        if ($serviceInfo.StartName -ne 'LocalSystem' -or $serviceInfo.StartMode -ne 'Auto') { throw 'Broker service account or startup mode is wrong.' }
        $sddl = (& sc.exe sdshow JoltIQBroker | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $sddl.StartsWith('D:')) { throw 'Broker service ACL could not be verified.' }
        return [ordered]@{ product_version = $product.DisplayVersion; arp_comments = $product.Comments; tray = $exe; shortcut = $shortcut; broker_status = $service.Status.ToString(); broker_account = $serviceInfo.StartName; broker_sddl = $sddl }
    }

    try {
        $firstMsi = if ($PreviousMsi) { $PreviousMsi } else { $Msi }
        $results.install_exit = Invoke-Msi "/i `"$firstMsi`" /qn /l*v `"$env:TEMP\joltiq-install.log`"" 'install'
        $results.install = Assert-Installed
        if (-not $PreviousMsi) {
            Assert-JoltIqInstallerClassification -Distribution $buildManifest.distribution -SignatureStatus $signature.Status.ToString() -InternalMarkerPresent:$internalMarker -ArpComments $results.install.arp_comments
        }

        $tray = Start-Process $results.install.tray -PassThru
        Start-Sleep -Seconds 2
        if ($tray.HasExited) { throw 'Installed tray executable exited during launch validation.' }
        Stop-Process -Id $tray.Id -Force
        $results.tray_launch = 'started'

        $results.repair_exit = Invoke-Msi "/fa `"$firstMsi`" /qn /l*v `"$env:TEMP\joltiq-repair.log`"" 'repair'
        $results.repair = Assert-Installed

        if ($PreviousMsi) {
            $results.upgrade_exit = Invoke-Msi "/i `"$Msi`" /qn /l*v `"$env:TEMP\joltiq-upgrade.log`"" 'upgrade'
            $results.upgrade = Assert-Installed
            Assert-JoltIqInstallerClassification -Distribution $buildManifest.distribution -SignatureStatus $signature.Status.ToString() -InternalMarkerPresent:$internalMarker -ArpComments $results.upgrade.arp_comments
            $downgrade = Start-Process msiexec.exe -ArgumentList "/i `"$PreviousMsi`" /qn /l*v `"$env:TEMP\joltiq-rollback.log`"" -Wait -PassThru
            if ($downgrade.ExitCode -eq 0) { throw 'Downgrade unexpectedly succeeded; rollback protection was not exercised.' }
            $afterRollback = Assert-Installed
            if ($afterRollback.product_version -ne $results.upgrade.product_version) { throw 'Failed downgrade did not preserve the upgraded product.' }
            $results.rollback = [ordered]@{ attempted_downgrade_exit = $downgrade.ExitCode; preserved_version = $afterRollback.product_version }
        }

        $results.uninstall_exit = Invoke-Msi "/x `"$Msi`" /qn /l*v `"$env:TEMP\joltiq-uninstall.log`"" 'uninstall'
        if (Get-Service JoltIQBroker -ErrorAction SilentlyContinue) { throw 'Broker service remained after uninstall.' }
        if (Test-Path (Join-Path $env:ProgramFiles 'JoltIQ Companion\JoltIQCompanion.exe')) { throw 'Tray executable remained after uninstall.' }
        if (Test-Path (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\JoltIQ\JoltIQ Companion.lnk')) { throw 'Start menu shortcut remained after uninstall.' }
        $results.uninstall = 'removed_service_files_and_shortcut'
        $results.passed = $true
    }
    finally {
        if (Get-JoltIqInstalledProduct) {
            Start-Process msiexec.exe -ArgumentList "/x `"$Msi`" /qn" -Wait | Out-Null
            if ($PreviousMsi) { Start-Process msiexec.exe -ArgumentList "/x `"$PreviousMsi`" /qn" -Wait | Out-Null }
        }
        $results.completed_at = (Get-Date).ToUniversalTime().ToString('o')
        if (-not $LifecycleEvidencePath) { $LifecycleEvidencePath = Join-Path $artifactRoot 'installer-lifecycle.json' }
        $results | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $LifecycleEvidencePath -Encoding utf8
        Write-Host "Installer lifecycle evidence: $LifecycleEvidencePath"
    }
    exit 0
}

if ($Msi -and -not $Finalize) {
    $signature = Get-AuthenticodeSignature -FilePath $Msi
    Write-Host "Installer signature: $($signature.Status)"
    Start-Process msiexec -ArgumentList "/i `"$Msi`" /qn /l*v `"$env:TEMP\joltiq-install.log`"" -Wait
    $service = Get-Service JoltIQBroker -ErrorAction Stop
    if ($service.Status -ne 'Running') { throw 'JoltIQBroker did not start.' }
}

$arguments = @{ CaseId = $CaseId; RunId = $RunId; EvidenceRoot = $EvidenceRoot }
if ($SnapshotName) { $arguments.SnapshotName = $SnapshotName }
if ($Msi) { $arguments.Msi = $Msi }
if ($ServerCommit) { $arguments.ServerCommit = $ServerCommit }
if ($OutlookVersion) { $arguments.OutlookVersion = $OutlookVersion }
if ($OutlookLicenseConfirmed) { $arguments.OutlookLicenseConfirmed = $true }
if ($DisposableProfile) { $arguments.DisposableProfile = $true }
if ($Finalize) { $arguments.Finalize = $true }
& $runner @arguments
exit $LASTEXITCODE
