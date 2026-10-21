Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:SecretPattern = '(?i)(authorization\s*:\s*bearer\s+(?!\[REDACTED\])|(?:password|client[_-]?secret|access[_-]?token|refresh[_-]?token)\s*[=:]\s*(?!\[REDACTED\])|set-cookie\s*:\s*(?!\[REDACTED\])|begin (rsa |ec )?private key)'
$script:ForbiddenContentPattern = '(?i)"(email_body|document_content|credential|cookie|private_key|access_token|refresh_token|password)"\s*:'

function Protect-JoltIqEvidenceText {
    [CmdletBinding()]
    param([Parameter(Mandatory, ValueFromPipeline)][string]$Text)
    process {
        $redacted = [regex]::Replace($Text, '(?i)(authorization\s*:\s*bearer\s+)\S+', '$1[REDACTED]')
        $redacted = [regex]::Replace($redacted, '(?i)(set-cookie\s*:\s*)[^\r\n]+', '$1[REDACTED]')
        $redacted = [regex]::Replace($redacted, '(?i)("?(password|client[_-]?secret|access[_-]?token|refresh[_-]?token)"?\s*[=:]\s*"?)[^"\s,}]+', '$1[REDACTED]')
        $redacted = [regex]::Replace($redacted, '(?is)-----BEGIN (RSA |EC )?PRIVATE KEY-----.*?-----END (RSA |EC )?PRIVATE KEY-----', '[REDACTED PRIVATE KEY]')
        return $redacted
    }
}

function Protect-JoltIqEvidenceFile {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination)
    if ([IO.Path]::GetExtension($Source) -notin @('.json', '.jsonl', '.txt')) { throw 'Only JSON, JSONL, and text evidence can be sanitized.' }
    $parent = Split-Path $Destination -Parent
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Protect-JoltIqEvidenceText (Get-Content -LiteralPath $Source -Raw) | Set-Content -LiteralPath $Destination -Encoding utf8
}

function Get-JoltIqCatalog {
    param([Parameter(Mandatory)][string]$CatalogPath)
    if (-not (Test-Path -LiteralPath $CatalogPath -PathType Leaf)) { throw "Acceptance catalog not found: $CatalogPath" }
    Get-Content -LiteralPath $CatalogPath -Raw | ConvertFrom-Json -Depth 30
}

function New-JoltIqEvidenceBundle {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$CaseId,
        [Parameter(Mandatory)][string]$RunId,
        [Parameter(Mandatory)][string]$CatalogPath
    )
    $catalog = Get-JoltIqCatalog $CatalogPath
    if (-not ($catalog.cases.id -contains $CaseId)) { throw "Unknown acceptance case: $CaseId" }
    if ($RunId -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$') { throw 'RunId must contain only letters, numbers, dot, underscore, or hyphen.' }
    $bundle = Join-Path (Join-Path $Root $CaseId) $RunId
    if (Test-Path -LiteralPath $bundle) { throw "Evidence bundle already exists: $bundle" }
    New-Item -ItemType Directory -Path (Join-Path $bundle 'screenshots') -Force | Out-Null
    return $bundle
}

