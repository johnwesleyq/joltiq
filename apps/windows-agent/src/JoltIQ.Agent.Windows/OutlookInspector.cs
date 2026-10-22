using System.Diagnostics.Eventing.Reader;
using System.IO;
using System.Runtime.Versioning;
using System.ServiceProcess;
using System.Text.Json.Nodes;
using Microsoft.Win32;

namespace JoltIQ.Agent.Windows;

public sealed record AddinFact(string Scope, string View, int? LoadBehavior, string? ValueKind, bool PolicyOverride);
public sealed record CrashLogFact(bool Readable, bool Enabled, bool ServiceRunning, DateTimeOffset? Oldest,
    DateTimeOffset? Newest, IReadOnlyList<DateTimeOffset> Matches);
public sealed record OutlookInspectionEvidence(bool WindowsSupported, string WindowsVersion, JsonObject Interactive,
    bool ClassicInstalled, string? ClassicPath, bool NewOutlookInstalled, IReadOnlyList<AddinFact> Addins, CrashLogFact Crash);

/// <summary>Fixed-target Outlook/PDF crash inspection. It never accepts a registry key or log name from the server.</summary>
[SupportedOSPlatform("windows10.0.19041.0")]
public sealed class OutlookInspector
{
    private readonly InteractiveSessionInspector _interactive;
    public OutlookInspector(InteractiveSessionInspector? interactive = null) => _interactive = interactive ?? new();

