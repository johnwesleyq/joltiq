// DEV/TEST ONLY — Linux headless host of the JoltIQ companion (see docs/runbooks/companion-headless.md).
// Real JoltIQ.Agent.Core + SIMULATED OS. The Windows tray (JoltIQ.Agent.Tray) is the product; this is its stand-in.
//
// Environment:
//   AGENT_SERVER_URL     API base (HTTPS, or http://localhost / http://127.0.0.1 for dev)   [http://localhost:8000]
//   AGENT_DEV_FORWARD    DEV: "host:port" to forward 127.0.0.1:<server port> to (compose: api:8000)  [off]
//   AGENT_DATA_DIR       device key + state (InsecureFileKeyStore, test only) and journal          [/var/lib/joltiq-companion]
//   AGENT_HOSTNAME       hostname reported at enrollment                                           [HEADLESS-SIM-01]
//   AGENT_ENROLL_CODE    optional enrollment code used at startup if not enrolled yet
//   AGENT_AUTO_CONSENT   "true" to auto-accept native consent (DEV demos only)                     [false]
//   AGENT_BROKEN_SERVICE "true": simulated service.restart leaves the service stopped              [false]
//   AGENT_POLL_MS        poll interval                                                             [1500]
//   AGENT_CONTROL_PORT   DEV control endpoint (what the person does in the tray on Windows)        [8400]
//   AGENT_CONTROL_KEY    X-Control-Key required by every control endpoint except /health (>= 16)  [required]
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using JoltIQ.Agent.Core;
using JoltIQ.Agent.Headless;

string Env(string k, string? fallback = null) =>
    Environment.GetEnvironmentVariable(k) is { Length: > 0 } v ? v : fallback ?? throw new InvalidOperationException($"{k} is required");
bool Flag(string k) => string.Equals(Environment.GetEnvironmentVariable(k), "true", StringComparison.OrdinalIgnoreCase);

var joltiqEnvironment = Env("JOLTIQ_ENV", "development");
if (string.Equals(joltiqEnvironment, "production", StringComparison.OrdinalIgnoreCase))
    throw new InvalidOperationException("The headless companion is a DEV/TEST fixture and refuses production.");

if (args.Contains("--healthcheck"))
{
    using var probe = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
    try { return (await probe.GetAsync($"http://127.0.0.1:{Env("AGENT_CONTROL_PORT", "8400")}/health")).IsSuccessStatusCode ? 0 : 1; }
    catch (HttpRequestException) { return 1; }
    catch (TaskCanceledException) { return 1; }
}
var serverUrl = Env("AGENT_SERVER_URL", "http://localhost:8000");
var dataDir = Env("AGENT_DATA_DIR", "/var/lib/joltiq-companion");
var hostname = Env("AGENT_HOSTNAME", "HEADLESS-SIM-01");
var controlKey = Env("AGENT_CONTROL_KEY");
if (controlKey.Length < 16) throw new InvalidOperationException("AGENT_CONTROL_KEY must be at least 16 characters");
Directory.CreateDirectory(dataDir);
if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(dataDir, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);

using var stopAll = new CancellationTokenSource();
var background = new List<Task>();
if (Environment.GetEnvironmentVariable("AGENT_DEV_FORWARD") is { Length: > 0 } fwd)
{
    var target = fwd.Split(':', 2);
    background.Add(DevForwarder.RunAsync(new Uri(serverUrl).Port, target[0], int.Parse(target[1], CultureInfo.InvariantCulture), stopAll.Token));
}

var store = new InsecureFileKeyStore(dataDir);
var sim = new SimulatedOs(Flag("AGENT_BROKEN_SERVICE"));
var ui = new HeadlessUi(Flag("AGENT_AUTO_CONSENT"));
using var client = new AgentClient(serverUrl, store);
var runtime = new AgentRuntime(client, sim, ui, new PersistentCommandJournal(store))
{
    PollInterval = TimeSpan.FromMilliseconds(int.Parse(Env("AGENT_POLL_MS", "1500"), CultureInfo.InvariantCulture)),
};
string? lastError = null;
var online = true;
CancellationTokenSource? loopCts = null;
Task? loop = null;
var gate = new object();

void StartLoop()
{
    lock (gate)
    {
        if (loop is { IsCompleted: false } || !client.Enrolled || !online) return;
        loopCts = CancellationTokenSource.CreateLinkedTokenSource(stopAll.Token);
        var token = loopCts.Token;
        loop = Task.Run(async () =>
        {
            try { await runtime.RunAsync(token); }
            catch (OperationCanceledException) { }
            catch (Exception e) { lastError = e.Message; }
        });
    }
}
void StopLoop() { lock (gate) loopCts?.Cancel(); }

async Task EnrollAsync(string code, CancellationToken ct)
{
    await client.EnrollAsync(code, hostname, "Linux headless stand-in (SIMULATED Windows state)",
        System.Runtime.InteropServices.RuntimeInformation.OSDescription, sim.Capabilities, ct: ct);
    lastError = null;
    StartLoop();
}

