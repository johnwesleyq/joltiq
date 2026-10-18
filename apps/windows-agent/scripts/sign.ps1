<#
.SYNOPSIS Authenticode-sign files with a certificate from CurrentUser\My (or an HSM-backed cert via its thumbprint).
          Requires signtool.exe (Windows SDK). Only run with an AUTHORIZED JoltIQ code-signing certificate.
#>
param([Parameter(Mandatory)][string[]]$Paths, [Parameter(Mandatory)][string]$CertThumbprint, [string]$TimestampUrl = "http://timestamp.digicert.com")
$ErrorActionPreference = "Stop"
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) { throw "signtool.exe not found; install the Windows 10/11 SDK" }
foreach ($p in $Paths) {
  & $signtool.FullName sign /sha1 $CertThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 /d "JoltIQ Companion" $p
  if ($LASTEXITCODE) { throw "signing failed: $p" }
  & $signtool.FullName verify /pa /v $p | Out-Null
  if ($LASTEXITCODE) { throw "signature verification failed: $p" }
  Write-Host "signed $p"
}