function Test-JoltIqEvidenceBundle {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Bundle,
        [Parameter(Mandatory)][string]$CatalogPath,
        [switch]$ThrowOnFailure
    )
    $catalog = Get-JoltIqCatalog $CatalogPath
    $errors = [System.Collections.Generic.List[string]]::new()
    foreach ($relative in $catalog.required_evidence) {
        if (-not (Test-Path -LiteralPath (Join-Path $Bundle $relative) -PathType Leaf)) { $errors.Add("missing evidence: $relative") }
    }
    $caseId = Split-Path (Split-Path $Bundle -Parent) -Leaf
    $case = $catalog.cases | Where-Object id -eq $caseId | Select-Object -First 1
    if (-not $case) { $errors.Add("bundle case is not in catalog: $caseId") }

    $files = @(Get-ChildItem -LiteralPath $Bundle -File -Recurse -ErrorAction SilentlyContinue)
    foreach ($file in $files) {
        if ($file.Extension -notin @('.json', '.jsonl', '.txt')) { continue }
        $raw = Get-Content -LiteralPath $file.FullName -Raw
        if ($raw -match $script:SecretPattern) { $errors.Add("secret-like content in $($file.Name)") }
        if ($raw -match $script:ForbiddenContentPattern) { $errors.Add("forbidden customer content in $($file.Name)") }
    }

    function ReadJson([string]$relative) {
        $path = Join-Path $Bundle $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
        try { return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -Depth 50 }
        catch { $errors.Add("invalid JSON: $relative"); return $null }
    }
    function GetProp($object, [string]$name) {
        if ($null -ne $object -and $object.PSObject.Properties.Match($name).Count -gt 0) { return $object.$name }
        return $null
    }

    $environment = ReadJson 'environment.json'
    foreach ($name in @('os_caption', 'display_version', 'os_build', 'architecture', 'agent_version', 'server_commit', 'harness_commit', 'fixture_version')) {
        if ($environment -and -not (GetProp $environment $name)) { $errors.Add("environment.json missing $name") }
    }
    if ($environment -and (GetProp $environment 'architecture') -ne '64-bit') { $errors.Add('only 64-bit Windows evidence is accepted') }
    if ($environment -and ((GetProp $environment 'server_commit') -notmatch '^[0-9a-fA-F]{7,64}$' -or (GetProp $environment 'harness_commit') -notmatch '^[0-9a-fA-F]{7,64}$')) {
        $errors.Add('environment.json requires exact hexadecimal server and harness commits')
    }
    if ($environment -and ((GetProp $environment 'os_build') -notmatch '^\d+$' -or (GetProp $environment 'agent_version') -in @('unknown', 'not-installed') -or (GetProp $environment 'fixture_version') -notmatch '^\d+\.\d+\.\d+')) {
        $errors.Add('environment.json requires exact OS, agent, and fixture versions')
    }
    if ($case -and $case.mode -eq 'licensed_outlook') {
        if (-not (GetProp $environment 'outlook_version') -or (GetProp $environment 'outlook_license_confirmed') -ne $true -or (GetProp $environment 'disposable_profile') -ne $true) {
            $errors.Add('licensed Outlook case requires exact version, confirmed license, and disposable profile')
        }
    }

    $snapshot = ReadJson 'snapshot.json'
    if ($snapshot -and (-not (GetProp $snapshot 'name') -or (GetProp $snapshot 'clean') -ne $true -or -not (GetProp $snapshot 'reverted_at'))) {
        $errors.Add('snapshot preflight must name a freshly reverted clean snapshot')
    }
    $installer = ReadJson 'installer.json'
    if ($installer -and ((GetProp $installer 'installed') -ne $true -or -not (GetProp $installer 'version') -or -not (GetProp $installer 'signature_status'))) {
        $errors.Add('installer evidence must contain install state, version, and signature status')
    }
    $procedure = ReadJson 'procedure.json'
    if ($procedure -and (-not (GetProp $procedure 'definition_hash') -or (GetProp $procedure 'hash_matches') -ne $true -or (GetProp $procedure 'publisher_mfa') -ne $true -or (GetProp $procedure 'distinct_approver') -ne $true)) {
        $errors.Add('procedure hash and two-admin MFA publication evidence must agree')
    }
    $workflow = ReadJson 'workflow.json'
    foreach ($name in @('ticket_id', 'run_id', 'session_id', 'ticket_state', 'run_state', 'session_state')) {
        if ($workflow -and -not (GetProp $workflow $name)) { $errors.Add("workflow.json missing $name") }
    }
    $commands = ReadJson 'commands.json'
    $commandItems = GetProp $commands 'commands'
    if ($commands -and ((GetProp $commands 'signature_checks_passed') -ne $true -or -not $commandItems -or @($commandItems | Where-Object { -not (GetProp $_ 'command_id') -or -not (GetProp $_ 'status') }).Count -gt 0)) {
        $errors.Add('command evidence requires signed command IDs and statuses')
    }
    $audit = ReadJson 'audit.json'
    if ($audit -and ((GetProp $audit 'chain_valid') -ne $true -or -not (GetProp $audit 'entries'))) { $errors.Add('audit chain evidence is missing or invalid') }
    $registry = ReadJson 'registry.json'
    if ($registry -and (-not (GetProp $registry 'before') -or -not (GetProp $registry 'after') -or -not (GetProp $registry 'readback'))) { $errors.Add('registry pre-state, post-state, and read-back are required') }
    $events = ReadJson 'events.json'
    if ($events -and (-not (GetProp $events 'observation') -or (GetProp $events 'raw_event_payload_collected') -ne $false)) {
        $errors.Add('events evidence must be a redacted observation and explicitly exclude raw event payloads')
    }
    $screenshots = ReadJson 'screenshots/index.json'
    $screenshotItems = GetProp $screenshots 'items'
    if ($screenshots -and (-not $screenshotItems -or @($screenshotItems).Count -lt 1)) { $errors.Add('at least one indexed, redacted screenshot is required') }
    if ($screenshotItems) {
        foreach ($item in @($screenshotItems)) {
            $screenshotFile = GetProp $item 'file'
            if (-not $screenshotFile -or $screenshotFile -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]*\.(png|jpe?g)$' -or -not (Test-Path -LiteralPath (Join-Path $Bundle "screenshots/$screenshotFile") -PathType Leaf)) {
                $errors.Add('screenshot index references a missing or unsafe file')
            }
        }
    }
    $stop = ReadJson 'stop-timing.json'
    if ($stop -and $stop.PSObject.Properties.Match('applicable').Count -eq 0) {
        $errors.Add('STOP timing must explicitly state whether it is applicable')
    } elseif ($stop -and (GetProp $stop 'applicable') -eq $true -and (-not (GetProp $stop 'target_ms') -or -not (GetProp $stop 'observed_ms') -or (GetProp $stop 'within_target') -ne $true)) {
        $errors.Add('applicable STOP timing must include target, measurement, and a passing result')
    }
    $desktop = ReadJson 'desktop.json'
    if ($desktop -and (-not (GetProp $desktop 'final_process_state') -or -not (GetProp $desktop 'final_desktop_state') -or (GetProp $desktop 'customer_content_collected') -ne $false)) {
        $errors.Add('final process/desktop state is incomplete or customer content was collected')
    }
    $result = ReadJson 'result.json'
    if ($result -and ((GetProp $result 'case_id') -ne $caseId -or (GetProp $result 'expected') -ne $case.expected -or (GetProp $result 'passed') -ne $true -or (GetProp $result 'server_device_ticket_agree') -ne $true)) {
        $errors.Add('result does not prove expected state and server/device/ticket agreement')
    }

    $valid = $errors.Count -eq 0
    if ($valid) {
        $manifest = [ordered]@{ schema_version = 1; case_id = $caseId; run_id = (Split-Path $Bundle -Leaf); validated_at = (Get-Date).ToUniversalTime().ToString('o'); files = @() }
        foreach ($file in $files | Sort-Object FullName) {
            if ($file.Name -eq 'manifest.json') { continue }
            $manifest.files += [ordered]@{ path = [IO.Path]::GetRelativePath($Bundle, $file.FullName).Replace('\', '/'); sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
        $manifest | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $Bundle 'manifest.json') -Encoding utf8
    }
    $answer = [pscustomobject]@{ valid = $valid; errors = @($errors); bundle = $Bundle }
    if (-not $valid -and $ThrowOnFailure) { throw ($errors -join [Environment]::NewLine) }
    return $answer
}

function Update-JoltIqAggregate {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string]$CatalogPath)
    $catalog = Get-JoltIqCatalog $CatalogPath
    $runs = @()
    foreach ($case in $catalog.cases) {
        $caseRoot = Join-Path $Root $case.id
        foreach ($directory in @(Get-ChildItem -LiteralPath $caseRoot -Directory -ErrorAction SilentlyContinue)) {
            $validation = Test-JoltIqEvidenceBundle -Bundle $directory.FullName -CatalogPath $CatalogPath
            $runs += [ordered]@{ case_id = $case.id; run_id = $directory.Name; valid = $validation.valid; errors = @($validation.errors) }
        }
    }
    $missing = @()
    foreach ($case in $catalog.cases) {
        $requiredCount = if ($case.PSObject.Properties.Match('repetitions').Count -gt 0) { [int]$case.repetitions } else { 1 }
        $actualCount = @($runs | Where-Object { $_.case_id -eq $case.id -and $_.valid }).Count
        if ($actualCount -lt $requiredCount) { $missing += "$($case.id): $actualCount/$requiredCount valid runs" }
    }
    $aggregate = [ordered]@{ schema_version = 1; generated_at = (Get-Date).ToUniversalTime().ToString('o'); passed = ($missing.Count -eq 0 -and @($runs | Where-Object { -not $_.valid }).Count -eq 0); missing = $missing; runs = $runs }
    $aggregate | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $Root 'windows-acceptance.json') -Encoding utf8
    return [pscustomobject]$aggregate
}

