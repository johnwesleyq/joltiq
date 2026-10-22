// TEST FIXTURE — JoltIQ companion protocol simulator.
// Uses the REAL AgentClient/AgentRuntime/LocalPolicy/crypto with a SIMULATED operating system, so the server <-> device
// protocol can be exercised end-to-end on any OS (CI on Linux). It does not touch the host machine.
//
// Usage:
//   sim enroll <serverUrl> <code> <stateDir>
//   sim run <serverUrl> <stateDir> <seconds> [--decline] [--stop-after-first-action] [--broken-service]
using System.Text.Json;
using System.Text.Json.Nodes;
using JoltIQ.Agent.Core;

if (args.Length < 1) { Console.Error.WriteLine("usage: enroll|run ..."); return 2; }
var sim = new SimulatedOs(args.Contains("--broken-service"));
if (args[0] == "enroll")
{
    using var c = new AgentClient(args[1], new InsecureFileKeyStore(args[3]));
    await c.EnrollAsync(args[2], "SIM-PC-01", "Simulated Windows 11 Pro (TEST FIXTURE)", "10.0.22631", sim.Capabilities);
    Console.WriteLine(JsonSerializer.Serialize(new { device_id = c.State.DeviceId, org_id = c.State.OrgId }));
    return 0;
}
if (args[0] == "run")
{
    using var c = new AgentClient(args[1], new InsecureFileKeyStore(args[2]));
    var ui = new AutoUi(!args.Contains("--decline"));
    var rt = new AgentRuntime(c, sim, ui, new InMemoryCommandJournal()) { PollInterval = TimeSpan.FromMilliseconds(400) };
    ui.Runtime = rt;
    ui.StopAfterFirstAction = args.Contains("--stop-after-first-action");
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(int.Parse(args[3])));
    await rt.RunAsync(cts.Token);
    foreach (var l in rt.Log) Console.WriteLine($"{l.Level}: {l.Message}");
    Console.WriteLine($"actions_executed={sim.Executed.Count} {string.Join(',', sim.Executed)}");
    return 0;
}
return 2;

sealed class AutoUi(bool accept) : IUserInteraction
{
    public AgentRuntime? Runtime;
    public bool StopAfterFirstAction;
    public Task<bool> AskConsentAsync(string sessionId, NativeConsentRequest request, TimeSpan timeout, CancellationToken ct)
        => Task.FromResult(accept);
    public void SessionStarted(string sessionId, string activeMessage, Action stopRequested)
        => Console.WriteLine($"[banner] {activeMessage}");
    public void SessionEnded(string sessionId, string reason) => Console.WriteLine($"[banner] session {sessionId} ended: {reason}");
    public void ActionPerformed(string sessionId, string summary)
    {
        Console.WriteLine($"[toast] {summary}");
        if (StopAfterFirstAction) _ = Runtime!.StopFromDeviceAsync(sessionId);
    }
    public string CurrentUserDisplayName => "Simulated User";
}

/// <summary>Deterministic fake OS state. Outlook "crashes" while add-in PdfPreview.Connect is enabled.</summary>
sealed class SimulatedOs(bool brokenService) : IActionProvider
{
    public readonly List<string> Executed = new();
    private readonly Dictionary<string, int> _addins = new() { ["PdfPreview.Connect"] = 3, ["Teams.Meeting.Addin"] = 3 };
    private readonly Dictionary<string, string> _services = new() { ["Spooler"] = "Stopped", ["BITS"] = "Running" };
    private bool _outlookResponding;

    public IReadOnlyCollection<string> Capabilities { get; } = new[]
        { "system.info", "process.list", "app.info", "disk.usage", "eventlog.query", "outlook.pdf_crash.inspect", "service.status", "service.restart", "app.restart",
          "office.addin.set_load_behavior", "config.read", "network.diagnostics" };

    public IDictionary<string, string> Facts() => new Dictionary<string, string> { ["simulated"] = "true", ["os"] = "Windows 11 23H2 (simulated)" };

