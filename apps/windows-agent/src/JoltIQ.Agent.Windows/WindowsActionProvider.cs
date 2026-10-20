using System.Diagnostics;
using System.Diagnostics.Eventing.Reader;
using System.IO;
using System.IO.Pipes;
using System.Net.NetworkInformation;
using System.Runtime.Versioning;
using System.ServiceProcess;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Automation;
using JoltIQ.Agent.Core;
using Microsoft.Win32;

namespace JoltIQ.Agent.Windows;

/// <summary>Real Windows implementations of the typed, allowlisted desktop actions. Runs as the logged-in user (no admin).
/// Privileged actions are forwarded to the separate JoltIQ.Agent.Broker service over an ACL'd named pipe.</summary>
[SupportedOSPlatform("windows10.0.19041.0")]
public sealed class WindowsActionProvider : IActionProvider
{
    public const string BrokerPipe = "JoltIQ.Agent.Broker.v1";
    private readonly string? _enrolledUserSid;
    public WindowsActionProvider(string? enrolledUserSid = null) => _enrolledUserSid = enrolledUserSid;

    public IReadOnlyCollection<string> Capabilities { get; } = new[]
    {
        "system.info", "process.list", "app.info", "disk.usage", "network.diagnostics", "eventlog.query", "service.status",
        "outlook.pdf_crash.inspect", "file.inspect", "config.read", "app.restart", "office.addin.set_load_behavior", "service.restart", "ui.invoke", "ui.set_text",
    };

    public IDictionary<string, string> Facts() => new Dictionary<string, string>
    {
        ["os"] = Environment.OSVersion.VersionString, ["machine"] = Environment.MachineName, ["is64"] = Environment.Is64BitOperatingSystem.ToString(),
        ["build"] = Registry.GetValue(@"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion", "")?.ToString() ?? "",
    };

    public async Task<JsonObject> ExecuteAsync(string action, JsonElement p, CancellationToken ct)
    {
        string S(string k) => p.GetProperty(k).GetString()!;
        string? O(string k) => p.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        int I(string k, int d) => p.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : d;
        return action switch
        {
            "system.info" => SystemInfo(),
            "process.list" => Processes(O("name_filter")),
            "app.info" => InstalledApps(S("name_filter")),
            "disk.usage" => Disks(),
            "network.diagnostics" => await Network(O("resolve_host"), ct),
            "eventlog.query" => Events(S("log"), O("provider"), O("level"), I("hours", 24), I("max_events", 20)),
            "outlook.pdf_crash.inspect" => new OutlookInspector(new InteractiveSessionInspector(_enrolledUserSid)).Inspect(
                S("addin_progid"), S("faulting_module"), I("lookback_hours", 24),
                DateTimeOffset.TryParse(O("since"), out var since) ? since : null),
            "service.status" => ServiceStatus(S("name")),
            "file.inspect" => InspectFile(Environment.ExpandEnvironmentVariables(S("path"))),
            "config.read" => ReadRegistry(S("key"), S("value")),
            "app.restart" => await RestartApp(S("process"), O("args"), ct),
            "office.addin.set_load_behavior" => SetAddin(S("app"), S("addin_progid"), p.GetProperty("enabled").GetBoolean()),
            "service.restart" => await Broker(new JsonObject { ["op"] = "service.restart", ["name"] = S("name") }, ct),
            "ui.invoke" => UiInvoke(S("process"), O("automation_id"), O("name"), O("control_type")),
            "ui.set_text" => UiSetText(S("process"), S("automation_id"), S("text")),
            _ => new JsonObject { ["ok"] = false, ["error"] = "unsupported" },
        };
    }

    private static JsonObject SystemInfo()
    {
        var gc = GC.GetGCMemoryInfo();
        return new JsonObject
        {
            ["os"] = Environment.OSVersion.VersionString, ["machine"] = Environment.MachineName,
            ["uptime_hours"] = Math.Round(Environment.TickCount64 / 3_600_000.0, 1), ["cpu_count"] = Environment.ProcessorCount,
            ["total_memory_gb"] = Math.Round(gc.TotalAvailableMemoryBytes / 1073741824.0, 1),
        };
    }

