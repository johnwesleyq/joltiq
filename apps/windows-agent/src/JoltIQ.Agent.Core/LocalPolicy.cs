using System.Text.Json;
using System.Text.RegularExpressions;

namespace JoltIQ.Agent.Core;

/// <summary>
/// Device-side policy. Enforced independently of the server: even a fully signed request is refused unless the action
/// is allowlisted here, its parameters are valid, a locally-consented session is active, and the target is permitted.
/// Mirrors apps/api/joltiq/services/actions/registry.py (desktop channel).
/// </summary>
public sealed class LocalPolicy
{
    private static readonly Regex ServiceName = new(@"^[A-Za-z0-9_.\- ]{1,80}$", RegexOptions.Compiled);
    private static readonly Regex ProcessName = new(@"^[A-Za-z0-9_.\- ]{1,80}(\.exe)?$", RegexOptions.Compiled);
    private static readonly Regex Host = new(@"^[A-Za-z0-9.-]{1,253}$", RegexOptions.Compiled);
    private static readonly Regex ProgId = new(@"^[A-Za-z0-9_.\-]{1,120}$", RegexOptions.Compiled);

    public static readonly IReadOnlySet<string> ReadOnlyActions = new HashSet<string>
        { "system.info", "process.list", "app.info", "disk.usage", "network.diagnostics", "eventlog.query", "service.status" };

    public static readonly IReadOnlySet<string> AllActions = new HashSet<string>(ReadOnlyActions)
        { "file.inspect", "config.read", "app.restart", "office.addin.set_load_behavior", "service.restart", "ui.invoke", "ui.set_text" };

    /// <summary>Applications the companion may restart or drive via UI Automation. Admin-extensible via local config.</summary>
    public HashSet<string> AllowedApps { get; } = new(StringComparer.OrdinalIgnoreCase)
        { "outlook.exe", "excel.exe", "winword.exe", "teams.exe", "ms-teams.exe", "onedrive.exe", "acrord32.exe", "qbw32.exe", "notepad.exe" };

    public HashSet<string> AllowedServices { get; } = new(StringComparer.OrdinalIgnoreCase)
        { "Spooler", "wuauserv", "BITS", "Dnscache", "W32Time", "WSearch", "AudioSrv" };

    /// <summary>Prefixes (after environment expansion) under which file.inspect may read.</summary>
    public List<string> AllowedFilePrefixes { get; } = new()
        { @"%LOCALAPPDATA%\Microsoft\Outlook\", @"%APPDATA%\Microsoft\Outlook\", @"%TEMP%\", @"%ProgramData%\JoltIQ\logs\" };

    public List<string> AllowedRegistryPrefixes { get; } = new()
        { @"HKCU\Software\Microsoft\Office\", @"HKCU\Software\Policies\Microsoft\Office\" };

    public static readonly string[] BlockedFileExtensions = { ".pst", ".ost", ".kdbx", ".key", ".pfx", ".p12", ".pem", ".rdp", ".cred" };

    public Func<string, string> ExpandEnv { get; set; } = Environment.ExpandEnvironmentVariables;

    public sealed record Verdict(bool Allowed, string Reason);

    public Verdict Evaluate(string action, JsonElement p, LocalSession? session)
    {
        if (!AllActions.Contains(action)) return new(false, $"action '{action}' is not allowlisted on this device");
        if (session is null || !session.Active) return new(false, "no active, locally-consented session");
        if (session.Expired) return new(false, "session expired");
        string? S(string k) => p.ValueKind == JsonValueKind.Object && p.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        int? I(string k) => p.ValueKind == JsonValueKind.Object && p.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : null;
        bool Has(string k) => p.ValueKind == JsonValueKind.Object && p.TryGetProperty(k, out _);

        switch (action)
        {
            case "system.info" or "disk.usage":
                return Ok();
            case "process.list":
                return S("name_filter") is { } nf && !ProcessName.IsMatch(nf) ? No("bad name_filter") : Ok();
            case "app.info":
                return S("name_filter") is { Length: > 0 and <= 80 } ? Ok() : No("name_filter required");
            case "network.diagnostics":
                return S("resolve_host") is { } h && !Host.IsMatch(h) ? No("bad host") : Ok();
            case "eventlog.query":
                if (S("log") is not ("Application" or "System")) return No("log must be Application or System");
                if (I("hours") is { } hrs && (hrs < 1 || hrs > 168)) return No("hours out of range");
                if (I("max_events") is { } m && (m < 1 || m > 100)) return No("max_events out of range");
                return Ok();
            case "service.status":
                return S("name") is { } sn && ServiceName.IsMatch(sn) ? Ok() : No("bad service name");
            case "service.restart":
                return S("name") is { } rs && ServiceName.IsMatch(rs) && AllowedServices.Contains(rs) ? Ok() : No("service not allowlisted for restart");
            case "app.restart":
                if (S("process") is not { } proc || !ProcessName.IsMatch(proc)) return No("bad process");
                if (!AllowedApps.Contains(Normalize(proc))) return No("application not allowlisted");
                return S("args") is null or "" or "/safe" ? Ok() : No("args not allowed");
            case "office.addin.set_load_behavior":
                if (S("app") is not ("Outlook" or "Excel" or "Word")) return No("bad app");
                if (S("addin_progid") is not { } pid || !ProgId.IsMatch(pid)) return No("bad add-in id");
                return Has("enabled") ? Ok() : No("enabled required");
            case "config.read":
                return S("key") is { } key && AllowedRegistryPrefixes.Any(pr => key.StartsWith(pr, StringComparison.OrdinalIgnoreCase))
                       && !key.Contains("..") && S("value") is { Length: > 0 and <= 120 } ? Ok() : No("registry key not allowlisted");
            case "file.inspect":
                return CheckPath(S("path"));
            case "ui.invoke" or "ui.set_text":
                if (S("process") is not { } up || !AllowedApps.Contains(Normalize(up))) return No("application not allowlisted for UI automation");
                if (action == "ui.set_text" && (S("text") is not { Length: <= 500 } || S("automation_id") is null)) return No("bad text/automation_id");
                return Ok();
        }
        return No("unhandled action");
    }

    public static string Normalize(string proc) => proc.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ? proc : proc + ".exe";

    private Verdict CheckPath(string? path)
    {
        if (string.IsNullOrWhiteSpace(path) || path.Length > 260) return No("path required");
        if (path.Contains("..") || path.Contains('\0') || path.StartsWith(@"\\")) return No("path traversal / UNC not allowed");
        var full = ExpandEnv(path);
        if (BlockedFileExtensions.Any(e => full.EndsWith(e, StringComparison.OrdinalIgnoreCase))) return No("file type is never read");
        var ok = AllowedFilePrefixes.Select(ExpandEnv).Any(pr => full.StartsWith(pr, StringComparison.OrdinalIgnoreCase));
        return ok ? Ok() : No("path outside allowed folders");
    }

    private static Verdict Ok() => new(true, "");
    private static Verdict No(string r) => new(false, r);
}

public sealed class LocalSession
{
    public required string SessionId { get; init; }
    public bool Active { get; set; }
    public long? ExpiresAt { get; init; }
    public bool Expired => ExpiresAt is { } e && e < DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    public CancellationTokenSource Cancel { get; } = new();
}
