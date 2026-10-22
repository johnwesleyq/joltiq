using System.Diagnostics;
using System.Diagnostics.Eventing.Reader;
using System.IO;
using System.IO.Pipes;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
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
    private const uint WmNull = 0x0000;
    private const uint SmtoBlock = 0x0001;
    private const uint SmtoAbortIfHung = 0x0002;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SendMessageTimeout(
        IntPtr window,
        uint message,
        UIntPtr wParam,
        IntPtr lParam,
        uint flags,
        uint timeoutMilliseconds,
        out UIntPtr result);

    public const string BrokerPipe = "JoltIQ.Agent.Broker.v1";
    private readonly string? _enrolledUserSid;
    private readonly OfficeAddinManager _addins;
    private readonly TimeSpan _restartTimeout;
    private readonly TimeSpan _restartSettle;

    public WindowsActionProvider(
        string? enrolledUserSid = null,
        ICommandJournal? journal = null,
        ActionHost host = ActionHost.InteractiveTray,
        Func<string?>? currentSid = null,
        TimeSpan? restartTimeout = null,
        TimeSpan? restartSettle = null)
    {
        _enrolledUserSid = enrolledUserSid;
        _addins = new OfficeAddinManager(enrolledUserSid, journal, host, currentSid);
        _restartTimeout = restartTimeout ?? TimeSpan.FromSeconds(20);
        _restartSettle = restartSettle ?? TimeSpan.FromSeconds(4);
        var capabilities = new List<string>
        {
            "system.info", "process.list", "app.info", "disk.usage", "network.diagnostics", "eventlog.query", "service.status",
            "outlook.pdf_crash.inspect", "file.inspect", "config.read", "app.restart", "service.restart", "ui.invoke", "ui.set_text",
        };
        if (_addins.Available)
            capabilities.AddRange(new[] { "office.addin.set_load_behavior", "office.addin.restore_load_behavior", "office.addin.verify_restored" });
        Capabilities = capabilities;
    }

    public IReadOnlyCollection<string> Capabilities { get; }

    public IDictionary<string, string> Facts() => new Dictionary<string, string>
    {
        ["os"] = Environment.OSVersion.VersionString, ["machine"] = Environment.MachineName, ["is64"] = Environment.Is64BitOperatingSystem.ToString(),
        ["build"] = Registry.GetValue(@"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion", "")?.ToString() ?? "",
    };

    public async Task<ActionResult> ExecuteAsync(string action, JsonElement p, CancellationToken ct, ActionExecutionContext? context = null)
    {
        string S(string k) => p.GetProperty(k).GetString()!;
        string? O(string k) => p.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        int I(string k, int d) => p.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : d;
        var data = action switch
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
            "office.addin.set_load_behavior" => _addins.SetLoadBehavior(S("app"), S("addin_progid"), p.GetProperty("enabled").GetBoolean(), context),
            "office.addin.restore_load_behavior" => _addins.Restore(S("source_command_id"), context),
            "office.addin.verify_restored" => _addins.VerifyRestored(S("source_command_id"), context),
            "service.restart" => await Broker(new JsonObject { ["op"] = "service.restart", ["name"] = S("name") }, ct),
            "ui.invoke" => UiInvoke(S("process"), O("automation_id"), O("name"), O("control_type")),
            "ui.set_text" => UiSetText(S("process"), S("automation_id"), S("text")),
            _ => new JsonObject { ["ok"] = false, ["error"] = "unsupported" },
        };
        return new ActionResult(data);
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
                var mainWindow = pr.MainWindowHandle != IntPtr.Zero;
                arr.Add(new JsonObject { ["name"] = pr.ProcessName + ".exe", ["pid"] = pr.Id, ["memory_mb"] = pr.WorkingSet64 / 1048576,
                                         ["main_window"] = mainWindow, ["responding"] = mainWindow && pr.Responding });
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
        using var root = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64);
        using var k = root.OpenSubKey(sub);
        if (k is null || !k.GetValueNames().Contains(value, StringComparer.OrdinalIgnoreCase))
            return new JsonObject { ["exists"] = false };
        var data = k.GetValue(value, null, RegistryValueOptions.DoNotExpandEnvironmentNames);
        if (data is null) return new JsonObject { ["exists"] = false };
        return new JsonObject { ["exists"] = true, ["data"] = data.ToString(), ["kind"] = k.GetValueKind(value).ToString(),
                                ["registry_view"] = "shared" };
    }

    private async Task<JsonObject> RestartApp(string proc, string? args, CancellationToken ct)
    {
        var name = Path.GetFileNameWithoutExtension(proc);
        string? exe = null;
        var initial = Process.GetProcessesByName(name);
        var wasRunning = initial.Length > 0;
        var closeRequested = wasRunning;
        foreach (var process in initial)
        {
            try
            {
                exe ??= process.MainModule?.FileName;
                closeRequested &= process.MainWindowHandle != IntPtr.Zero && process.CloseMainWindow();
            }
            catch (Exception e) when (e is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                closeRequested = false;
            }
            finally { process.Dispose(); }
        }
        var deadline = DateTime.UtcNow.Add(_restartTimeout);
        while (HasProcesses(name) && DateTime.UtcNow < deadline) await Task.Delay(100, ct);
        if (HasProcesses(name))
            return RestartResult(false, wasRunning, closeRequested, false, false, false,
                "application did not close normally (the user may have unsaved work); it was not forced");
        exe ??= AppPathFromRegistry(LocalPolicy.Normalize(proc));
        if (exe is null || !File.Exists(exe))
            return RestartResult(false, wasRunning, closeRequested, false, false, false, "could not locate application executable");
        try
        {
            using var startedProcess = Process.Start(new ProcessStartInfo(exe, args ?? "") { UseShellExecute = true });
            if (startedProcess is null)
                return RestartResult(false, wasRunning, closeRequested, false, false, false, "application launch returned no process");
        }
        catch (Exception e) when (e is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            return RestartResult(false, wasRunning, closeRequested, false, false, false, "application could not be started safely");
        }
        var settleDeadline = DateTime.UtcNow.Add(_restartSettle);
        var responding = false;
        do
        {
            responding = HasRespondingWindow(name);
            if (!responding) await Task.Delay(100, ct);
        } while (!responding && DateTime.UtcNow < settleDeadline);
        return RestartResult(responding, wasRunning, closeRequested, !wasRunning, wasRunning, responding,
            responding ? null : "application started but no responding main window appeared");
    }

    private static bool HasProcesses(string name)
    {
        var processes = Process.GetProcessesByName(name);
        try { return processes.Length > 0; }
        finally { foreach (var process in processes) process.Dispose(); }
    }

    private static bool HasRespondingWindow(string name)
    {
        var processes = Process.GetProcessesByName(name);
        try
        {
            return processes.Any(process =>
            {
                try
                {
                    var window = process.MainWindowHandle;
                    if (window == IntPtr.Zero || !process.Responding) return false;
                    return SendMessageTimeout(
                        window,
                        WmNull,
                        UIntPtr.Zero,
                        IntPtr.Zero,
                        SmtoBlock | SmtoAbortIfHung,
                        250,
                        out _) != IntPtr.Zero;
                }
                catch (InvalidOperationException) { return false; }
            });
        }
        finally { foreach (var process in processes) process.Dispose(); }
    }

    private static JsonObject RestartResult(bool ok, bool wasRunning, bool closeRequested, bool started, bool restarted,
        bool responding, string? error)
    {
        var result = new JsonObject
        {
            ["ok"] = ok,
            ["was_running"] = wasRunning,
            ["close_requested"] = closeRequested,
            ["started"] = started,
            ["restarted"] = restarted,
            ["responding"] = responding,
            ["safe"] = ok,
        };
        if (error is not null) result["error"] = error;
        return result;
    }

    private static string? AppPathFromRegistry(string exe)
    {
        var path = $@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}";
        foreach (var hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            using var root = RegistryKey.OpenBaseKey(hive, view);
            using var key = root.OpenSubKey(path);
            if (key?.GetValue(null) is string value) return value;
        }
        return null;
    }

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