    private static JsonObject Processes(string? filter)
    {
        var name = filter is null ? null : Path.GetFileNameWithoutExtension(filter);
        var arr = new JsonArray();
        foreach (var pr in (name is null ? Process.GetProcesses() : Process.GetProcessesByName(name)).Take(200))
        {
            try
            {
                arr.Add(new JsonObject { ["name"] = pr.ProcessName + ".exe", ["pid"] = pr.Id, ["memory_mb"] = pr.WorkingSet64 / 1048576,
                                         ["responding"] = pr.MainWindowHandle == IntPtr.Zero || pr.Responding });
            }
            catch (InvalidOperationException) { }
            finally { pr.Dispose(); }
        }
        return new JsonObject { ["processes"] = arr };
    }

    private static JsonObject InstalledApps(string filter)
    {
        var arr = new JsonArray();
        foreach (var root in new[] { Registry.LocalMachine, Registry.CurrentUser })
        foreach (var path in new[] { @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", @"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" })
        {
            using var k = root.OpenSubKey(path);
            if (k is null) continue;
            foreach (var sub in k.GetSubKeyNames())
            {
                using var a = k.OpenSubKey(sub);
                var dn = a?.GetValue("DisplayName") as string;
                if (dn is null || !dn.Contains(filter, StringComparison.OrdinalIgnoreCase)) continue;
                arr.Add(new JsonObject { ["name"] = dn, ["version"] = a!.GetValue("DisplayVersion") as string, ["publisher"] = a.GetValue("Publisher") as string });
                if (arr.Count >= 25) return new JsonObject { ["apps"] = arr };
            }
        }
        return new JsonObject { ["apps"] = arr };
    }

    private static JsonObject Disks() => new()
    {
        ["drives"] = new JsonArray(DriveInfo.GetDrives().Where(d => d.IsReady && d.DriveType == DriveType.Fixed)
            .Select(d => (JsonNode)new JsonObject { ["name"] = d.Name, ["free_gb"] = d.AvailableFreeSpace / 1073741824, ["total_gb"] = d.TotalSize / 1073741824 }).ToArray()),
    };

    private static async Task<JsonObject> Network(string? host, CancellationToken ct)
    {
        var adapters = new JsonArray();
        string? gw = null;
        foreach (var n in NetworkInterface.GetAllNetworkInterfaces().Where(n => n.OperationalStatus == OperationalStatus.Up && n.NetworkInterfaceType != NetworkInterfaceType.Loopback))
        {
            var ip = n.GetIPProperties();
            gw ??= ip.GatewayAddresses.FirstOrDefault()?.Address.ToString();
            adapters.Add(new JsonObject { ["name"] = n.Name, ["type"] = n.NetworkInterfaceType.ToString(), ["dns"] = string.Join(",", ip.DnsAddresses.Select(d => d.ToString())) });
        }
        bool? gwOk = null;
        if (gw is not null) { using var ping = new Ping(); try { gwOk = (await ping.SendPingAsync(gw, 1500)).Status == IPStatus.Success; } catch (PingException) { gwOk = false; } }
        JsonObject res = new() { ["adapters"] = adapters, ["gateway_reachable"] = gwOk };
        if (host is not null)
        {
            try { res["resolved"] = string.Join(",", (await System.Net.Dns.GetHostAddressesAsync(host, ct)).Select(a => a.ToString())); res["dns_ok"] = true; }
            catch (System.Net.Sockets.SocketException) { res["dns_ok"] = false; }
        }
        return res;
    }

    private static JsonObject Events(string log, string? provider, string? level, int hours, int max)
    {
        var lvl = level switch { "Error" => "Level=2", "Warning" => "Level=3", "Information" => "Level=4", _ => "Level<=4" };
        var prov = provider is null ? "" : $" and Provider[@Name='{provider.Replace("'", "")}']";
        var q = new EventLogQuery(log, PathType.LogName, $"*[System[({lvl}){prov} and TimeCreated[timediff(@SystemTime) <= {hours * 3_600_000L}]]]") { ReverseDirection = true };
        var arr = new JsonArray();
        using var reader = new EventLogReader(q);
        for (var e = reader.ReadEvent(); e is not null && arr.Count < max; e = reader.ReadEvent())
        {
            using (e)
            {
                string msg;
                try { msg = e.FormatDescription() ?? ""; } catch (EventLogException) { msg = ""; }
                arr.Add(new JsonObject { ["time"] = e.TimeCreated?.ToUniversalTime().ToString("o"), ["provider"] = e.ProviderName, ["id"] = e.Id,
                                         ["level"] = e.LevelDisplayName, ["message"] = Redactor.Redact(msg.Length > 600 ? msg[..600] : msg) });
            }
        }
        return new JsonObject { ["events"] = arr };
    }

    private static JsonObject ServiceStatus(string name)
    {
        try
        {
            using var sc = new ServiceController(name);
            return new JsonObject { ["name"] = name, ["status"] = sc.Status.ToString(), ["start_type"] = sc.StartType.ToString() };
        }
        catch (InvalidOperationException) { return new JsonObject { ["name"] = name, ["status"] = "NotFound" }; }
    }

    private static JsonObject InspectFile(string path)
    {
        var fi = new FileInfo(path);
        if (!fi.Exists) return new JsonObject { ["exists"] = false };
        if ((fi.Attributes & FileAttributes.ReparsePoint) != 0) return new JsonObject { ["ok"] = false, ["error"] = "symbolic links are not followed" };
        var buf = new byte[Math.Min(65536, fi.Length)];
        using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite)) fs.ReadExactly(buf);
        var text = Encoding.UTF8.GetString(buf);
        return new JsonObject { ["exists"] = true, ["size"] = fi.Length, ["modified"] = fi.LastWriteTimeUtc.ToString("o"), ["head"] = Redactor.Redact(text) };
    }

    private static JsonObject ReadRegistry(string key, string value)
    {
        var sub = key.StartsWith(@"HKCU\", StringComparison.OrdinalIgnoreCase) ? key[5..] : throw new InvalidOperationException("HKCU only");
        using var k = Registry.CurrentUser.OpenSubKey(sub);
        var v = k?.GetValue(value);
        return v is null ? new JsonObject { ["exists"] = false } : new JsonObject { ["exists"] = true, ["data"] = v.ToString() };
    }

    private static JsonObject SetAddin(string app, string progId, bool enabled)
    {
        // Per-user add-in key (no admin). LoadBehavior 3 = load at startup, 2 = do not load.
        using var k = Registry.CurrentUser.CreateSubKey($@"Software\Microsoft\Office\{app}\Addins\{progId}");
        var before = k.GetValue("LoadBehavior");
        k.SetValue("LoadBehavior", enabled ? 3 : 2, RegistryValueKind.DWord);
        return new JsonObject { ["addin"] = progId, ["before"] = before?.ToString(), ["after"] = enabled ? 3 : 2 };
    }

    private static async Task<JsonObject> RestartApp(string proc, string? args, CancellationToken ct)
    {
        var name = Path.GetFileNameWithoutExtension(proc);
        string? exe = null;
        foreach (var p in Process.GetProcessesByName(name))
        {
            try { exe ??= p.MainModule?.FileName; p.CloseMainWindow(); } catch (InvalidOperationException) { }
        }
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while (Process.GetProcessesByName(name).Length > 0 && DateTime.UtcNow < deadline) await Task.Delay(500, ct);
        if (Process.GetProcessesByName(name).Length > 0)
            return new JsonObject { ["ok"] = false, ["error"] = "application did not close (the user may have unsaved work); not forcing" };
        exe ??= AppPathFromRegistry(LocalPolicy.Normalize(proc));
        if (exe is null) return new JsonObject { ["ok"] = false, ["error"] = "could not locate application executable" };
        Process.Start(new ProcessStartInfo(exe, args ?? "") { UseShellExecute = true });
        await Task.Delay(4000, ct);
        return new JsonObject { ["restarted"] = proc, ["running"] = Process.GetProcessesByName(name).Length > 0 };
    }

    private static string? AppPathFromRegistry(string exe) =>
        Registry.GetValue($@"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}", "", null) as string;

    private static async Task<JsonObject> Broker(JsonObject req, CancellationToken ct)
    {
        await using var pipe = new NamedPipeClientStream(".", BrokerPipe, PipeDirection.InOut, PipeOptions.Asynchronous);
        try { await pipe.ConnectAsync(3000, ct); }
        catch (System.TimeoutException) { return new JsonObject { ["ok"] = false, ["error"] = "privileged broker service is not installed or not running" }; }
        var bytes = Encoding.UTF8.GetBytes(req.ToJsonString() + "\n");
        await pipe.WriteAsync(bytes, ct);
        await pipe.FlushAsync(ct);
        using var sr = new StreamReader(pipe);
        var line = await sr.ReadLineAsync(ct);
        return JsonNode.Parse(line ?? "{\"ok\":false,\"error\":\"no response\"}")!.AsObject();
    }

    private static AutomationElement? AppWindow(string proc)
    {
        var p = Process.GetProcessesByName(Path.GetFileNameWithoutExtension(proc)).FirstOrDefault(x => x.MainWindowHandle != IntPtr.Zero);
        return p is null ? null : AutomationElement.FromHandle(p.MainWindowHandle);
    }

    private static AutomationElement? Find(AutomationElement root, string? automationId, string? name, string? controlType)
    {
        var conds = new List<Condition>();
        if (automationId is not null) conds.Add(new PropertyCondition(AutomationElement.AutomationIdProperty, automationId));
        if (name is not null) conds.Add(new PropertyCondition(AutomationElement.NameProperty, name));
        if (controlType is not null)
        {
            var ct = (ControlType?)typeof(ControlType).GetField(controlType)?.GetValue(null);
            if (ct is not null) conds.Add(new PropertyCondition(AutomationElement.ControlTypeProperty, ct));
        }
        Condition c = conds.Count switch { 0 => Condition.FalseCondition, 1 => conds[0], _ => new AndCondition(conds.ToArray()) };
        return root.FindFirst(TreeScope.Descendants, c);
    }

    private static JsonObject UiInvoke(string proc, string? id, string? name, string? type)
    {
        var w = AppWindow(proc);
        if (w is null) return new JsonObject { ["ok"] = false, ["error"] = "application window not found" };
        var el = Find(w, id, name, type);
        if (el is null) return new JsonObject { ["ok"] = false, ["error"] = "control not found" };
        if (el.TryGetCurrentPattern(InvokePattern.Pattern, out var ip)) ((InvokePattern)ip).Invoke();
        else if (el.TryGetCurrentPattern(TogglePattern.Pattern, out var tp)) ((TogglePattern)tp).Toggle();
        else if (el.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var sp)) ((SelectionItemPattern)sp).Select();
        else return new JsonObject { ["ok"] = false, ["error"] = "control is not invokable" };
        return new JsonObject { ["invoked"] = el.Current.Name };
    }

    private static JsonObject UiSetText(string proc, string id, string text)
    {
        var w = AppWindow(proc);
        var el = w is null ? null : Find(w, id, null, null);
        if (el is null) return new JsonObject { ["ok"] = false, ["error"] = "control not found" };
        if (el.Current.IsPassword) return new JsonObject { ["ok"] = false, ["error"] = "refusing to type into a password field" };
        if (!el.TryGetCurrentPattern(ValuePattern.Pattern, out var vp) || ((ValuePattern)vp).Current.IsReadOnly)
            return new JsonObject { ["ok"] = false, ["error"] = "control is not editable" };
        ((ValuePattern)vp).SetValue(text);
        return new JsonObject { ["set"] = true };
    }
}
