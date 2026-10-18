<#
.SYNOPSIS  Build, test, publish and package the JoltIQ Windows companion. Run on Windows 10/11 with the .NET 8 SDK.
.EXAMPLE   .\scripts\build.ps1 -Version 1.0.0            # build + tests + MSI (unsigned)
.EXAMPLE   .\scripts\build.ps1 -Version 1.0.0 -Sign -CertThumbprint ABC123...   # signed binaries + MSI
#>
param(
  [string]$Version = "1.0.0",
  [switch]$Sign,
  [string]$CertThumbprint = "",
  [string]$TimestampUrl = "http://timestamp.digicert.com",
  [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "artifacts"
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $out | Out-Null

Write-Host "== Restore & build" -ForegroundColor Cyan
$projects = Get-ChildItem "$root\src","$root\tests" -Recurse -Filter *.csproj
foreach ($p in $projects) {
  dotnet build $p.FullName -c Release -p:Version=$Version
  if ($LASTEXITCODE) { throw "build failed: $($p.Name)" }
}

if (-not $SkipTests) {
  Write-Host "== Tests" -ForegroundColor Cyan
  dotnet run --project "$root\tests\JoltIQ.Agent.Core.SelfTest" -c Release
  if ($LASTEXITCODE) { throw "core self-tests failed" }
  dotnet test "$root\tests\JoltIQ.Agent.Tests" -c Release --logger "trx;LogFileName=$out\windows-tests.trx"
  if ($LASTEXITCODE) { throw "windows tests failed" }
}

Write-Host "== Publish (self-contained, win-x64)" -ForegroundColor Cyan
dotnet publish "$root\src\JoltIQ.Agent.Tray" -c Release -r win-x64 --self-contained true -p:Version=$Version -o "$out\publish\tray"
dotnet publish "$root\src\JoltIQ.Agent.Broker" -c Release -r win-x64 --self-contained true -p:Version=$Version -o "$out\publish\broker"

if ($Sign) {
  if (-not $CertThumbprint) { throw "-Sign requires -CertThumbprint (an Authenticode code-signing certificate in CurrentUser\My)" }
  & "$PSScriptRoot\sign.ps1" -Paths @("$out\publish\tray\JoltIQCompanion.exe", "$out\publish\broker\JoltIQBroker.exe") -CertThumbprint $CertThumbprint -TimestampUrl $TimestampUrl
} else {
  Write-Warning "Binaries are NOT signed. Do not distribute unsigned builds to customers."
}

Write-Host "== MSI (WiX v5)" -ForegroundColor Cyan
dotnet build "$root\installer\JoltIQ.Installer.wixproj" -c Release -p:ProductVersion=$Version -p:PublishDir="$out\publish\" -o $out
if ($LASTEXITCODE) { throw "msi build failed" }
$msi = Get-ChildItem $out -Filter *.msi | Select-Object -First 1
if ($Sign) { & "$PSScriptRoot\sign.ps1" -Paths @($msi.FullName) -CertThumbprint $CertThumbprint -TimestampUrl $TimestampUrl }

$hash = (Get-FileHash $msi.FullName -Algorithm SHA256).Hash.ToLower()
"$hash  $($msi.Name)" | Out-File "$out\SHA256SUMS.txt" -Encoding ascii
Write-Host "Built $($msi.FullName)`nSHA256 $hash" -ForegroundColor Green
