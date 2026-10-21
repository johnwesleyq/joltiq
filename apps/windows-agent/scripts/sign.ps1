<#
.SYNOPSIS Authenticode-sign files with a certificate from CurrentUser\My (or an HSM-backed cert via its thumbprint).
          Requires signtool.exe (Windows SDK). Only run with an AUTHORIZED JoltIQ code-signing certificate.
#>
param([Parameter(Mandatory)][string[]]$Paths, [Parameter(Mandatory)][string]$CertThumbprint, [string]$TimestampUrl = "https://timestamp.digicert.com")
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
Import-Module "$PSScriptRoot\BuildGate.psm1" -Force
Assert-JoltIqBuildRequest -Distribution External -Sign:$true -CertThumbprint $CertThumbprint -TimestampUrl $TimestampUrl
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) { throw "signtool.exe not found; install the Windows 10/11 SDK" }
foreach ($p in $Paths) {
  Assert-JoltIqFile -Path $p -Purpose "signing input"
  & $signtool.FullName sign /sha1 $CertThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 /d "JoltIQ Companion" $p
  Assert-JoltIqCommandSucceeded -ExitCode $LASTEXITCODE -Operation "signing $p"
  & $signtool.FullName verify /pa /all /v $p | Out-Null
  Assert-JoltIqCommandSucceeded -ExitCode $LASTEXITCODE -Operation "signature verification $p"
  Assert-JoltIqAuthenticodeFile -Path $p -RequireTimestamp
  Write-Host "signed and timestamp-verified $p"
}
