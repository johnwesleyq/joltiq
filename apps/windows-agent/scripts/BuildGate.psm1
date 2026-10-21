Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-JoltIqBuildRequest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][ValidateSet('Internal', 'External')][string]$Distribution,
        [Parameter(Mandatory)][bool]$Sign,
        [AllowEmptyString()][string]$CertThumbprint,
        [Parameter(Mandatory)][string]$TimestampUrl
    )
    if ($Distribution -eq 'External' -and -not $Sign) { throw 'External packages must be signed; unsigned output is internal-test-only.' }
    if ($Sign -and [string]::IsNullOrWhiteSpace($CertThumbprint)) { throw 'Signing requires a certificate thumbprint.' }
    if ($Sign -and $CertThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'Signing certificate thumbprint must be exactly 40 hexadecimal characters.' }
    if ($Sign -and -not $TimestampUrl.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) { throw 'Signing requires an HTTPS RFC 3161 timestamp URL.' }
}

function Assert-JoltIqPackageVersion {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Version)
    if ($Version -notmatch '^(\d{1,3})\.(\d{1,3})\.(\d{1,5})$') { throw 'Version must contain exactly three numeric components.' }
    if ([int]$Matches[1] -gt 255 -or [int]$Matches[2] -gt 255 -or [int]$Matches[3] -gt 65535) { throw 'Version is outside Windows Installer numeric limits.' }
}

function Resolve-JoltIqOutputDirectory {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string]$Requested)
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $outputPath = [IO.Path]::GetFullPath($Requested).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $parent = [IO.Path]::GetDirectoryName($outputPath)
    $leaf = [IO.Path]::GetFileName($outputPath)
    if (-not $parent.Equals($rootPath, [StringComparison]::OrdinalIgnoreCase) -or $leaf -notmatch '^artifacts(?:-[a-zA-Z0-9][a-zA-Z0-9._-]*)?$') {
        throw 'OutputDirectory must be an immediate child of the Windows agent root named artifacts or artifacts-<suffix>.'
    }
    return $outputPath
}

function Assert-JoltIqCommandSucceeded {
    [CmdletBinding()]
    param([Parameter(Mandatory)][int]$ExitCode, [Parameter(Mandatory)][string]$Operation)
    if ($ExitCode -ne 0) { throw "$Operation failed with exit code $ExitCode." }
}

function Assert-JoltIqCleanBuildOutput {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Output)
    if ($Output -match '(?im)\bwarning\s+(?:[A-Z]{2,}\d+|:)') { throw 'Build output contains one or more warnings.' }
}

function Assert-JoltIqFile {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Purpose)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Purpose is missing: $Path" }
    if ((Get-Item -LiteralPath $Path).Length -le 0) { throw "$Purpose is empty: $Path" }
}

function Write-JoltIqChecksum {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Artifact, [Parameter(Mandatory)][string]$Manifest)
    Assert-JoltIqFile -Path $Artifact -Purpose 'artifact'
    $hash = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $(Split-Path $Artifact -Leaf)" | Set-Content -LiteralPath $Manifest -Encoding ascii
    Assert-JoltIqFile -Path $Manifest -Purpose 'checksum manifest'
}

function Assert-JoltIqChecksum {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Artifact, [Parameter(Mandatory)][string]$Manifest)
    Assert-JoltIqFile -Path $Artifact -Purpose 'artifact'
    Assert-JoltIqFile -Path $Manifest -Purpose 'checksum manifest'
    $name = Split-Path $Artifact -Leaf
    $line = Get-Content -LiteralPath $Manifest | Where-Object { $_ -match "\s+$([regex]::Escape($name))$" } | Select-Object -First 1
    if (-not $line -or $line -notmatch '^([0-9a-fA-F]{64})\s{2,}(.+)$') { throw "Checksum manifest has no valid entry for $name." }
    $actual = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash
    if ($Matches[1] -ne $actual) { throw "Checksum mismatch for $name." }
}

function Assert-JoltIqSignatureEvidence {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Status,
        [Parameter(Mandatory)][bool]$HasTimestamp,
        [Parameter(Mandatory)][string]$Path
    )
    if ($Status -ne 'Valid') { throw "Authenticode signature is not valid for $Path (status: $Status)." }
    if (-not $HasTimestamp) { throw "Authenticode signature has no trusted timestamp for $Path." }
}

function Assert-JoltIqAuthenticodeFile {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path, [switch]$RequireTimestamp)
    Assert-JoltIqFile -Path $Path -Purpose 'signed artifact'
    $signature = Get-AuthenticodeSignature -FilePath $Path
    Assert-JoltIqSignatureEvidence -Status $signature.Status.ToString() -HasTimestamp:($null -ne $signature.TimeStamperCertificate) -Path $Path
    if (-not $RequireTimestamp -and $null -eq $signature.TimeStamperCertificate) {
        throw "Authenticode signature has no trusted timestamp for $Path."
    }
}

function Assert-JoltIqInstallerClassification {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][ValidateSet('Internal', 'External')][string]$Distribution,
        [Parameter(Mandatory)][string]$SignatureStatus,
        [Parameter(Mandatory)][bool]$InternalMarkerPresent,
        [AllowEmptyString()][string]$ArpComments
    )
    if ($Distribution -eq 'Internal') {
        if (-not $InternalMarkerPresent -or $ArpComments -notmatch '(?i)internal.*(not approved|not.*customer)') {
            throw 'Internal installer is not visibly labelled as unsuitable for customer deployment.'
        }
        return
    }
    if ($SignatureStatus -ne 'Valid') { throw 'External installer is not Authenticode-valid.' }
    if ($ArpComments -notmatch '(?i)external') { throw 'External installer classification is not visible in installed-product metadata.' }
}

Export-ModuleMember -Function Assert-JoltIqBuildRequest, Assert-JoltIqPackageVersion, Resolve-JoltIqOutputDirectory, Assert-JoltIqCommandSucceeded, Assert-JoltIqCleanBuildOutput, Assert-JoltIqFile, Write-JoltIqChecksum, Assert-JoltIqChecksum, Assert-JoltIqSignatureEvidence, Assert-JoltIqAuthenticodeFile, Assert-JoltIqInstallerClassification
