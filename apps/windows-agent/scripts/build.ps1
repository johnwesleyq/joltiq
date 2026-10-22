<#
.SYNOPSIS  Build, test, publish and package the JoltIQ Windows companion. Run on Windows 10/11 with the .NET 8 SDK.
.EXAMPLE   .\scripts\build.ps1 -Version 1.0.0            # build + tests + MSI (unsigned)
.EXAMPLE   .\scripts\build.ps1 -Version 1.0.0 -Sign -CertThumbprint ABC123...   # signed binaries + MSI
#>
param(
  [string]$Version = "1.0.0",
  [ValidateSet("Internal", "External")][string]$Distribution = "Internal",
  [switch]$Sign,
  [string]$CertThumbprint = "",
  [string]$TimestampUrl = "https://timestamp.digicert.com",
  [switch]$SkipTests,
  [string]$OutputDirectory = ""
)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
# The native-command wrapper captures stdout/stderr. Reusable MSBuild/compiler
# workers can inherit those pipe handles and keep PowerShell waiting after the
# real build has exited, so packaging must use one-shot workers.
$env:MSBUILDDISABLENODEREUSE = '1'
$env:DOTNET_CLI_USE_MSBUILD_SERVER = '0'
$root = Split-Path -Parent $PSScriptRoot
Import-Module "$PSScriptRoot\BuildGate.psm1" -Force
Assert-JoltIqBuildRequest -Distribution $Distribution -Sign:$Sign -CertThumbprint $CertThumbprint -TimestampUrl $TimestampUrl
Assert-JoltIqPackageVersion -Version $Version
$requestedOutput = if ($OutputDirectory) { $OutputDirectory } else { Join-Path $root "artifacts" }
$out = Resolve-JoltIqOutputDirectory -Root $root -Requested $requestedOutput
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $out | Out-Null
$logs = Join-Path $out "logs"
New-Item -ItemType Directory $logs | Out-Null

function Invoke-CheckedDotnet([string]$Operation, [string[]]$Arguments, [string]$LogName) {
  $result = Invoke-JoltIqNativeCommand -FilePath "dotnet" -Arguments ($Arguments + @("-p:UseSharedCompilation=false"))
  $output = @($result.Output)
  $exitCode = $result.ExitCode
  $output | Tee-Object -FilePath (Join-Path $logs $LogName) | ForEach-Object { Write-Host $_ }
  Assert-JoltIqCleanBuildOutput -Output ($output -join [Environment]::NewLine)
  Assert-JoltIqCommandSucceeded -ExitCode $exitCode -Operation $Operation
}

function Invoke-CheckedPowerShell([string]$Operation, [string[]]$Arguments, [string]$LogName) {
  $result = Invoke-JoltIqNativeCommand -FilePath "powershell.exe" -Arguments (@("-NoProfile", "-ExecutionPolicy", "Bypass") + $Arguments)
  $output = @($result.Output)
  $output | Tee-Object -FilePath (Join-Path $logs $LogName) | ForEach-Object { Write-Host $_ }
  Assert-JoltIqCommandSucceeded -ExitCode $result.ExitCode -Operation $Operation
}

Write-Host "== Build-server preflight" -ForegroundColor Cyan
& dotnet build-server shutdown
Assert-JoltIqCommandSucceeded -ExitCode $LASTEXITCODE -Operation "dotnet build-server shutdown"

Write-Host "== Restore & build" -ForegroundColor Cyan
$projects = Get-ChildItem "$root\src","$root\tests" -Recurse -Filter *.csproj
foreach ($p in $projects) {
  Invoke-CheckedDotnet "build $($p.Name)" @("build", $p.FullName, "-c", "Release", "-warnaserror", "-p:Version=$Version", "--nologo") "build-$($p.BaseName).log"
}

if (-not $SkipTests) {
  Write-Host "== Tests" -ForegroundColor Cyan
  Invoke-CheckedDotnet "core self-tests" @("run", "--project", "$root\tests\JoltIQ.Agent.Core.SelfTest", "-c", "Release", "--no-build") "core-self-tests.log"
  Invoke-CheckedDotnet "Windows xUnit tests" @("test", "$root\tests\JoltIQ.Agent.Tests", "-c", "Release", "--no-build", "--results-directory", $out, "--logger", "trx;LogFileName=windows-tests.trx") "windows-tests.log"
}

