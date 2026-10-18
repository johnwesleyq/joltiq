using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.Json.Nodes;

var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddWindowsService(o => o.ServiceName = "JoltIQBroker");
builder.Services.AddHostedService<BrokerService>();
builder.Build().Run();

/// <summary>Privileged broker. The allowlist is fixed at install time (HKLM\SOFTWARE\JoltIQ\Broker\AllowedServices,
/// admin-writable only) and defaults to a small set of safe-to-restart services.</summary>
sealed class BrokerService(ILogger<BrokerService> log) : BackgroundService
{
    private const string Pipe = "JoltIQ.Agent.Broker.v1";
    private static readonly string[] Defaults = { "Spooler", "wuauserv", "BITS", "Dnscache", "W32Time", "WSearch", "AudioSrv" };

    private static HashSet<string> Allowed()
    {
        using var k = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\JoltIQ\Broker");
        var v = k?.GetValue("AllowedServices") as string[];
        return new HashSet<string>(v is { Length: > 0 } ? v : Defaults, StringComparer.OrdinalIgnoreCase);
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            var sec = new PipeSecurity();
            sec.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.InteractiveSid, null), PipeAccessRights.ReadWrite, AccessControlType.Allow));
            sec.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
            sec.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null), PipeAccessRights.FullControl, AccessControlType.Deny));
            await using var server = NamedPipeServerStreamAcl.Create(Pipe, PipeDirection.InOut, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 4096, 4096, sec);
            await server.WaitForConnectionAsync(ct);
            try
            {
                using var reader = new StreamReader(server, Encoding.UTF8, leaveOpen: true);
                var line = await reader.ReadLineAsync(ct);
                var resp = Handle(line);
                var bytes = Encoding.UTF8.GetBytes(resp.ToJsonString() + "\n");
                await server.WriteAsync(bytes, ct);
                await server.FlushAsync(ct);
            }
            catch (Exception e) when (e is not OperationCanceledException) { log.LogWarning("broker request failed: {msg}", e.Message); }
        }
    }

    private JsonObject Handle(string? line)
    {
        if (line is null || line.Length > 2000) return Fail("bad request");
        var req = JsonNode.Parse(line)?.AsObject();
        if (req?["op"]?.GetValue<string>() != "service.restart") return Fail("operation not allowed");
        var name = req["name"]?.GetValue<string>() ?? "";
        if (!Allowed().Contains(name)) return Fail("service not on the broker allowlist");
        using var sc = new ServiceController(name);
        log.LogInformation("restarting service {name} on request of interactive user", name);
        if (sc.Status != ServiceControllerStatus.Stopped) { sc.Stop(); sc.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(30)); }
        sc.Start();
        sc.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(30));
        return new JsonObject { ["ok"] = true, ["name"] = name, ["status"] = sc.Status.ToString() };
    }

    private static JsonObject Fail(string m) => new() { ["ok"] = false, ["error"] = m };
}