    public Task<ActionResult> ExecuteAsync(string action, JsonElement p, CancellationToken ct, ActionExecutionContext? context = null)
    {
        Executed.Add(action);
        string S(string k) => p.GetProperty(k).GetString()!;
        JsonObject r = action switch
        {
            "system.info" => new() { ["os"] = "Windows 11 Pro 23H2 (simulated)", ["uptime_hours"] = 51, ["ram_gb"] = 16 },
            "app.info" => new() { ["apps"] = new JsonArray(new JsonObject { ["name"] = "Microsoft 365 Apps - Outlook", ["version"] = "16.0.17928 (2408)" }) },
            "eventlog.query" => new() { ["events"] = new JsonArray(new JsonObject {
                ["provider"] = "Application Error", ["id"] = 1000, ["level"] = "Error",
                ["message"] = "Faulting application OUTLOOK.EXE, faulting module PdfPreview.dll, exception code 0xc0000005" }) },
            "outlook.pdf_crash.inspect" => InspectOutlook(),
            "process.list" => new() { ["processes"] = new JsonArray(new JsonObject { ["name"] = "OUTLOOK.EXE", ["pid"] = 4242,
                                                                                       ["main_window"] = _outlookResponding, ["responding"] = _outlookResponding }) },
            "service.status" => new() { ["name"] = S("name"), ["status"] = _services.GetValueOrDefault(S("name"), "NotFound") },
            "service.restart" => Restart(S("name")),
            "app.restart" => new() { ["was_running"] = true, ["close_requested"] = true, ["started"] = false,
                                      ["restarted"] = true, ["responding"] = true, ["safe"] = true, ["note"] = "simulated" },
            "office.addin.set_load_behavior" => SetAddin(S("addin_progid"), p.GetProperty("enabled").GetBoolean()),
            "config.read" => ReadConfig(S("key")),
            "network.diagnostics" => new() { ["gateway_reachable"] = true, ["dns_ok"] = true },
            _ => new() { ["ok"] = false, ["error"] = "unsupported" },
        };
        if (action == "app.restart") _outlookResponding = _addins.GetValueOrDefault("PdfPreview.Connect") != 3;
        return Task.FromResult(new ActionResult(r));
    }

    private JsonObject Restart(string name)
    {
        _services[name] = brokenService ? "Stopped" : "Running";
        return new() { ["requested"] = name };
    }

    private JsonObject InspectOutlook()
    {
        var crashMatched = _addins.GetValueOrDefault("PdfPreview.Connect") == 3;
        return new()
        {
            ["supported"] = crashMatched, ["unsupported_reasons"] = crashMatched ? new JsonArray() : new JsonArray("matching_crash_not_found"),
            ["windows"] = new JsonObject { ["supported"] = true, ["version"] = "Windows 11 simulated" },
            ["interactive"] = new JsonObject { ["supported"] = true, ["unlocked"] = true, ["same_user"] = true, ["active_session_count"] = 1 },
            ["outlook"] = new JsonObject { ["kind"] = "classic", ["classic_installed"] = true, ["new_installed"] = false },
            ["addin"] = new JsonObject { ["installed"] = true, ["scope"] = "HKCU", ["registry_view"] = "64",
                                           ["original_value"] = _addins["PdfPreview.Connect"], ["original_kind"] = "DWord",
                                           ["policy_override"] = false, ["changeable"] = true },
            ["crash"] = new JsonObject { ["log_readable"] = true, ["log_enabled"] = true, ["service_running"] = true,
                                          ["window_trustworthy"] = true, ["matched"] = crashMatched,
                                          ["matching_event_count"] = crashMatched ? 1 : 0 },
        };
    }

    private JsonObject SetAddin(string id, bool enabled)
    {
        _addins[id] = enabled ? 3 : 2;
        return new() { ["addin"] = id, ["load_behavior"] = _addins[id] };
    }

    private JsonObject ReadConfig(string key)
    {
        var id = key.Split('\\').Last();
        return _addins.TryGetValue(id, out var v) ? new() { ["data"] = v } : new() { ["ok"] = false, ["error"] = "not found" };
    }
}