Write-Host "== Acceptance harness contract" -ForegroundColor Cyan
Invoke-CheckedPowerShell "Outlook acceptance harness validation" @("-File", "$root\scripts\test-on-windows.ps1", "-ValidateHarness") "acceptance-outlook-harness.log"
Invoke-CheckedPowerShell "computer-use acceptance harness validation" @("-File", "$root\scripts\test-on-windows.ps1", "-ValidateComputerUseHarness") "acceptance-computer-use-harness.log"

Write-Host "== Publish (self-contained, win-x64)" -ForegroundColor Cyan
Invoke-CheckedDotnet "publish tray" @("publish", "$root\src\JoltIQ.Agent.Tray", "-c", "Release", "-r", "win-x64", "--self-contained", "true", "-warnaserror", "-p:Version=$Version", "-o", "$out\publish\tray", "--nologo") "publish-tray.log"
Invoke-CheckedDotnet "publish broker" @("publish", "$root\src\JoltIQ.Agent.Broker", "-c", "Release", "-r", "win-x64", "--self-contained", "true", "-warnaserror", "-p:Version=$Version", "-o", "$out\publish\broker", "--nologo") "publish-broker.log"
$tray = "$out\publish\tray\JoltIQCompanion.exe"
$broker = "$out\publish\broker\JoltIQBroker.exe"
Assert-JoltIqFile -Path $tray -Purpose "tray executable"
Assert-JoltIqFile -Path $broker -Purpose "broker executable"
$computerUseManifest = "$out\publish\tray\computer-use-capabilities.json"
Assert-JoltIqComputerUsePackage -PublishDirectory "$out\publish\tray" -ManifestPath $computerUseManifest

if ($Sign) {
  & "$PSScriptRoot\sign.ps1" -Paths @($tray, $broker) -CertThumbprint $CertThumbprint -TimestampUrl $TimestampUrl
  Assert-JoltIqAuthenticodeFile -Path $tray -RequireTimestamp
  Assert-JoltIqAuthenticodeFile -Path $broker -RequireTimestamp
} else {
  "INTERNAL ENGINEERING BUILD ONLY`r`nUnsigned artifacts must not be distributed to customers." | Set-Content -LiteralPath "$out\INTERNAL-ONLY.txt" -Encoding ascii
  Write-Warning "Binaries are NOT signed. Output is labelled internal-only and must not be distributed to customers."
}

Write-Host "== MSI (WiX v5)" -ForegroundColor Cyan
Invoke-CheckedDotnet "build MSI" @("build", "$root\installer\JoltIQ.Installer.wixproj", "-c", "Release", "-warnaserror", "-p:ProductVersion=$Version", "-p:PublishDir=$out\publish\", "-p:Distribution=$Distribution", "-o", $out, "--nologo") "build-msi.log"
$msis = @(Get-ChildItem $out -Filter *.msi)
if ($msis.Count -ne 1) { throw "Expected exactly one MSI in $out; found $($msis.Count)." }
$msi = $msis[0]
Assert-JoltIqFile -Path $msi.FullName -Purpose "MSI"
if ($Sign) {
  & "$PSScriptRoot\sign.ps1" -Paths @($msi.FullName) -CertThumbprint $CertThumbprint -TimestampUrl $TimestampUrl
  Assert-JoltIqAuthenticodeFile -Path $msi.FullName -RequireTimestamp
}

$manifest = "$out\SHA256SUMS.txt"
Write-JoltIqChecksum -Artifact $msi.FullName -Manifest $manifest
Assert-JoltIqChecksum -Artifact $msi.FullName -Manifest $manifest
$hash = (Get-FileHash $msi.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
[ordered]@{ distribution = $Distribution; signed = [bool]$Sign; timestamped = [bool]$Sign; version = $Version; msi = $msi.Name; sha256 = $hash; native_computer_use = $true; capabilities_manifest_sha256 = (Get-FileHash $computerUseManifest -Algorithm SHA256).Hash.ToLowerInvariant() } |
  ConvertTo-Json | Set-Content -LiteralPath "$out\build-manifest.json" -Encoding utf8
Write-Host "Built $($msi.FullName)`nClassification $Distribution`nSHA256 $hash" -ForegroundColor Green
