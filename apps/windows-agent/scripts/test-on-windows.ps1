<#
.SYNOPSIS Entry point for the JoltIQ Windows installer and Outlook PDF acceptance suite.
.DESCRIPTION Run only on authorized, disposable Windows 10/11 64-bit VMs. Evidence is written outside the repository.
#>
[CmdletBinding(DefaultParameterSetName = 'Validate')]
param(
    [Parameter(ParameterSetName = 'Validate')][switch]$ValidateHarness,
    [Parameter(ParameterSetName = 'List')][switch]$List,
    [Parameter(ParameterSetName = 'Run', Mandatory)][string]$CaseId,
    [Parameter(ParameterSetName = 'Run')][string]$RunId = (Get-Date -Format 'yyyyMMdd-HHmmss'),
    [Parameter(ParameterSetName = 'Run')][string]$SnapshotName,
    [Parameter(ParameterSetName = 'Run')][switch]$Finalize,
    [Parameter(ParameterSetName = 'Run')][string]$Msi,
    [Parameter(ParameterSetName = 'Run')][string]$ServerCommit,
    [Parameter(ParameterSetName = 'Run')][string]$OutlookVersion,
    [Parameter(ParameterSetName = 'Run')][switch]$OutlookLicenseConfirmed,
    [Parameter(ParameterSetName = 'Run')][switch]$DisposableProfile,
    [string]$EvidenceRoot = (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'JoltIQ\AcceptanceEvidence')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'acceptance\Invoke-OutlookPdfMvp.ps1'

if ($List) { & $runner -List; exit $LASTEXITCODE }
if ($ValidateHarness -or $PSCmdlet.ParameterSetName -eq 'Validate') { & $runner -ValidateHarness; exit $LASTEXITCODE }

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
