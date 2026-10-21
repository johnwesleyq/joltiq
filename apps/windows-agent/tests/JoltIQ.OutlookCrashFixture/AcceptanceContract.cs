using System.Text.Json;
using System.Text.RegularExpressions;

namespace JoltIQ.OutlookCrashFixture;

internal static partial class AcceptanceContract
{
    internal static readonly string[] RequiredEvidence =
    [
        "environment.json", "snapshot.json", "installer.json", "procedure.json", "workflow.json",
        "commands.json", "audit.json", "companion.jsonl", "registry.json", "events.json",
        "stop-timing.json", "screenshots/index.json", "desktop.json", "result.json"
    ];

    private static readonly string[] RequiredCaseIds =
    [
        "happy-fixture-win11-a", "happy-fixture-win11-b", "happy-fixture-win10", "happy-real-outlook-win11",
        "unsupported-new-outlook", "unsupported-outlook-absent", "unsupported-addin-absent",
        "diagnosis-crash-absent", "diagnosis-wrong-module", "event-log-disabled", "event-log-unreadable",
        "event-log-stale-window", "event-log-unknown-observation", "addin-policy-controlled", "addin-machine-scoped",
        "screen-locked", "no-interactive-user", "multiple-interactive-users", "rdp-session",
        "enrolled-user-mismatch-diagnostic", "enrolled-user-mismatch-before-write", "portal-consent-denied",
        "local-consent-denied", "consent-expired", "approval-payload-changed", "invalid-command-signature",
        "device-revoked", "duplicate-command", "expired-command", "offline-before-action",
        "offline-after-registry-write", "offline-before-result", "outlook-save-prompt", "registry-write-failure",
        "outlook-restart-failure", "outlook-start-failure", "registry-readback-failure", "symptom-persists",
        "symptom-confirmation-deferred", "stop-before-mutation", "stop-during-registry-write", "stop-during-restart",
        "rollback-accepted", "rollback-declined", "rollback-unavailable-offline", "rollback-unavailable-revoked",
        "rollback-missing-local-entry", "rollback-restore-failed", "rollback-restart-declined",
        "rollback-restart-failed", "remembered-fix-cannot-execute", "screen-control-action-refused",
        "publish-self-approval-refused", "publish-without-mfa-refused", "published-hash-mismatch", "publish-two-admin-valid"
    ];

    internal static void ValidateCatalog(string casesPath)
    {
        using var document = JsonDocument.Parse(File.ReadAllText(casesPath));
        var root = document.RootElement;
        if (root.GetProperty("schema_version").GetInt32() != 1) throw new InvalidDataException("unsupported schema_version");
        var evidence = root.GetProperty("required_evidence").EnumerateArray().Select(x => x.GetString()!).ToHashSet(StringComparer.Ordinal);
        var missingEvidence = RequiredEvidence.Where(x => !evidence.Contains(x)).ToArray();
        if (missingEvidence.Length > 0) throw new InvalidDataException($"catalog misses evidence: {string.Join(", ", missingEvidence)}");
        var cases = root.GetProperty("cases").EnumerateArray().ToArray();
        var ids = cases.Select(x => x.GetProperty("id").GetString()!).ToArray();
        if (ids.Length != ids.Distinct(StringComparer.Ordinal).Count()) throw new InvalidDataException("duplicate case id");
        var missingCases = RequiredCaseIds.Where(x => !ids.Contains(x, StringComparer.Ordinal)).ToArray();
        if (missingCases.Length > 0) throw new InvalidDataException($"catalog misses cases: {string.Join(", ", missingCases)}");
        foreach (var item in cases)
        {
            foreach (var property in new[] { "id", "category", "mode", "expected" })
                if (!item.TryGetProperty(property, out var value) || string.IsNullOrWhiteSpace(value.GetString()))
                    throw new InvalidDataException($"case lacks {property}");
        }
        var repetitions = cases.Where(x => x.GetProperty("id").GetString()!.StartsWith("happy-fixture-win11", StringComparison.Ordinal))
            .Sum(x => x.GetProperty("repetitions").GetInt32());
        if (repetitions != 5) throw new InvalidDataException("Windows 11 fixture runs must total exactly five");
        if (!cases.Any(x => x.GetProperty("mode").GetString() == "licensed_outlook"))
            throw new InvalidDataException("licensed classic Outlook case is required");
    }

    internal static void ValidateBundle(IReadOnlyDictionary<string, string> files)
    {
        var missing = RequiredEvidence.Where(x => !files.ContainsKey(x)).ToArray();
        if (missing.Length > 0) throw new InvalidDataException($"missing evidence: {string.Join(", ", missing)}");
        foreach (var (path, content) in files)
        {
            if (SecretPattern().IsMatch(content)) throw new InvalidDataException($"secret-like value found in {path}");
            if (path.Equals("result.json", StringComparison.Ordinal) && !content.Contains("\"passed\":true", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("result.json does not record passed=true");
        }
    }

    internal static void AssertCompleteSyntheticEvidence() => ValidateBundle(Synthetic());

    internal static void AssertMissingEvidenceFails()
    {
        foreach (var required in RequiredEvidence)
        {
            var files = Synthetic();
            files.Remove(required);
            try { ValidateBundle(files); }
            catch (InvalidDataException) { continue; }
            throw new Exception($"missing {required} was accepted");
        }
    }

    internal static void AssertSecretsAreRejected()
    {
        foreach (var secret in new[] { "Authorization: Bearer abc123", "password=hunter2", "client_secret=topsecret", "BEGIN PRIVATE KEY" })
        {
            var files = Synthetic();
            files["companion.jsonl"] = secret;
            try { ValidateBundle(files); }
            catch (InvalidDataException) { continue; }
            throw new Exception($"secret pattern was accepted: {secret.Split('=')[0]}");
        }
    }

    private static Dictionary<string, string> Synthetic() => RequiredEvidence.ToDictionary(x => x, x =>
        x == "result.json" ? "{\"passed\":true}" : "{\"redacted\":true}", StringComparer.Ordinal);

    [GeneratedRegex("(?i)(authorization\\s*:\\s*bearer|password\\s*[=:]|client[_-]?secret\\s*[=:]|begin (rsa |ec )?private key|set-cookie\\s*:)")]
    private static partial Regex SecretPattern();
}