function Test-JoltIqEvidenceModule {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$CatalogPath)
    $temp = Join-Path ([IO.Path]::GetTempPath()) ("joltiq-evidence-selftest-" + [guid]::NewGuid().ToString('N'))
    try {
        $catalog = Get-JoltIqCatalog $CatalogPath
        if (@($catalog.required_evidence).Count -lt 14) { throw 'required evidence contract is incomplete' }
        $sample = Join-Path $temp 'happy-fixture-win11-a/run-1'
        New-Item -ItemType Directory -Path $sample -Force | Out-Null
        foreach ($relative in $catalog.required_evidence) {
            $path = Join-Path $sample $relative
            New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
            '{}' | Set-Content -LiteralPath $path
        }
        @{ os_caption = 'Windows 11 Pro'; display_version = '24H2'; os_build = '26100'; architecture = '64-bit'; agent_version = '1.2.3'; server_commit = 'abcdef1'; harness_commit = '1234567'; fixture_version = '1.0.0' } | ConvertTo-Json | Set-Content (Join-Path $sample 'environment.json')
        @{ name = 'clean'; clean = $true; reverted_at = '2026-10-20T00:00:00Z' } | ConvertTo-Json | Set-Content (Join-Path $sample 'snapshot.json')
        @{ installed = $true; version = '1.2.3'; signature_status = 'Valid' } | ConvertTo-Json | Set-Content (Join-Path $sample 'installer.json')
        @{ definition_hash = ('a' * 64); hash_matches = $true; publisher_mfa = $true; distinct_approver = $true } | ConvertTo-Json | Set-Content (Join-Path $sample 'procedure.json')
        @{ ticket_id = 't1'; run_id = 'r1'; session_id = 's1'; ticket_state = 'resolved'; run_state = 'finished'; session_state = 'ended' } | ConvertTo-Json | Set-Content (Join-Path $sample 'workflow.json')
        @{ signature_checks_passed = $true; commands = @(@{ command_id = 'c1'; status = 'completed' }) } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $sample 'commands.json')
        @{ chain_valid = $true; entries = @(@{ id = 'a1' }) } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $sample 'audit.json')
        @{ before = @{ value = 3 }; after = @{ value = 2 }; readback = @{ value = 2 } } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $sample 'registry.json')
        @{ observation = @{ status = 'clean' }; raw_event_payload_collected = $false } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $sample 'events.json')
        @{ applicable = $false } | ConvertTo-Json | Set-Content (Join-Path $sample 'stop-timing.json')
        @{ items = @(@{ file = '01-redacted.png'; proves = 'final state' }) } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $sample 'screenshots/index.json')
        'redacted fixture screenshot' | Set-Content (Join-Path $sample 'screenshots/01-redacted.png')
        @{ final_process_state = 'running'; final_desktop_state = 'clean'; customer_content_collected = $false } | ConvertTo-Json | Set-Content (Join-Path $sample 'desktop.json')
        @{ case_id = 'happy-fixture-win11-a'; expected = 'resolved_verified'; passed = $true; server_device_ticket_agree = $true } | ConvertTo-Json | Set-Content (Join-Path $sample 'result.json')
        $completeResult = Test-JoltIqEvidenceBundle -Bundle $sample -CatalogPath $CatalogPath
        if (-not $completeResult.valid) { throw "complete evidence did not validate: $($completeResult.errors -join '; ')" }
        foreach ($relative in $catalog.required_evidence | Where-Object { $_ -match '\.(json|jsonl)$' }) { '{}' | Set-Content -LiteralPath (Join-Path $sample $relative) }
        $emptyResult = Test-JoltIqEvidenceBundle -Bundle $sample -CatalogPath $CatalogPath
        foreach ($expectedError in @(
            'environment.json missing os_caption',
            'environment.json requires exact hexadecimal server and harness commits',
            'environment.json requires exact OS, agent, and fixture versions',
            'snapshot preflight must name a freshly reverted clean snapshot',
            'installer evidence must contain install state, version, and signature status',
            'procedure hash and two-admin MFA publication evidence must agree',
            'workflow.json missing ticket_id',
            'command evidence requires signed command IDs and statuses',
            'audit chain evidence is missing or invalid',
            'registry pre-state, post-state, and read-back are required',
            'events evidence must be a redacted observation and explicitly exclude raw event payloads',
            'at least one indexed, redacted screenshot is required',
            'STOP timing must explicitly state whether it is applicable',
            'final process/desktop state is incomplete or customer content was collected',
            'result does not prove expected state and server/device/ticket agreement'
        )) {
            if ($emptyResult.errors -notcontains $expectedError) { throw "validator did not enforce: $expectedError" }
        }
        Remove-Item -LiteralPath (Join-Path $sample 'audit.json')
        $result = Test-JoltIqEvidenceBundle -Bundle $sample -CatalogPath $CatalogPath
        if ($result.valid -or -not ($result.errors -contains 'missing evidence: audit.json')) { throw 'missing evidence did not fail closed' }
        'Authorization: Bearer should-never-be-saved' | Set-Content -LiteralPath (Join-Path $sample 'companion.jsonl')
        $result = Test-JoltIqEvidenceBundle -Bundle $sample -CatalogPath $CatalogPath
        if (-not ($result.errors -contains 'secret-like content in companion.jsonl')) { throw 'secret scanner did not fail closed' }
        $sanitized = Protect-JoltIqEvidenceText 'Authorization: Bearer should-never-be-saved password=hunter2'
        if ($sanitized -match $script:SecretPattern -or $sanitized -match 'hunter2|should-never') { throw 'secret sanitizer did not redact known secret forms' }
        return $true
    }
    finally { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}

Export-ModuleMember -Function Protect-JoltIqEvidenceText, Protect-JoltIqEvidenceFile, Get-JoltIqCatalog, New-JoltIqEvidenceBundle, Test-JoltIqEvidenceBundle, Update-JoltIqAggregate, Test-JoltIqEvidenceModule