    public JsonObject Inspect(string progId, string module, int lookbackHours, DateTimeOffset? since)
    {
        var start = since ?? DateTimeOffset.UtcNow.AddHours(-lookbackHours);
        var evidence = new OutlookInspectionEvidence(
            OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041) && Environment.Is64BitOperatingSystem,
            Environment.OSVersion.VersionString, _interactive.Inspect(), FindClassicOutlook(out var path), path,
            FindNewOutlook(), ReadAddin(progId), ReadCrashes(module, start));
        return Evaluate(evidence, start);
    }

    public static JsonObject Evaluate(OutlookInspectionEvidence e, DateTimeOffset windowStart)
    {
        var reasons = new JsonArray();
        if (!e.WindowsSupported) reasons.Add("unsupported_windows");
        if (!(e.Interactive["supported"]?.GetValue<bool>() ?? false))
            foreach (var reason in e.Interactive["reasons"]?.AsArray() ?? new JsonArray()) reasons.Add(reason?.DeepClone());
        if (!e.ClassicInstalled) reasons.Add("classic_outlook_not_installed");
        if (e.NewOutlookInstalled) reasons.Add("new_outlook_present");

        var policy = e.Addins.Any(x => x.PolicyOverride);
        var candidates = e.Addins.Where(x => !x.PolicyOverride).ToList();
        var selected = candidates.FirstOrDefault(x => x.Scope == "HKCU") ?? candidates.FirstOrDefault();
        if (selected is null) reasons.Add("addin_not_installed");
        if (policy) reasons.Add("addin_policy_controlled");
        if (selected is not null && selected.Scope != "HKCU") reasons.Add("addin_machine_scoped");

        var trustworthy = e.Crash.Readable && e.Crash.Enabled && e.Crash.ServiceRunning && e.Crash.Oldest is { } oldest && oldest <= windowStart;
        if (!e.Crash.Readable) reasons.Add("application_log_unreadable");
        else if (!e.Crash.Enabled) reasons.Add("application_log_disabled");
        else if (!e.Crash.ServiceRunning) reasons.Add("event_log_service_unavailable");
        else if (!trustworthy) reasons.Add("application_log_window_untrustworthy");
        var matched = e.Crash.Matches.Any(x => x >= windowStart);
        if (trustworthy && !matched) reasons.Add("matching_crash_not_found");

        return new JsonObject
        {
            ["windows"] = new JsonObject { ["supported"] = e.WindowsSupported, ["version"] = e.WindowsVersion, ["is_64_bit"] = Environment.Is64BitOperatingSystem },
            ["interactive"] = e.Interactive.DeepClone(),
            ["outlook"] = new JsonObject { ["kind"] = e.ClassicInstalled ? "classic" : e.NewOutlookInstalled ? "new" : "absent",
                                            ["classic_installed"] = e.ClassicInstalled, ["new_installed"] = e.NewOutlookInstalled,
                                            ["path"] = e.ClassicPath },
            ["addin"] = new JsonObject { ["installed"] = selected is not null, ["scope"] = selected?.Scope, ["registry_view"] = selected?.View,
                                         ["original_value"] = selected?.LoadBehavior, ["original_kind"] = selected?.ValueKind,
                                         ["policy_override"] = policy, ["changeable"] = selected?.Scope == "HKCU" && !policy },
            ["crash"] = new JsonObject { ["log_readable"] = e.Crash.Readable, ["log_enabled"] = e.Crash.Enabled,
                                         ["service_running"] = e.Crash.ServiceRunning, ["window_start"] = windowStart.ToString("O"),
                                         ["window_trustworthy"] = trustworthy, ["newest_event_timestamp"] = e.Crash.Newest?.ToString("O"),
                                         ["matched"] = matched, ["matching_event_count"] = e.Crash.Matches.Count(x => x >= windowStart) },
            ["supported"] = reasons.Count == 0,
            ["unsupported_reasons"] = reasons,
        };
    }

    private static bool FindClassicOutlook(out string? path)
    {
        foreach (var hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            using var root = RegistryKey.OpenBaseKey(hive, view);
            using var key = root.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE");
            if (key?.GetValue(null) is string value && File.Exists(value)) { path = value; return true; }
        }
        path = null;
        return false;
    }

    private static bool FindNewOutlook()
    {
        using var key = Registry.CurrentUser.OpenSubKey(@"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages");
        return key?.GetSubKeyNames().Any(x => x.StartsWith("Microsoft.OutlookForWindows_", StringComparison.OrdinalIgnoreCase)) == true;
    }

    private static IReadOnlyList<AddinFact> ReadAddin(string progId)
    {
        var facts = new List<AddinFact>();
        // HKCU\Software is shared across WOW64 views, so inspect it once rather than reporting a
        // duplicate 32/64 registration. HKLM\Software remains redirected and retains both views.
        using (var root = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64))
        {
            using var key = root.OpenSubKey($@"Software\Microsoft\Office\Outlook\Addins\{progId}");
            if (key is not null)
            {
                var raw = key.GetValue("LoadBehavior", null, RegistryValueOptions.DoNotExpandEnvironmentNames);
                facts.Add(new("HKCU", "shared", raw is int i ? i : null,
                    raw is null ? null : key.GetValueKind("LoadBehavior").ToString(), false));
            }
            using var policy = root.OpenSubKey(@"Software\Policies\Microsoft\Office\16.0\Outlook\Resiliency\AddinList");
            if (policy?.GetValue(progId) is not null) facts.Add(new("HKCU", "shared", null, null, true));
        }
        foreach (var (view, viewName) in new[] { (RegistryView.Registry64, "64"), (RegistryView.Registry32, "32") })
        {
            using var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view);
            using var key = root.OpenSubKey($@"Software\Microsoft\Office\Outlook\Addins\{progId}");
            if (key is not null)
            {
                var raw = key.GetValue("LoadBehavior", null, RegistryValueOptions.DoNotExpandEnvironmentNames);
                facts.Add(new("HKLM", viewName, raw is int i ? i : null,
                    raw is null ? null : key.GetValueKind("LoadBehavior").ToString(), false));
            }
        }
        using (var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
        {
            using var policy = root.OpenSubKey(@"Software\Policies\Microsoft\Office\16.0\Outlook\Resiliency\AddinList");
            if (policy?.GetValue(progId) is not null) facts.Add(new("HKLM", "shared", null, null, true));
        }
        return facts;
    }

    private static CrashLogFact ReadCrashes(string module, DateTimeOffset start)
    {
        var readable = true;
        var enabled = false;
        var serviceRunning = false;
        DateTimeOffset? oldest = null;
        DateTimeOffset? newest = null;
        var matches = new List<DateTimeOffset>();
        try
        {
            using var service = new ServiceController("EventLog");
            serviceRunning = service.Status == ServiceControllerStatus.Running;
            using var config = new EventLogConfiguration("Application");
            enabled = config.IsEnabled;
            var query = new EventLogQuery("Application", PathType.LogName,
                $"*[System[(EventID=1000 or EventID=1001) and TimeCreated[@SystemTime >= '{start.UtcDateTime:O}']]]") { ReverseDirection = true };
            using var reader = new EventLogReader(query);
            for (var item = reader.ReadEvent(); item is not null; item = reader.ReadEvent())
            {
                using (item)
                {
                    if (item.TimeCreated is not { } time) continue;
                    var at = new DateTimeOffset(time.ToUniversalTime());
                    newest ??= at;
                    oldest = at;
                    string description;
                    try { description = item.FormatDescription() ?? item.ToXml(); }
                    catch (EventLogException) { description = item.ToXml(); }
                    if (description.Contains("OUTLOOK.EXE", StringComparison.OrdinalIgnoreCase) &&
                        description.Contains(module, StringComparison.OrdinalIgnoreCase)) matches.Add(at);
                }
            }
            // A second fixed query establishes whether retained Application events cover the requested window.
            using var all = new EventLogReader(new EventLogQuery("Application", PathType.LogName) { ReverseDirection = false });
            using var first = all.ReadEvent();
            if (first?.TimeCreated is { } firstTime) oldest = new DateTimeOffset(firstTime.ToUniversalTime());
        }
        catch (UnauthorizedAccessException) { readable = false; }
        catch (EventLogException) { readable = false; }
        catch (InvalidOperationException) { readable = false; }
        return new(readable, enabled, serviceRunning, oldest, newest, matches);
    }
}
