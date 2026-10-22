Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not ('JoltIq.Build.NativeProcessRunner' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;

namespace JoltIq.Build
{
    public sealed class NativeProcessResult
    {
        public int ExitCode { get; set; }
        public string[] Output { get; set; }
    }

    public static class NativeProcessRunner
    {
        public static NativeProcessResult Run(string filePath, string argumentLine)
        {
            var stdout = new ConcurrentQueue<string>();
            var stderr = new ConcurrentQueue<string>();
            var stdoutClosed = new ManualResetEventSlim(false);
            var stderrClosed = new ManualResetEventSlim(false);
            var process = new Process();
            {
                process.StartInfo = new ProcessStartInfo
                {
                    FileName = filePath,
                    Arguments = argumentLine,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardInput = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs args)
                {
                    if (args.Data == null) stdoutClosed.Set();
                    else stdout.Enqueue(args.Data);
                };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args)
                {
                    if (args.Data == null) stderrClosed.Set();
                    else stderr.Enqueue(args.Data);
                };
                if (!process.Start()) throw new InvalidOperationException("Native build command runner did not start.");
                process.StandardInput.Close();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                // The parameterless overload also waits for asynchronous stream
                // readers, which never finish if a descendant inherited a pipe.
                // Poll the process handle with the timed overload instead.
                while (!process.WaitForExit(1000)) { }
                var stdoutFinished = stdoutClosed.Wait(10000);
                var stderrFinished = stderrClosed.Wait(10000);

                var output = new List<string>();
                output.AddRange(stdout.ToArray());
                output.AddRange(stderr.ToArray());
                var result = new NativeProcessResult { ExitCode = process.ExitCode, Output = output.ToArray() };

                // Cancelling an asynchronous read and Process.Dispose can both wait
                // forever when a descendant inherited the pipe handle after exit.
                // Dispose only after both readers reported EOF. Otherwise allow the
                // short-lived build host to release the orphaned reader naturally.
                if (stdoutFinished && stderrFinished)
                {
                    process.Dispose();
                    stdoutClosed.Dispose();
                    stderrClosed.Dispose();
                }
                return result;
            }
        }
    }
}
'@
}

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

function ConvertTo-JoltIqWindowsArgument {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $quoted = New-Object Text.StringBuilder
    [void]$quoted.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes++
            continue
        }
        if ($character -eq '"') {
            if ($slashes -gt 0) { [void]$quoted.Append(('\' * ($slashes * 2))) }
            [void]$quoted.Append('\"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) { [void]$quoted.Append(('\' * $slashes)) }
        [void]$quoted.Append($character)
        $slashes = 0
    }
    # Backslashes immediately before the closing quote must be doubled so the
    # Windows command-line parser does not consume that quote.
    if ($slashes -gt 0) { [void]$quoted.Append(('\' * ($slashes * 2))) }
    [void]$quoted.Append('"')
    return $quoted.ToString()
}

function Invoke-JoltIqNativeCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    foreach ($value in @($FilePath) + @($Arguments)) {
        if ([string]$value -match "`0") { throw 'Native build command values cannot contain NUL.' }
    }
    # Do not let Windows PowerShell own native stdout/stderr. Build tools can leave
    # inherited pipeline handles open, and PowerShell 5.1 can promote redirected
    # native stderr to a terminating error. Launch an isolated runner through the
    # .NET process API and drain both streams asynchronously. Construct argv with
    # the documented Windows quoting rules so PowerShell and cmd.exe never parse
    # build values and trailing backslashes remain exact.
    $argumentLine = (@($Arguments | ForEach-Object { ConvertTo-JoltIqWindowsArgument -Value ([string]$_) }) -join ' ')
    # Keep the asynchronous callbacks entirely inside managed code. PowerShell
    # scriptblock callbacks execute on threads without a runspace under Windows
    # PowerShell 5.1, which can silently discard output and change the result.
    [JoltIq.Build.NativeProcessRunner]::Run($FilePath, $argumentLine)
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

function Assert-JoltIqComputerUsePackage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$PublishDirectory,
        [Parameter(Mandatory)][string]$ManifestPath
    )
    foreach ($relative in @('JoltIQCompanion.exe', 'JoltIQ.Agent.Core.dll', 'JoltIQ.Agent.Windows.dll')) {
        Assert-JoltIqFile -Path (Join-Path $PublishDirectory $relative) -Purpose "native computer-use package file $relative"
    }
    Assert-JoltIqFile -Path $ManifestPath -Purpose 'computer-use capability manifest'
    try { $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json }
    catch { throw "Computer-use capability manifest is invalid JSON: $($_.Exception.Message)" }
    if ($manifest.schema_version -ne 1 -or $manifest.native_driver -ne $true) { throw 'Published tray does not declare the native Windows computer-use driver.' }
    foreach ($mode in @('guide', 'fix')) {
        if (@($manifest.modes) -notcontains $mode) { throw "Published tray is missing computer-use mode: $mode" }
    }
    $required = @('screen.capture', 'input.click', 'input.drag', 'input.move', 'input.mouse', 'input.type', 'input.key', 'input.scroll', 'input.wait', 'input.cursor', 'guide.highlight', 'guide.clear', 'url.open')
    foreach ($capability in $required) {
        if (@($manifest.capabilities) -notcontains $capability) { throw "Published tray is missing computer-use capability: $capability" }
    }
    if ($manifest.guide_input_authority -ne $false) { throw 'Guide mode package must declare zero native input authority.' }
    $expectedGuide = @('screen.capture', 'input.wait', 'input.cursor', 'guide.highlight', 'guide.clear')
    if (@($manifest.guide_capabilities).Count -ne $expectedGuide.Count) { throw 'Guide capability declaration is incomplete.' }
    foreach ($capability in $expectedGuide) {
        if (@($manifest.guide_capabilities) -notcontains $capability) { throw "Guide mode package is missing capability: $capability" }
    }
    if (@($manifest.guide_capabilities) -contains 'url.open' -or @($manifest.guide_capabilities | Where-Object { $_ -like 'input.*' -and $_ -notin @('input.wait', 'input.cursor') }).Count -gt 0) {
        throw 'Guide mode package declares active input or URL-launch authority.'
    }
    if (@($manifest.screenshot_upload_order).Count -ne 2 -or $manifest.screenshot_upload_order[0] -ne 'screenshot' -or $manifest.screenshot_upload_order[1] -ne 'result') {
        throw 'Published tray must declare screenshot-before-result evidence ordering.'
    }
}

Export-ModuleMember -Function Assert-JoltIqBuildRequest, Assert-JoltIqPackageVersion, Resolve-JoltIqOutputDirectory, Assert-JoltIqCommandSucceeded, Invoke-JoltIqNativeCommand, Assert-JoltIqCleanBuildOutput, Assert-JoltIqFile, Write-JoltIqChecksum, Assert-JoltIqChecksum, Assert-JoltIqSignatureEvidence, Assert-JoltIqAuthenticodeFile, Assert-JoltIqInstallerClassification, Assert-JoltIqComputerUsePackage
