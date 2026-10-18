<#
.SYNOPSIS Windows 11 acceptance test for the companion against a running JoltIQ API. Run on an AUTHORIZED test machine.
.DESCRIPTION
  1. Installs the MSI silently, verifies files, broker service, and Start menu entry.
  2. Asks you to enroll via the tray (code from the portal) and run a desktop session from the portal.
  3. Verifies repair and uninstall leave no service/files behind.
  Results are written to artifacts\windows-acceptance.json to paste into PROJECT_STATUS.md.
#>
param([Parameter(Mandatory)][string]$Msi)
$ErrorActionPreference = "Stop"
$results = [ordered]@{ os = (Get-CimInstance Win32_OperatingSystem).Caption; build = [Environment]::OSVersion.Version.ToString(); started = (Get-Date).ToString("o") }
function Check($name, [scriptblock]$b) { try { $ok = & $b; $results[$name] = [bool]$ok } catch { $results[$name] = "error: $_" } ; Write-Host "$name -> $($results[$name])" }

Start-Process msiexec -ArgumentList "/i `"$Msi`" /qn /l*v install.log" -Wait
Check "install.files" { Test-Path "$env:ProgramFiles\JoltIQ Companion\JoltIQCompanion.exe" }
Check "install.broker_running" { (Get-Service JoltIQBroker).Status -eq "Running" }
Check "install.signature" { (Get-AuthenticodeSignature "$env:ProgramFiles\JoltIQ Companion\JoltIQCompanion.exe").Status -eq "Valid" }

Read-Host "Launch 'JoltIQ Companion' from the Start menu, enroll with a portal code, then press Enter"
Check "enrolled.state_encrypted" { Test-Path "$env:LOCALAPPDATA\JoltIQ\Agent\device.key.dpapi" }
Read-Host "From the portal start a desktop session for this device, ACCEPT the consent dialog, approve the plan, wait for VERIFIED, press STOP on the banner. Then press Enter"
$results["manual.session_verified"] = (Read-Host "Did the portal show VERIFIED? (y/n)") -eq "y"
$results["manual.stop_banner_worked"] = (Read-Host "Did STOP end the session immediately? (y/n)") -eq "y"

Start-Process msiexec -ArgumentList "/fa `"$Msi`" /qn" -Wait
Check "repair.ok" { (Get-Service JoltIQBroker).Status -eq "Running" }
Start-Process msiexec -ArgumentList "/x `"$Msi`" /qn" -Wait
Check "uninstall.files_removed" { -not (Test-Path "$env:ProgramFiles\JoltIQ Companion\JoltIQCompanion.exe") }
Check "uninstall.service_removed" { -not (Get-Service JoltIQBroker -ErrorAction SilentlyContinue) }

New-Item -ItemType Directory -Force artifacts | Out-Null
$results | ConvertTo-Json | Out-File artifacts\windows-acceptance.json
Write-Host "Saved artifacts\windows-acceptance.json"
