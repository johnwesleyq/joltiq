[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(ParameterSetName = 'List')][switch]$List,
    [Parameter(ParameterSetName = 'Validate')][switch]$ValidateHarness,
    [Parameter(ParameterSetName = 'Run', Mandatory)][string]$CaseId,
    [Parameter(ParameterSetName = 'Run')][string]$RunId = (Get-Date -Format 'yyyyMMdd-HHmmss'),
    [Parameter(ParameterSetName = 'Run')][string]$SnapshotName,
    [Parameter(ParameterSetName = 'Run')][string]$Msi,
    [Parameter(ParameterSetName = 'Run')][string]$ServerCommit,
    [Parameter(ParameterSetName = 'Run')][string]$OutlookVersion,
    [Parameter(ParameterSetName = 'Run')][switch]$OutlookLicenseConfirmed,
    [Parameter(ParameterSetName = 'Run')][switch]$DisposableProfile,
    [Parameter(ParameterSetName = 'Run')][switch]$Finalize,
    [string]$EvidenceRoot = (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'JoltIQ\AcceptanceEvidence'),
    [string]$CatalogPath = (Join-Path $PSScriptRoot 'cases.json'),
    [string]$FixtureProject = (Join-Path $PSScriptRoot '..\..\tests\JoltIQ.OutlookCrashFixture\JoltIQ.OutlookCrashFixture.csproj')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'EvidenceBundle.psm1') -Force
$catalog = Get-JoltIqCatalog $CatalogPath

if ($List) {
    $catalog.cases | Select-Object id, category, mode, repetitions, expected | Format-Table -AutoSize
    exit 0
}

if ($ValidateHarness) {
    & dotnet run --project $FixtureProject -c Release -- validate-harness --cases $CatalogPath
    if ($LASTEXITCODE -ne 0) { throw 'Portable harness contract validation failed.' }
    if (-not (Test-JoltIqEvidenceModule -CatalogPath $CatalogPath)) { throw 'PowerShell evidence module self-test failed.' }
    Write-Host 'Acceptance harness contract and fail-closed evidence tests passed.' -ForegroundColor Green
    exit 0
}

if (-not $IsWindows) { throw 'Acceptance case execution is supported only on Windows 10/11 64-bit.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Only 64-bit Windows 10/11 is supported.' }
$os = Get-CimInstance Win32_OperatingSystem
if ($os.Version -notmatch '^10\.') { throw "Unsupported Windows version: $($os.Version)" }
$case = $catalog.cases | Where-Object id -eq $CaseId | Select-Object -First 1
if (-not $case) { throw "Unknown case: $CaseId. Use -List." }
$bundle = Join-Path (Join-Path $EvidenceRoot $CaseId) $RunId

if ($Finalize) {
    $validation = Test-JoltIqEvidenceBundle -Bundle $bundle -CatalogPath $CatalogPath
    $aggregate = Update-JoltIqAggregate -Root $EvidenceRoot -CatalogPath $CatalogPath
    if (-not $validation.valid) {
        $validation.errors | ForEach-Object { Write-Error $_ -ErrorAction Continue }
        exit 1
    }
    Write-Host "Validated $bundle" -ForegroundColor Green
    Write-Host "Overall Windows acceptance passed: $($aggregate.passed)"
    if (-not $aggregate.passed) { $aggregate.missing | ForEach-Object { Write-Host "Pending: $_" } }
    exit 0
}

if (-not $SnapshotName) { throw '-SnapshotName is required before a case begins; revert the VM to a named clean snapshot first.' }
if ($ServerCommit -notmatch '^[0-9a-fA-F]{7,64}$') { throw '-ServerCommit must be the exact 7-64 character hexadecimal commit tested.' }
if ($case.mode -eq 'licensed_outlook' -and (-not $OutlookVersion -or -not $OutlookLicenseConfirmed -or -not $DisposableProfile)) {
    throw 'The licensed Outlook case requires -OutlookVersion, -OutlookLicenseConfirmed, and -DisposableProfile.'
}
$bundle = New-JoltIqEvidenceBundle -Root $EvidenceRoot -CaseId $CaseId -RunId $RunId -CatalogPath $CatalogPath
$registryPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$harnessCommit = (& git -C (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path rev-parse HEAD 2>$null)
$fixtureVersion = (& dotnet run --project $FixtureProject -c Release -- version)
$agentExe = Join-Path $env:ProgramFiles 'JoltIQ Companion\JoltIQCompanion.exe'
$agentVersion = if (Test-Path $agentExe) { (Get-Item $agentExe).VersionInfo.ProductVersion } else { 'not-installed' }
$environment = [ordered]@{
    os_caption = $os.Caption
    display_version = (Get-ItemProperty $registryPath).DisplayVersion
    os_build = $os.BuildNumber
    architecture = '64-bit'
    agent_version = $agentVersion
    server_commit = $ServerCommit
    harness_commit = "$harnessCommit".Trim()
    fixture_version = "$fixtureVersion".Trim()
    outlook_version = if ($case.mode -eq 'licensed_outlook') { $OutlookVersion } else { 'not_applicable' }
    outlook_license_confirmed = [bool]$OutlookLicenseConfirmed
    disposable_profile = [bool]$DisposableProfile
}
$environment | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $bundle 'environment.json') -Encoding utf8
[ordered]@{ name = $SnapshotName; clean = $true; reverted_at = (Get-Date).ToUniversalTime().ToString('o'); confirmed_by = $env:USERNAME } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundle 'snapshot.json') -Encoding utf8

if ($Msi) {
    $signature = Get-AuthenticodeSignature -FilePath $Msi
    $signer = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { 'none' }
    [ordered]@{ msi = (Split-Path $Msi -Leaf); sha256 = (Get-FileHash $Msi -Algorithm SHA256).Hash.ToLowerInvariant(); signature_status = $signature.Status.ToString(); signer = $signer; installed = (Test-Path $agentExe); version = $agentVersion } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundle 'installer.json') -Encoding utf8
}

if ($case.mode -eq 'fixture') {
    if ($CaseId -ne 'unsupported-addin-absent') {
        & dotnet run --project $FixtureProject -c Release -- install --progid $catalog.fixture.progid
    }
    if ($CaseId -notin @('unsupported-addin-absent', 'diagnosis-crash-absent', 'diagnosis-wrong-module')) {
        & dotnet run --project $FixtureProject -c Release -- emit --kind matching --module $catalog.fixture.faulting_module
    } elseif ($CaseId -eq 'diagnosis-wrong-module') {
        & dotnet run --project $FixtureProject -c Release -- emit --kind nonmatching --module $catalog.fixture.faulting_module
    }
}

$remaining = @($catalog.required_evidence | Where-Object { -not (Test-Path -LiteralPath (Join-Path $bundle $_)) })
Write-Host "Initialized authorized acceptance case $CaseId at $bundle" -ForegroundColor Cyan
Write-Host 'Run the case using the operating guide. Export only redacted evidence; never copy email bodies, documents, credentials, cookies, or full event payloads.' -ForegroundColor Yellow
Write-Host 'Still required:'
$remaining | ForEach-Object { Write-Host "  $_" }
Write-Host "When evidence is present, run: .\Invoke-OutlookPdfMvp.ps1 -CaseId '$CaseId' -RunId '$RunId' -EvidenceRoot '$EvidenceRoot' -Finalize"
exit 0