if (!client.Enrolled && Environment.GetEnvironmentVariable("AGENT_ENROLL_CODE") is { Length: > 0 } startupCode)
{
    try { await EnrollAsync(startupCode, stopAll.Token); } catch (Exception e) { lastError = $"startup enrollment failed: {e.Message}"; }
}
StartLoop();

var builder = WebApplication.CreateSlimBuilder(args);
builder.WebHost.UseUrls($"http://0.0.0.0:{Env("AGENT_CONTROL_PORT", "8400")}");
builder.Logging.SetMinimumLevel(LogLevel.Warning);
var app = builder.Build();

bool Authorized(HttpRequest r) => r.Headers.TryGetValue("X-Control-Key", out var k)
    && CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(k.ToString()), Encoding.UTF8.GetBytes(controlKey));
IResult Json(JsonNode n, int status = 200) => Results.Text(n.ToJsonString(), "application/json", Encoding.UTF8, status);
IResult Err(string detail, int status) => Json(new JsonObject { ["detail"] = detail }, status);
async Task<JsonObject> Body(HttpRequest r) => (await JsonNode.ParseAsync(r.Body)) as JsonObject ?? new JsonObject();

JsonObject Status()
{
    var log = new JsonArray();
    RuntimeLogEntry[] snap; lock (runtime.Log) snap = runtime.Log.ToArray();
    foreach (var l in snap.TakeLast(30)) log.Add($"{l.At:o} {l.Level}: {Redactor.Redact(l.Message)}");
    var pending = new JsonArray();
    foreach (var p in ui.PendingConsents) pending.Add(new JsonObject { ["session_id"] = p.SessionId, ["title"] = p.Request.Title, ["body"] = p.Request.Body, ["asked_at"] = p.AskedAt.ToString("o") });
    return new JsonObject
    {
        ["label"] = "DEV/TEST: Linux headless host of the JoltIQ companion (real C# core, SIMULATED Windows state)",
        ["enrolled"] = client.Enrolled,
        ["device_id"] = client.State.DeviceId,
        ["org_id"] = client.State.OrgId,
        ["counter"] = client.State.Counter,
        ["online"] = online,
        ["polling"] = loop is { IsCompleted: false },
        ["auto_consent"] = ui.AutoConsent,
        ["last_error"] = lastError,
        ["pending_consents"] = pending,
        ["active_sessions"] = new JsonArray([.. ui.ActiveSessions.Select(s => (JsonNode)s)]),
        ["simulated_actions_executed"] = new JsonArray([.. sim.Executed.ToArray().Select(s => (JsonNode)s)]),
        ["ui_events"] = new JsonArray([.. ui.Events.TakeLast(30).Select(s => (JsonNode)s)]),
        ["runtime_log"] = log,
    };
}

app.MapGet("/health", () => Json(new JsonObject { ["status"] = "ok", ["enrolled"] = client.Enrolled }));
app.MapGet("/status", IResult (HttpRequest r) => Authorized(r) ? Json(Status()) : Err("control key required", 401));
app.MapPost("/enroll", async Task<IResult> (HttpRequest r) =>
{
    if (!Authorized(r)) return Err("control key required", 401);
    var code = (await Body(r))["code"]?.GetValue<string>() ?? "";
    try { await EnrollAsync(code, r.HttpContext.RequestAborted); }
    catch (Exception e) when (e is AgentProtocolException or InvalidOperationException or HttpRequestException) { lastError = e.Message; return Err(e.Message, 400); }
    return Json(Status());
});
app.MapPost("/consent/{sessionId}", async Task<IResult> (HttpRequest r, string sessionId) =>
{
    if (!Authorized(r)) return Err("control key required", 401);
    var accept = (await Body(r))["accept"]?.GetValue<bool>() == true;
    return ui.Answer(sessionId, accept) ? Json(Status()) : Err("no pending consent for that session", 404);
});
app.MapPost("/stop/{sessionId}", IResult (HttpRequest r, string sessionId) =>
{
    if (!Authorized(r)) return Err("control key required", 401);
    return ui.Stop(sessionId) ? Json(Status()) : Err("no active session with that id", 404);
});
app.MapPost("/network", async Task<IResult> (HttpRequest r) =>
{
    if (!Authorized(r)) return Err("control key required", 401);
    online = (await Body(r))["online"]?.GetValue<bool>() != false;
    if (online) StartLoop(); else StopLoop();
    return Json(Status());
});
app.MapPost("/forget", IResult (HttpRequest r) =>
{
    if (!Authorized(r)) return Err("control key required", 401);
    StopLoop();
    client.ForgetEnrollment();
    return Json(Status());
});

await app.RunAsync(stopAll.Token);
await stopAll.CancelAsync();
StopLoop();
return 0;

