Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot '..\BuildGate.psm1') -Force
$windowsPowerShellEvidenceModule = Join-Path $PSScriptRoot '..\acceptance\EvidenceBundle.psm1'
$windowsPowerShellAcceptanceCatalog = Join-Path $PSScriptRoot '..\acceptance\cases.json'

$passed = 0
$failed = 0
function Test([string]$Name, [scriptblock]$Body) {
    try { & $Body; $script:passed++; Write-Host "PASS $Name" }
    catch { $script:failed++; Write-Error "FAIL $Name`: $_" -ErrorAction Continue }
}
function Throws([scriptblock]$Body) {
    try { & $Body; throw 'expected the operation to throw' }
    catch {
        if ($_.Exception.Message -eq 'expected the operation to throw') { throw }
    }
}

Test 'internal unsigned build is allowed but externally labelled output is refused' {
    Assert-JoltIqBuildRequest -Distribution Internal -Sign:$false -CertThumbprint '' -TimestampUrl 'https://timestamp.example.test'
    Throws { Assert-JoltIqBuildRequest -Distribution External -Sign:$false -CertThumbprint '' -TimestampUrl 'https://timestamp.example.test' }
}
Test 'signed request requires a certificate and HTTPS timestamping' {
    Throws { Assert-JoltIqBuildRequest -Distribution External -Sign:$true -CertThumbprint '' -TimestampUrl 'https://timestamp.example.test' }
    Throws { Assert-JoltIqBuildRequest -Distribution External -Sign:$true -CertThumbprint 'ABC123' -TimestampUrl 'http://timestamp.example.test' }
    Throws { Assert-JoltIqBuildRequest -Distribution External -Sign:$true -CertThumbprint 'ABC123' -TimestampUrl 'https://timestamp.example.test' }
    Assert-JoltIqBuildRequest -Distribution External -Sign:$true -CertThumbprint ('A' * 40) -TimestampUrl 'https://timestamp.example.test'
}
Test 'package version is a strict three-part numeric version' {
    Assert-JoltIqPackageVersion -Version '1.2.3'
    Throws { Assert-JoltIqPackageVersion -Version '1.2.3-preview' }
    Throws { Assert-JoltIqPackageVersion -Version "1.2.3'; Write-Host unsafe" }
}
Test 'artifact cleanup stays inside a narrowly named agent output directory' {
    $root = Join-Path ([IO.Path]::GetTempPath()) 'joltiq-agent-root'
    $resolved = Resolve-JoltIqOutputDirectory -Root $root -Requested (Join-Path $root 'artifacts-previous')
    if ($resolved -ne [IO.Path]::GetFullPath((Join-Path $root 'artifacts-previous'))) { throw 'safe artifact path was changed' }
    Throws { Resolve-JoltIqOutputDirectory -Root $root -Requested $root }
    Throws { Resolve-JoltIqOutputDirectory -Root $root -Requested (Join-Path (Split-Path $root -Parent) 'artifacts') }
    Throws { Resolve-JoltIqOutputDirectory -Root $root -Requested (Join-Path $root 'publish') }
}
Test 'compile warnings and failed commands fail the gate' {
    Throws { Assert-JoltIqCleanBuildOutput -Output 'warning CS0618: obsolete API' }
    Throws { Assert-JoltIqCommandSucceeded -ExitCode 1 -Operation 'dotnet test' }
    Assert-JoltIqCommandSucceeded -ExitCode 0 -Operation 'dotnet test'
}
Test 'native stderr is captured even when the caller stops on PowerShell errors' {
    $fixture = Join-Path ([IO.Path]::GetTempPath()) ('joltiq-native-stderr-' + [guid]::NewGuid().ToString('N') + '.cmd')
    try {
        "@echo off`r`necho stderr marker 1>&2`r`nexit /b 0" | Set-Content -LiteralPath $fixture -Encoding ascii
        $result = Invoke-JoltIqNativeCommand -FilePath $env:ComSpec -Arguments @('/d', '/c', $fixture)
        if ($result.ExitCode -ne 0) { throw "native command returned $($result.ExitCode)" }
        if ((@($result.Output) -join "`n") -notmatch 'stderr marker') { throw 'native stderr was not captured' }
    }
    finally { Remove-Item -LiteralPath $fixture -Force -ErrorAction SilentlyContinue }
}
Test 'missing package inputs and checksum mismatches fail the gate' {
    $temp = Join-Path ([IO.Path]::GetTempPath()) ('joltiq-build-gate-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $temp | Out-Null
        Throws { Assert-JoltIqFile -Path (Join-Path $temp 'missing.msi') -Purpose 'MSI' }
        $artifact = Join-Path $temp 'fixture.msi'
        'fixture' | Set-Content -LiteralPath $artifact
        $manifest = Join-Path $temp 'SHA256SUMS.txt'
        'bad-hash  fixture.msi' | Set-Content -LiteralPath $manifest
        Throws { Assert-JoltIqChecksum -Artifact $artifact -Manifest $manifest }
        Write-JoltIqChecksum -Artifact $artifact -Manifest $manifest
        Assert-JoltIqChecksum -Artifact $artifact -Manifest $manifest
    }
    finally { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}
Test 'external signature evidence must be valid and timestamped' {
    Throws { Assert-JoltIqSignatureEvidence -Status 'NotSigned' -HasTimestamp:$false -Path 'fixture.exe' }
    Throws { Assert-JoltIqSignatureEvidence -Status 'Valid' -HasTimestamp:$false -Path 'fixture.exe' }
    Assert-JoltIqSignatureEvidence -Status 'Valid' -HasTimestamp:$true -Path 'fixture.exe'
}
Test 'installer classification never presents unsigned output as external-ready' {
    Assert-JoltIqInstallerClassification -Distribution Internal -SignatureStatus 'NotSigned' -InternalMarkerPresent:$true -ArpComments 'Internal engineering build - not approved for customer deployment.'
    Throws { Assert-JoltIqInstallerClassification -Distribution Internal -SignatureStatus 'NotSigned' -InternalMarkerPresent:$false -ArpComments '' }
    Throws { Assert-JoltIqInstallerClassification -Distribution External -SignatureStatus 'NotSigned' -InternalMarkerPresent:$false -ArpComments 'External release' }
    Assert-JoltIqInstallerClassification -Distribution External -SignatureStatus 'Valid' -InternalMarkerPresent:$false -ArpComments 'Externally releasable build'
}
Test 'acceptance harness loads its catalog under Windows PowerShell 5.1' ({
    if ($PSVersionTable.PSVersion.Major -ne 5) { throw 'this compatibility gate must run under Windows PowerShell 5.1' }
    Import-Module $windowsPowerShellEvidenceModule -Force
    $catalog = Get-JoltIqCatalog $windowsPowerShellAcceptanceCatalog
    if (-not $catalog.cases) { throw 'acceptance catalog did not load any cases' }
    if (-not (Test-JoltIqEvidenceModule -CatalogPath $windowsPowerShellAcceptanceCatalog)) {
        throw 'acceptance evidence module self-test failed under Windows PowerShell 5.1'
    }
}.GetNewClosure())

Write-Host "$passed passed; $failed failed"
if ($failed -ne 0) { exit 1 }
