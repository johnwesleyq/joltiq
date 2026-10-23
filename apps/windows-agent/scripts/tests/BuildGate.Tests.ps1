Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot '..\BuildGate.psm1') -Force
$windowsPowerShellEvidenceModule = Join-Path $PSScriptRoot '..\acceptance\EvidenceBundle.psm1'
$windowsPowerShellAcceptanceCatalog = Join-Path $PSScriptRoot '..\acceptance\cases.json'
$windowsPowerShellComputerUseCatalog = Join-Path $PSScriptRoot '..\acceptance\computer-use-cases.json'

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
Test 'native command capture uses an isolated asynchronous process rather than a PowerShell pipeline' {
    $module = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\BuildGate.psm1') -Raw
    if ($module -notmatch 'Diagnostics' -or $module -notmatch 'BeginOutputReadLine\(\)') {
        throw 'native command capture must use the asynchronous .NET process API'
    }
    if ($module -match '\$output\s*=\s*@\(&\s+\$FilePath') {
        throw 'native command capture must not wait on an inherited PowerShell pipeline'
    }
    if ($module -match '&\s+\$FilePath\s+@Arguments\s+1>') {
        throw 'Windows PowerShell must not own native stdout or stderr redirection'
    }
    if ($module -match '(?m)^\s*(?:\$\w+\s*=\s*)?Start-Process\b') {
        throw 'native command capture must not rely on the Windows PowerShell 5.1 Start-Process exit-code object'
    }
    if ($module -match '\$env:ComSpec|\.cmd["'']') {
        throw 'native command capture must not route untrusted values through cmd.exe'
    }
    if ($module -match 'Task\]::WaitAll\(' -or $module -match 'ReadToEndAsync\(\)') {
        throw 'native command capture must never wait forever for inherited pipe handles'
    }
    if ($module -match 'process\.WaitForExit\(\);' -or $module -notmatch 'process\.WaitForExit\(1000\)') {
        throw 'native command capture must poll process exit without the asynchronous-reader wait in parameterless WaitForExit'
    }
    if ($module -notmatch 'stdoutClosed\.Wait\(10000\)' -or
        $module -notmatch 'stderrClosed\.Wait\(10000\)' -or
        $module -notmatch 'if \(stdoutFinished && stderrFinished\)') {
        throw 'native command capture must bound output draining and dispose only readers that reached EOF'
    }
    if ($module -match 'CancelOutputRead\(\)' -or $module -match 'CancelErrorRead\(\)') {
        throw 'native command capture must not block while cancelling a stale inherited pipe read'
    }
    if ($module -notmatch 'NativeProcessRunner' -or $module -match '\[Diagnostics\.DataReceivedEventHandler\]\s*\{') {
        throw 'native output callbacks must remain in managed code and never require a PowerShell runspace'
    }
}
Test 'native command capture preserves Unicode, metacharacters, quotes, and trailing slashes' {
    $temp = Join-Path ([IO.Path]::GetTempPath()) ('joltiq-argv-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $temp | Out-Null
        $fixture = Join-Path $temp 'capture argv.ps1'
        $resultPath = Join-Path $temp 'result.json'
        @(
            'param([string]$OutputPath, [Parameter(ValueFromRemainingArguments=$true)][string[]]$Values)',
            '$Values | ConvertTo-Json | Set-Content -LiteralPath $OutputPath -Encoding UTF8'
        ) | Set-Content -LiteralPath $fixture -Encoding UTF8
        $values = @('Unicode-測試', 'space value', 'C:\path with space\', 'amp&value', 'percent%TEMP%value', 'paren(value)', 'quote"value')
        $captured = Invoke-JoltIqNativeCommand -FilePath 'powershell.exe' -Arguments (@('-NoProfile', '-File', $fixture, $resultPath) + $values)
        if ($captured.ExitCode -ne 0) { throw "argv fixture failed: $($captured.Output -join [Environment]::NewLine)" }
        # Windows PowerShell 5.1 emits a top-level JSON array as one Object[]
        # pipeline object. Cast it explicitly so the comparison validates argv
        # values rather than comparing a nested System.Object[] wrapper.
        $actual = [string[]](Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json)
        if (($actual -join "`n") -ne ($values -join "`n")) { throw "argv changed across isolated runner: $($actual -join ' | ')" }
        $oddExecutable = Join-Path $temp 'where Unicode 測試.exe'
        Copy-Item -LiteralPath (Join-Path $env:SystemRoot 'System32\where.exe') -Destination $oddExecutable
        $oddPath = Invoke-JoltIqNativeCommand -FilePath $oddExecutable -Arguments @('powershell.exe')
        if ($oddPath.ExitCode -ne 0) { throw "Unicode/metacharacter executable path failed: $($oddPath.Output -join [Environment]::NewLine)" }
    }
    finally { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}
Test 'build disables reusable dotnet workers so captured output cannot hang' {
    $buildScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\build.ps1') -Raw
    $expectedSettings = @{
        MSBUILDDISABLENODEREUSE = '1'
        DOTNET_CLI_USE_MSBUILD_SERVER = '0'
    }
    foreach ($setting in $expectedSettings.GetEnumerator()) {
        $expectedLine = '$env:' + $setting.Key + " = '$($setting.Value)'"
        if ($buildScript -notmatch [regex]::Escape($expectedLine)) {
            throw "build.ps1 must set $($setting.Key) to $($setting.Value) before invoking dotnet"
        }
    }
    if ($buildScript -notmatch [regex]::Escape('-p:UseSharedCompilation=false')) {
        throw 'build.ps1 must disable the reusable compiler server for captured dotnet commands'
    }
    $shutdown = $buildScript.IndexOf('dotnet build-server shutdown')
    $firstCapturedBuild = $buildScript.IndexOf('Invoke-CheckedDotnet "build ')
    if ($shutdown -lt 0 -or $firstCapturedBuild -lt 0 -or $shutdown -gt $firstCapturedBuild) {
        throw 'build.ps1 must shut down stale dotnet build servers before the first captured build'
    }
}
Test 'installer lifecycle host detection supports Windows PowerShell 5.1' {
    $lifecycleScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\test-on-windows.ps1') -Raw
    if ($lifecycleScript -match 'if \(-not \$IsWindows\)') {
        throw 'installer lifecycle still reads the PowerShell Core-only variable directly'
    }
    if ($lifecycleScript -notmatch [regex]::Escape("`$env:OS -eq 'Windows_NT'")) {
        throw 'installer lifecycle lacks a Windows PowerShell 5.1 host fallback'
    }
}
Test 'installer lifecycle ignores uninstall registry entries without DisplayName under strict mode' {
    $lifecycleScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\test-on-windows.ps1') -Raw
    if ($lifecycleScript -match 'Where-Object DisplayName') {
        throw 'installer lifecycle still uses simplified property syntax that fails when a registry entry has no DisplayName'
    }
    if ($lifecycleScript -notmatch [regex]::Escape("`$_.PSObject.Properties.Match('DisplayName').Count -gt 0")) {
        throw 'installer lifecycle does not guard access to optional uninstall registry values'
    }
}
Test 'installer launch condition uses the real Windows build number' {
    $installerSource = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\..\installer\Product.wxs') -Raw
    if ($installerSource -match 'VersionNT64 &gt;= 1000') {
        throw 'installer still assumes Windows 10 reports VersionNT64 1000; Windows Installer reports 603 by design'
    }
    if ($installerSource -notmatch 'Name="CurrentBuildNumber"' -or $installerSource -notmatch 'WINDOWSBUILDNUMBER &gt;= 10240') {
        throw 'installer must detect Windows 10 or later using CurrentBuildNumber while separately requiring VersionNT64'
    }
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
Test 'general computer-use harness is complete under Windows PowerShell 5.1' ({
    if ($PSVersionTable.PSVersion.Major -ne 5) { throw 'this compatibility gate must run under Windows PowerShell 5.1' }
    Import-Module $windowsPowerShellEvidenceModule -Force
    $catalog = Get-JoltIqCatalog $windowsPowerShellComputerUseCatalog
    $required = @(
        'notepad-guide-win11', 'notepad-fix-win11', 'notepad-guide-win10', 'notepad-fix-win10',
        'multi-monitor-dpi', 'layout-change', 'screen-lock', 'uac-secure-desktop', 'password-target', 'otp-target',
        'focus-theft', 'enrolled-user-mismatch', 'local-decline', 'portal-decline', 'stop-queued',
        'stop-before-native-input', 'stop-during-drag', 'offline-command', 'expired-command', 'duplicate-command',
        'tray-restart', 'unsupported-secure-desktop', 'guide-input-refusal', 'switch-to-fix-fresh-consent'
    )
    foreach ($id in $required) {
        if ($catalog.cases.id -notcontains $id) { throw "computer-use catalog missing required case: $id" }
    }
    if (-not (Test-JoltIqComputerUseEvidenceModule -CatalogPath $windowsPowerShellComputerUseCatalog)) {
        throw 'general computer-use evidence module self-test failed under Windows PowerShell 5.1'
    }
}.GetNewClosure())
Test 'published tray advertises the native computer-use contract and fails closed' {
    $temp = Join-Path ([IO.Path]::GetTempPath()) ('joltiq-capabilities-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $temp | Out-Null
        'fixture' | Set-Content -LiteralPath (Join-Path $temp 'JoltIQCompanion.exe')
        'fixture' | Set-Content -LiteralPath (Join-Path $temp 'JoltIQ.Agent.Core.dll')
        'fixture' | Set-Content -LiteralPath (Join-Path $temp 'JoltIQ.Agent.Windows.dll')
        $manifest = Join-Path $temp 'computer-use-capabilities.json'
        @{ schema_version = 1; native_driver = $true; modes = @('guide', 'fix'); capabilities = @('screen.capture'); guide_capabilities = @('screen.capture'); guide_input_authority = $false; screenshot_upload_order = @('screenshot', 'result') } |
            ConvertTo-Json | Set-Content -LiteralPath $manifest
        Throws { Assert-JoltIqComputerUsePackage -PublishDirectory $temp -ManifestPath $manifest }
        @{ schema_version = 1; native_driver = $true; modes = @('guide', 'fix'); capabilities = @('screen.capture', 'input.click', 'input.drag', 'input.move', 'input.mouse', 'input.type', 'input.key', 'input.scroll', 'input.wait', 'input.cursor', 'guide.highlight', 'guide.clear', 'url.open'); guide_capabilities = @('screen.capture', 'input.wait', 'input.cursor', 'guide.highlight', 'guide.clear'); guide_input_authority = $false; screenshot_upload_order = @('screenshot', 'result') } |
            ConvertTo-Json | Set-Content -LiteralPath $manifest
        Assert-JoltIqComputerUsePackage -PublishDirectory $temp -ManifestPath $manifest
    }
    finally { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host "$passed passed; $failed failed"
if ($failed -ne 0) { exit 1 }
