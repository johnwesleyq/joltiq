[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(ParameterSetName = 'List')][switch]$List,
    [Parameter(ParameterSetName = 'Validate')][switch]$ValidateHarness,
    [Parameter(ParameterSetName = 'Run', Mandatory)][string]$CaseId,
    [Parameter(ParameterSetName = 'Run')][string]$RunId = (Get-Date -Format 'yyyyMMdd-HHmmss'),
    [Parameter(ParameterSetName = 'Run')][string]$SnapshotName,
    [Parameter(ParameterSetName = 'Run')][string]$Msi,
    [Parameter(ParameterSetName = 'Run')][string]$ServerCommit,
    [Parameter(ParameterSetName = 'Run')][switch]$Finalize,
    [string]$EvidenceRoot = (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'JoltIQ\ComputerUseAcceptanceEvidence'),
    [string]$CatalogPath = (Join-Path $PSScriptRoot 'computer-use-cases.json')
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
    if ($catalog.acceptance_kind -ne 'computer_use') { throw 'Catalog is not a computer-use acceptance catalog.' }
    $win10Cases = @($catalog.cases | Where-Object { $_.PSObject.Properties.Match('platforms').Count -gt 0 -and $_.platforms -contains 'win10' })
    $win11Cases = @($catalog.cases | Where-Object { $_.PSObject.Properties.Match('platforms').Count -gt 0 -and $_.platforms -contains 'win11' })
    if ($win10Cases.Count -lt 1 -or $win11Cases.Count -lt 1) {
        throw 'Catalog must include clean Windows 10 and Windows 11 cases.'
    }
    if (@($catalog.cases | Where-Object category -eq 'stop').Count -lt 3) { throw 'Catalog must exercise all declared STOP races.' }
    if (-not (Test-JoltIqComputerUseEvidenceModule -CatalogPath $CatalogPath)) { throw 'Computer-use evidence module self-test failed.' }
    Write-Host 'General computer-use acceptance contract and fail-closed evidence tests passed.' -ForegroundColor Green
    exit 0
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Acceptance case execution is supported only on Windows 10/11 64-bit.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Only 64-bit Windows 10/11 is supported.' }
$os = Get-CimInstance Win32_OperatingSystem
if ($os.Version -notmatch '^10\.') { throw "Unsupported Windows version: $($os.Version)" }
$case = $catalog.cases | Where-Object id -eq $CaseId | Select-Object -First 1
if (-not $case) { throw "Unknown case: $CaseId. Use -List." }
$bundle = Join-Path (Join-Path $EvidenceRoot $CaseId) $RunId

if ($Finalize) {
    $validation = Test-JoltIqComputerUseEvidenceBundle -Bundle $bundle -CatalogPath $CatalogPath
    $aggregate = Update-JoltIqAggregate -Root $EvidenceRoot -CatalogPath $CatalogPath
    if (-not $validation.valid) {
        $validation.errors | ForEach-Object { Write-Error $_ -ErrorAction Continue }
        exit 1
    }
    Write-Host "Validated $bundle" -ForegroundColor Green
    Write-Host "Overall Windows computer-use acceptance passed: $($aggregate.passed)"
    if (-not $aggregate.passed) { $aggregate.missing | ForEach-Object { Write-Host "Pending: $_" } }
    exit 0
}

if (-not $SnapshotName) { throw '-SnapshotName is required; revert a disposable VM to a named clean snapshot before every case.' }
if ($ServerCommit -notmatch '^[0-9a-fA-F]{7,64}$') { throw '-ServerCommit must be the exact 7-64 character hexadecimal commit tested.' }
if (-not $Msi) { throw '-Msi is required so the exact installer hash and signature are recorded.' }
$resolvedMsi = (Resolve-Path $Msi).Path
$agentExe = Join-Path $env:ProgramFiles 'JoltIQ Companion\JoltIQCompanion.exe'
if (-not (Test-Path -LiteralPath $agentExe -PathType Leaf)) { throw 'JoltIQ Companion must be installed before initializing an acceptance case.' }

$bundle = New-JoltIqEvidenceBundle -Root $EvidenceRoot -CaseId $CaseId -RunId $RunId -CatalogPath $CatalogPath
$registryPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$harnessCommit = (& git -C $repoRoot rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or "$harnessCommit".Trim() -notmatch '^[0-9a-fA-F]{7,64}$') { throw 'Exact harness commit could not be read.' }
$agentVersion = (Get-Item -LiteralPath $agentExe).VersionInfo.ProductVersion
$signature = Get-AuthenticodeSignature -FilePath $resolvedMsi
$signer = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { 'none' }

[ordered]@{
    os_caption = $os.Caption
    display_version = (Get-ItemProperty $registryPath).DisplayVersion
    os_build = $os.BuildNumber
    architecture = '64-bit'
    agent_version = $agentVersion
    server_commit = $ServerCommit
    harness_commit = "$harnessCommit".Trim()
    fixture_version = '1.0.0'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundle 'environment.json') -Encoding utf8
[ordered]@{ name = $SnapshotName; clean = $true; reverted_at = (Get-Date).ToUniversalTime().ToString('o'); confirmed_by = $env:USERNAME } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundle 'snapshot.json') -Encoding utf8
[ordered]@{
    msi = (Split-Path $resolvedMsi -Leaf)
    sha256 = (Get-FileHash $resolvedMsi -Algorithm SHA256).Hash.ToLowerInvariant()
    signature_status = $signature.Status.ToString()
    signer = $signer
    installed = $true
    version = $agentVersion
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundle 'installer.json') -Encoding utf8

$remaining = @($catalog.required_evidence | Where-Object { -not (Test-Path -LiteralPath (Join-Path $bundle $_)) })
Write-Host "Initialized authorized computer-use case $CaseId at $bundle" -ForegroundColor Cyan
Write-Host 'Run only the named case on the consented disposable VM. Export redacted metadata and frames; never retain customer text, credentials, cookies, OTPs, or document contents.' -ForegroundColor Yellow
Write-Host 'Still required:'
$remaining | ForEach-Object { Write-Host "  $_" }
Write-Host "When evidence is present, run: .\Invoke-GeneralComputerUse.ps1 -CaseId '$CaseId' -RunId '$RunId' -EvidenceRoot '$EvidenceRoot' -Finalize"
exit 0
