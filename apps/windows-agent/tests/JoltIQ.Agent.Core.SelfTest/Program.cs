using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using JoltIQ.Agent.Core;

var failures = 0;
var passed = 0;
void T(string name, Action body)
{
    try { body(); passed++; Console.WriteLine($"PASS {name}"); }
    catch (Exception e) { failures++; Console.WriteLine($"FAIL {name}: {e.Message}"); }
}
async Task TA(string name, Func<Task> body)
{
    try { await body(); passed++; Console.WriteLine($"PASS {name}"); }
    catch (Exception e) { failures++; Console.WriteLine($"FAIL {name}: {e.Message}"); }
}
void Assert(bool c, string m = "assertion failed") { if (!c) throw new Exception(m); }
JsonElement J(string s) => JsonDocument.Parse(s).RootElement;
var active = new LocalSession { SessionId = "s1", Active = true, ExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 600 };

T("string-to-sign matches server format", () =>
{
    var s = Encoding.UTF8.GetString(RequestSigning.StringToSign("post", "/api/v1/agent/v1/poll", "100", "abc", "7", Encoding.UTF8.GetBytes("{}")));
    Assert(s == "POST\n/api/v1/agent/v1/poll\n100\nabc\n7\n44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a", s);
});
T("device signature verifies with exported public key", () =>
{
    using var k = DeviceKey.Create();
    var msg = Encoding.UTF8.GetBytes("hello");
    using var pub = ECDsa.Create();
    pub.ImportFromPem(k.PublicKeyPem());
    Assert(pub.VerifyData(msg, Convert.FromBase64String(k.Sign(msg)), HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence));
    using var k2 = DeviceKey.FromPkcs8(k.ExportPkcs8());
    Assert(k2.PublicKeyPem() == k.PublicKeyPem(), "pkcs8 round trip");
});
T("envelope verifier rejects tampered payload and foreign key", () =>
{
    using var server = ECDsa.Create(ECCurve.NamedCurves.nistP256);
    var payload = Encoding.UTF8.GetBytes("{\"type\":\"action\"}");
    var sig = Convert.ToBase64String(server.SignData(payload, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence));
    using var v = new EnvelopeVerifier(server.ExportSubjectPublicKeyInfoPem());
    Assert(v.Verify(payload, sig), "valid");
    Assert(!v.Verify(Encoding.UTF8.GetBytes("{\"type\":\"actioN\"}"), sig), "tampered accepted");
    using var other = ECDsa.Create(ECCurve.NamedCurves.nistP256);
    using var v2 = new EnvelopeVerifier(other.ExportSubjectPublicKeyInfoPem());
    Assert(!v2.Verify(payload, sig), "foreign key accepted");
    Assert(!v.Verify(payload, "not-base64!!"), "garbage sig accepted");
});
T("replay guard: nonce reuse, expiry, age, future", () =>
{
    long now = 1_000_000;
    var g = new ReplayGuard(() => now);
    Assert(g.Check("n1", now, now + 60).ok);
    Assert(!g.Check("n1", now, now + 60).ok, "replay accepted");
    Assert(!g.Check("n2", now - 700, null).ok, "old accepted");
    Assert(!g.Check("n3", now, now - 1).ok, "expired accepted");
    Assert(!g.Check("n4", now + 500, null).ok, "future accepted");
    Assert(!g.Check(null, now, null).ok, "missing nonce accepted");
});
T("policy: requires active local session", () =>
{
    var p = new LocalPolicy();
    Assert(!p.Evaluate("system.info", J("{}"), null).Allowed);
    Assert(!p.Evaluate("system.info", J("{}"), new LocalSession { SessionId = "x", Active = false }).Allowed);
    Assert(!p.Evaluate("system.info", J("{}"), new LocalSession { SessionId = "x", Active = true, ExpiresAt = 1 }).Allowed, "expired");
    Assert(p.Evaluate("system.info", J("{}"), active).Allowed);
});
T("policy: no shell or unknown actions", () =>
{
    var p = new LocalPolicy();
    foreach (var a in new[] { "shell.exec", "powershell", "file.write", "registry.write", "delete.anything", "credential.read" })
        Assert(!p.Evaluate(a, J("{}"), active).Allowed, a);
});
T("policy: native input requires a server-bound frame and Fix mode", () =>
{
    var p = new LocalPolicy();
    var fix = new LocalSession
    {
        SessionId = "fix",
        Active = true,
        ComputerUseMode = "fix",
        ExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 60,
    };
    var guide = new LocalSession
    {
        SessionId = "guide",
        Active = true,
        ComputerUseMode = "guide",
        ExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 60,
    };
    var bound = J("{\"x\":10,\"y\":20,\"frame_generation\":7,\"layout_hash\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}");
    Assert(p.Evaluate("input.click", bound, fix).Allowed, "valid Fix input was refused");
    Assert(!p.Evaluate("input.click", J("{\"x\":10,\"y\":20}"), fix).Allowed, "unbound input was accepted");
    Assert(!p.Evaluate("input.click", bound, guide).Allowed, "Guide mode injected input");
});
T("policy: app/service allowlists", () =>
{
    var p = new LocalPolicy();
    Assert(p.Evaluate("app.restart", J("{\"process\":\"OUTLOOK.EXE\"}"), active).Allowed);
    Assert(!p.Evaluate("app.restart", J("{\"process\":\"lsass.exe\"}"), active).Allowed, "lsass");
    Assert(!p.Evaluate("app.restart", J("{\"process\":\"outlook.exe\",\"args\":\"/cleanprofile\"}"), active).Allowed, "args");
    Assert(p.Evaluate("service.restart", J("{\"name\":\"Spooler\"}"), active).Allowed);
    Assert(!p.Evaluate("service.restart", J("{\"name\":\"WinDefend\"}"), active).Allowed, "defender");
    Assert(!p.Evaluate("service.restart", J("{\"name\":\"a;b\"}"), active).Allowed, "injection");
});
T("policy: file + registry scoping", () =>
{
    var p = new LocalPolicy { ExpandEnv = s => s.Replace("%LOCALAPPDATA%", @"C:\Users\u\AppData\Local").Replace("%TEMP%", @"C:\T").Replace("%APPDATA%", @"C:\A").Replace("%ProgramData%", @"C:\PD") };
    Assert(p.Evaluate("file.inspect", J("{\"path\":\"%LOCALAPPDATA%\\\\Microsoft\\\\Outlook\\\\log.txt\"}"), active).Allowed);
    Assert(!p.Evaluate("file.inspect", J("{\"path\":\"%LOCALAPPDATA%\\\\Microsoft\\\\Outlook\\\\mail.ost\"}"), active).Allowed, "ost");
    Assert(!p.Evaluate("file.inspect", J("{\"path\":\"%LOCALAPPDATA%\\\\Microsoft\\\\Outlook\\\\..\\\\..\\\\x\"}"), active).Allowed, "traversal");
    Assert(!p.Evaluate("file.inspect", J("{\"path\":\"C:\\\\Windows\\\\System32\\\\config\\\\SAM\"}"), active).Allowed, "SAM");
    Assert(!p.Evaluate("file.inspect", J("{\"path\":\"\\\\\\\\server\\\\share\\\\x\"}"), active).Allowed, "UNC");
    Assert(p.Evaluate("config.read", J("{\"key\":\"HKCU\\\\Software\\\\Microsoft\\\\Office\\\\Outlook\\\\Addins\\\\X\",\"value\":\"LoadBehavior\"}"), active).Allowed);
    Assert(!p.Evaluate("config.read", J("{\"key\":\"HKLM\\\\SAM\\\\SAM\",\"value\":\"F\"}"), active).Allowed, "HKLM");
});
T("policy: Outlook PDF inspection is typed and narrowly scoped", () =>
{
    var p = new LocalPolicy();
    Assert(p.Evaluate("outlook.pdf_crash.inspect", J("{\"addin_progid\":\"PdfPreview.Connect\",\"faulting_module\":\"PdfPreview.dll\",\"lookback_hours\":24,\"since\":\"2026-10-20T12:00:00Z\"}"), active).Allowed);
    foreach (var bad in new[]
    {
        "{}",
        "{\"addin_progid\":\"PdfPreview.Connect\",\"faulting_module\":\"PdfPreview.dll\",\"lookback_hours\":0}",
        "{\"addin_progid\":\"..\\\\Bad\",\"faulting_module\":\"PdfPreview.dll\",\"lookback_hours\":24}",
        "{\"addin_progid\":\"PdfPreview.Connect\",\"faulting_module\":\"C:\\\\evil.dll\",\"lookback_hours\":24}",
        "{\"addin_progid\":\"PdfPreview.Connect\",\"faulting_module\":\"PdfPreview.dll\",\"lookback_hours\":24,\"log\":\"Security\"}",
    }) Assert(!p.Evaluate("outlook.pdf_crash.inspect", J(bad), active).Allowed, bad);
});
T("redactor scrubs secrets and secret-named keys", () =>
{
    var n = Redactor.RedactNode(JsonNode.Parse("{\"password\":\"x\",\"msg\":\"user bob@acme.com at C:\\\\Users\\\\bob\\\\a token=abc123\",\"list\":[\"4111 1111 1111 1111\"]}"))!;
    var s = n.ToJsonString();
    Assert(!s.Contains("bob@acme.com") && !s.Contains("abc123") && !s.Contains("4111") && !s.Contains("Users\\\\bob") && s.Contains("[REDACTED]"), s);
});
T("client refuses plain HTTP to non-local servers", () =>
{
    try { using var _ = new AgentClient("http://evil.example.com", new InsecureFileKeyStore(Path.GetTempPath())); throw new Exception("accepted"); }
    catch (ArgumentException) { }
});

T("command journal prevents duplicate execution and acknowledges the durable outbox", () =>
{
    var journal = new InMemoryCommandJournal();
    var context = new ActionExecutionContext("cmd-1", "device-1", "session-1", "user-1", "procedure-1");
    Assert(journal.MarkRunning(context, "system.info"), "first dispatch was refused");
    Assert(!journal.MarkRunning(context, "system.info"), "duplicate dispatch was accepted");
    Assert(journal.MarkCompleted("cmd-1", "done", JsonNode.Parse("{\"ok\":true,\"value\":7}")!.AsObject()), "completion was not saved");
    Assert(journal.TryGetCommand("cmd-1", out var saved) && saved.State == LocalCommandState.Completed, "completion missing");
    Assert(saved.Result?["value"]?.GetValue<int>() == 7, "wrong cached result");
    Assert(journal.PendingReports().Count == 1, "pending report missing");
    Assert(journal.AcknowledgeReport("cmd-1"), "acknowledgement failed");
    Assert(journal.PendingReports().Count == 0, "acknowledged report remained pending");
});

T("command journal protects screenshot evidence across restart and deletes it after acknowledgement", () =>
{
    var store = new MemoryKeyStore();
    var first = new PersistentCommandJournal(store);
    var context = new ActionExecutionContext("cmd-screen", "device-1", "session-1", "user-1", "procedure-1");
    var screenshot = new byte[] { 137, 80, 78, 71, 13, 10, 26, 10, 1, 2, 3, 4 };
    Assert(first.MarkRunning(context, "screen.capture"));
    Assert(first.MarkCompleted(
        "cmd-screen",
        "done",
        JsonNode.Parse("{\"ok\":true}")!.AsObject(),
        new CommandEvidence("image/png", screenshot)), "evidence completion was not persisted");

    var restarted = new PersistentCommandJournal(store);
    var report = restarted.PendingReports().Single();
    Assert(report.Evidence is not null, "evidence metadata was lost on restart");
    Assert(restarted.TryLoadEvidence(report.CommandId, out var loaded, out var error), error ?? "evidence could not be loaded");
    Assert(loaded is not null && loaded.MediaType == "image/png" && loaded.Bytes.SequenceEqual(screenshot), "evidence changed on disk");
    var evidenceName = report.Evidence!.StoreName;
    Assert(store.Load(evidenceName) is not null, "evidence was not retained before server acknowledgement");
    Assert(restarted.AcknowledgeReport(report.CommandId), "acknowledgement failed");
    Assert(store.Load(evidenceName) is null, "acknowledged evidence remained on disk");
});

T("missing or corrupt screenshot evidence is downgraded to an unknown failed outcome", () =>
{
    var store = new MemoryKeyStore();
    var journal = new PersistentCommandJournal(store);
    var context = new ActionExecutionContext("cmd-corrupt", "device-1", "session-1", "user-1", "procedure-1");
    Assert(journal.MarkRunning(context, "input.click"));
    Assert(journal.MarkCompleted(
        "cmd-corrupt",
        "done",
        JsonNode.Parse("{\"ok\":true}")!.AsObject(),
        new CommandEvidence("image/png", new byte[] { 1, 2, 3, 4 })));
    var evidenceName = journal.PendingReports().Single().Evidence!.StoreName;
    store.Save(evidenceName, new byte[] { 9, 9, 9 });

    Assert(!journal.TryLoadEvidence("cmd-corrupt", out _, out var error) && error is not null, "corruption was accepted");
    Assert(journal.MarkEvidenceUnavailable("cmd-corrupt", error!), "corrupt evidence was not failed closed");
    var failed = journal.PendingReports().Single();
    Assert(failed.Status == "failed" && failed.Evidence is null, "corrupt evidence retained a success report");
    Assert(failed.Result["outcome"]?.GetValue<string>() == "unknown", "uncertain outcome was hidden");
});

T("malformed screenshot hash metadata fails closed without crashing reconciliation", () =>
{
    var store = new MemoryKeyStore();
    var journal = new PersistentCommandJournal(store);
    var context = new ActionExecutionContext("cmd-bad-hash", "device-1", "session-1", "user-1", "procedure-1");
    Assert(journal.MarkRunning(context, "input.click"));
    Assert(journal.MarkCompleted(
        "cmd-bad-hash",
        "done",
        JsonNode.Parse("{\"ok\":true}")!.AsObject(),
        new CommandEvidence("image/png", new byte[] { 1, 2, 3, 4 })));

    var document = JsonNode.Parse(store.Load("command-journal.v1.json")!)!.AsObject();
    document["Commands"]!["cmd-bad-hash"]!["Evidence"]!["Sha256"] = "not-hex";
    store.Save("command-journal.v1.json", Encoding.UTF8.GetBytes(document.ToJsonString()));
    var restarted = new PersistentCommandJournal(store);

    Assert(!restarted.TryLoadEvidence("cmd-bad-hash", out _, out var error) && error is not null,
        "malformed evidence metadata was accepted");
});

T("persistent journal survives restart with result and exact typed rollback state", () =>
{
    var dir = Path.Combine(Path.GetTempPath(), "joltiq-journal-" + Guid.NewGuid().ToString("N"));
    try
    {
        var store = new InsecureFileKeyStore(dir);
        var first = new PersistentCommandJournal(store);
        var context = new ActionExecutionContext("cmd-persist", "device-1", "session-1", "user-1", "procedure-1");
        Assert(first.MarkRunning(context, "office.addin.set_load_behavior"));
        var before = new RegistryValueSnapshot(true, @"Software\Microsoft\Office\Outlook\Addins\Fixture", "LoadBehavior", "32",
            "DWord", JsonValue.Create(3));
        Assert(first.TrySaveMutation(new MutationRecord("cmd-persist", "device-1", "session-1", "user-1", "procedure-1",
            "office.addin.set_load_behavior", before)), "typed pre-state was not saved");
        Assert(first.MarkCompleted("cmd-persist", "done", JsonNode.Parse("{\"ok\":true}")!.AsObject()));

        var restarted = new PersistentCommandJournal(store);
        Assert(restarted.PendingReports().Single().CommandId == "cmd-persist", "pending report was lost on restart");
        Assert(restarted.TryGet("cmd-persist", out var mutation), "rollback record was lost on restart");
        Assert(mutation.Before.RegistryView == "32" && mutation.Before.Kind == "DWord" &&
               mutation.Before.Value?.GetValue<int>() == 3, "typed rollback state changed");
        Assert(restarted.TryMarkMutationApplied("cmd-persist", before with { Value = JsonValue.Create(2) }));
        Assert(restarted.TryMarkRestored("cmd-persist", "restore-1"), "first restore link was refused");
        Assert(!restarted.TryMarkRestored("cmd-persist", "restore-2"), "rollback record was reused");
    }
    finally { if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true); }
});

T("process restart converts interrupted running command into pending unknown-outcome evidence", () =>
{
    var store = new MemoryKeyStore();
    var first = new PersistentCommandJournal(store);
    var context = new ActionExecutionContext("interrupted", "device-1", "session-1", "user-1", "procedure-1");
    Assert(first.MarkRunning(context, "office.addin.set_load_behavior"));
    var before = new RegistryValueSnapshot(true, @"Software\Microsoft\Office\Outlook\Addins\Fixture", "LoadBehavior", "64",
        "DWord", JsonValue.Create(3));
    Assert(first.TrySaveMutation(new MutationRecord("interrupted", "device-1", "session-1", "user-1", "procedure-1",
        "office.addin.set_load_behavior", before)));
    Assert(first.TryMarkMutationApplied("interrupted", before with { Value = JsonValue.Create(2) }));

    var restarted = new PersistentCommandJournal(store);
    var report = restarted.PendingReports().Single();
    Assert(report.Status == "failed", "interrupted action was not failed safely");
    Assert(report.Result["outcome"]?.GetValue<string>() == "unknown", "unknown outcome was hidden");
    Assert(report.Result["changed"]?.GetValue<bool>() is true, "completed mutation was not disclosed");
    Assert(report.Result["source_command_id"]?.GetValue<string>() == "interrupted", "rollback reference missing");
});

T("corrupt persistent journal fails closed instead of forgetting handled commands", () =>
{
    var store = new MemoryKeyStore();
    store.Save("command-journal.v1.json", Encoding.UTF8.GetBytes("not valid journal JSON"));
    try { _ = new PersistentCommandJournal(store); throw new Exception("corrupt journal was treated as empty"); }
    catch (InvalidDataException) { }
});

await TA("runtime sends the durable outbox before polling and never re-executes a completed duplicate", async () =>
{
    using var harness = new RuntimeHarness();
    var journal = new InMemoryCommandJournal();
    var provider = new CountingProvider();
    var runtime = new AgentRuntime(harness.Client, provider, new AcceptingUi(), journal);
    var begin = harness.SessionBegin("session-1");
    var action = harness.Action("command-1", "session-1", "system.info", new JsonObject());
    harness.EnqueuePoll(begin, action);
    harness.FailNextResult = true;

    try { await runtime.PollOnceAsync(default); throw new Exception("lost connection was not surfaced"); }
    catch (AgentProtocolException) { }
    Assert(provider.CallCount == 1, "action did not execute exactly once before disconnect");
    Assert(journal.PendingReports().Count == 1, "completed result was lost after disconnect");

    harness.Paths.Clear();
    harness.EnqueuePoll(action);
    await runtime.PollOnceAsync(default);
    Assert(provider.CallCount == 1, "completed duplicate executed again");
    Assert(journal.PendingReports().Count == 0, "accepted report was not acknowledged");
    Assert(harness.Paths.Count >= 2 && harness.Paths[0].EndsWith("/commands/command-1/result") && harness.Paths[1].EndsWith("/poll"),
        "pending result did not flush before polling for new work");
    Assert(harness.AcceptedResults.Count == 2, "completed duplicate did not resend its cached result");
    Assert(!harness.Paths.Any(x => x.EndsWith("/screenshot")), "JSON-only action uploaded screenshot evidence");
});

await TA("runtime uploads screenshot before result and restart resumes without repeating native input", async () =>
{
    var store = new MemoryKeyStore();
    var provider = new CountingProvider { Screenshot = new byte[] { 137, 80, 78, 71, 5, 4, 3, 2, 1 } };
    using (var first = new RuntimeHarness())
    {
        var runtime = new AgentRuntime(first.Client, provider, new AcceptingUi(), new PersistentCommandJournal(store));
        first.EnqueuePoll(
            first.SessionBegin("screen-session"),
            first.Action("screen-command", "screen-session", "system.info", new JsonObject()));
        first.FailNextScreenshot = true;
        try { await runtime.PollOnceAsync(default); throw new Exception("screenshot upload failure was not surfaced"); }
        catch (AgentProtocolException) { }
        Assert(provider.CallCount == 1, "native action did not execute exactly once");
        Assert(!first.Paths.Any(x => x.EndsWith("/result")), "result was sent before screenshot evidence succeeded");
    }

    using (var restarted = new RuntimeHarness())
    {
        var shouldNotRun = new CountingProvider();
        var runtime = new AgentRuntime(restarted.Client, shouldNotRun, new AcceptingUi(), new PersistentCommandJournal(store));
        restarted.EnqueuePoll();
        await runtime.PollOnceAsync(default);
        Assert(shouldNotRun.CallCount == 0, "restart repeated an already journaled action");
        var evidenceAt = restarted.Paths.FindIndex(x => x.EndsWith("/screenshot"));
        var resultAt = restarted.Paths.FindIndex(x => x.EndsWith("/result"));
        Assert(evidenceAt >= 0 && resultAt > evidenceAt, "result was not ordered after screenshot upload");
        Assert(restarted.AcceptedScreenshots.Single().SequenceEqual(provider.Screenshot!), "uploaded screenshot bytes changed");
    }
});

await TA("runtime refuses a duplicate command id whose signed parameters changed", async () =>
{
    using var harness = new RuntimeHarness();
    var provider = new CountingProvider();
    var runtime = new AgentRuntime(harness.Client, provider, new AcceptingUi(), new InMemoryCommandJournal());
    harness.EnqueuePoll(
        harness.SessionBegin("conflict-session"),
        harness.Action("conflict-command", "conflict-session", "system.info", new JsonObject { ["detail"] = "basic" }));
    await runtime.PollOnceAsync(default);

    harness.EnqueuePoll(
        harness.Action("conflict-command", "conflict-session", "system.info", new JsonObject { ["detail"] = "full" }));
    await runtime.PollOnceAsync(default);

    Assert(provider.CallCount == 1, "conflicting duplicate executed again");
    Assert(harness.AcceptedResults.Count == 1, "cached success was reported for a different signed payload");
    Assert(runtime.Log.Any(x => x.Level == "critical" && x.Message.Contains("conflicting duplicate command")),
        "conflicting duplicate was not recorded as a security event");
});

await TA("runtime passes exact signed consent copy to the native UI", async () =>
{
    using var harness = new RuntimeHarness();
    var ui = new CapturingUi();
    var runtime = new AgentRuntime(harness.Client, new CountingProvider(), ui, new InMemoryCommandJournal());
    harness.EnqueuePoll(harness.SessionBegin("copy-session", new JsonObject
    {
        ["native_title"] = "JoltIQ — permission requested",
        ["native_message"] = "Disable Contoso PDF Preview and safely restart Outlook. Allow this session?",
        ["addin_name"] = "Contoso PDF Preview",
        ["addin_progid"] = "Contoso.PdfPreview",
        ["active_message"] = "JoltIQ support is active on this PC — STOP ends control immediately.",
    }));

    await runtime.PollOnceAsync(default);

    Assert(ui.Request is not null, "native consent model was not delivered");
    Assert(ui.Request!.Title == "JoltIQ — permission requested", "signed title changed");
    Assert(ui.Request.Body == "Disable Contoso PDF Preview and safely restart Outlook. Allow this session?", "signed body changed");
    Assert(ui.Request.AddinName == "Contoso PDF Preview" && ui.Request.AddinProgId == "Contoso.PdfPreview", "signed add-in identity changed");
    Assert(ui.ActiveMessage == "JoltIQ support is active on this PC — STOP ends control immediately.", "signed banner changed");
});

await TA("runtime ignores a duplicate while the original command is still running", async () =>
{
    using var harness = new RuntimeHarness();
    var provider = new CountingProvider { WaitForRelease = true };
    var runtime = new AgentRuntime(harness.Client, provider, new AcceptingUi(), new InMemoryCommandJournal());
    harness.EnqueuePoll(harness.SessionBegin("session-1"));
    await runtime.PollOnceAsync(default);
    var action = harness.Action("command-running", "session-1", "system.info", new JsonObject());
    harness.EnqueuePoll(action);
    var first = runtime.PollOnceAsync(default);
    await provider.Entered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    harness.EnqueuePoll(action);
    await runtime.PollOnceAsync(default);
    Assert(provider.CallCount == 1, "running duplicate executed concurrently");
    provider.Release.TrySetResult();
    await first;
});

await TA("STOP refuses queued work and cancels in-progress work with monotonic evidence", async () =>
{
    using (var before = new RuntimeHarness())
    {
        var provider = new CountingProvider();
        var runtime = new AgentRuntime(before.Client, provider, new AcceptingUi(), new InMemoryCommandJournal());
        before.EnqueuePoll(before.SessionBegin("session-before"));
        await runtime.PollOnceAsync(default);
        await runtime.StopFromDeviceAsync("session-before");
        before.EnqueuePoll(before.Action("after-stop", "session-before", "system.info", new JsonObject()));
        await runtime.PollOnceAsync(default);
        Assert(provider.CallCount == 0, "queued work ran after STOP");
        Assert(runtime.StopEvents.Count == 1 && runtime.StopEvents[0].MonotonicTimestamp > 0, "STOP timing evidence missing");
    }

    using (var during = new RuntimeHarness())
    {
        var provider = new CountingProvider { WaitForCancellation = true };
        var journal = new InMemoryCommandJournal();
        var runtime = new AgentRuntime(during.Client, provider, new AcceptingUi(), journal);
        during.EnqueuePoll(during.SessionBegin("session-during"));
        await runtime.PollOnceAsync(default);
        during.EnqueuePoll(during.Action("during-stop", "session-during", "system.info", new JsonObject()));
        var running = runtime.PollOnceAsync(default);
        await provider.Entered.Task.WaitAsync(TimeSpan.FromSeconds(2));
        await runtime.StopFromDeviceAsync("session-during");
        await running;
        Assert(provider.CancellationObserved, "in-progress action did not receive STOP cancellation");
        Assert(journal.TryGetCommand("during-stop", out var saved) && saved.Status == "failed", "cancelled result was not journaled");
    }
});

Console.WriteLine($"\n{passed} passed, {failures} failed");
return failures == 0 ? 0 : 1;

sealed class MemoryKeyStore : IKeyStore
{
    private readonly Dictionary<string, byte[]> _data = new(StringComparer.Ordinal);
    public byte[]? Load(string name) => _data.TryGetValue(name, out var value) ? value.ToArray() : null;
    public void Save(string name, byte[] data) => _data[name] = data.ToArray();
    public void Delete(string name) => _data.Remove(name);
}

sealed class ProtocolHandler : HttpMessageHandler
{
    private readonly Queue<JsonArray> _polls = new();
    public bool FailNextResult { get; set; }
    public bool FailNextScreenshot { get; set; }
    public List<string> Paths { get; } = new();
    public List<JsonObject> AcceptedResults { get; } = new();
    public List<byte[]> AcceptedScreenshots { get; } = new();

    public void Enqueue(params JsonObject[] envelopes)
    {
        lock (_polls) _polls.Enqueue(new JsonArray(envelopes.Select(x => (JsonNode?)x.DeepClone()).ToArray()));
    }

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var path = request.RequestUri!.AbsolutePath;
        Paths.Add(path);
        if (path.EndsWith("/poll"))
        {
            JsonArray envelopes;
            lock (_polls) envelopes = _polls.Count == 0 ? new JsonArray() : _polls.Dequeue();
            return Json(new JsonObject { ["envelopes"] = envelopes });
        }
        if (path.Contains("/commands/") && path.EndsWith("/result"))
        {
            if (FailNextResult)
            {
                FailNextResult = false;
                return new HttpResponseMessage(System.Net.HttpStatusCode.ServiceUnavailable) { Content = new StringContent("offline") };
            }
            var body = JsonNode.Parse(await request.Content!.ReadAsStringAsync(cancellationToken))!.AsObject();
            AcceptedResults.Add(body);
            return Json(new JsonObject { ["ok"] = true });
        }
        if (path.Contains("/commands/") && path.EndsWith("/screenshot"))
        {
            if (FailNextScreenshot)
            {
                FailNextScreenshot = false;
                return new HttpResponseMessage(System.Net.HttpStatusCode.ServiceUnavailable) { Content = new StringContent("offline") };
            }
            if (request.Content?.Headers.ContentType?.MediaType != "image/png")
                throw new InvalidOperationException("screenshot content type changed");
            AcceptedScreenshots.Add(await request.Content!.ReadAsByteArrayAsync(cancellationToken));
            return Json(new JsonObject { ["ok"] = true, ["stored"] = true });
        }
        return Json(new JsonObject { ["ok"] = true });
    }

    private static HttpResponseMessage Json(JsonNode body) => new(System.Net.HttpStatusCode.OK)
    {
        Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json"),
    };
}

sealed class RuntimeHarness : IDisposable
{
    private readonly ECDsa _server = ECDsa.Create(ECCurve.NamedCurves.nistP256);
    private readonly ProtocolHandler _handler = new();
    public AgentClient Client { get; }
    public bool FailNextResult { set => _handler.FailNextResult = value; }
    public bool FailNextScreenshot { set => _handler.FailNextScreenshot = value; }
    public List<string> Paths => _handler.Paths;
    public List<JsonObject> AcceptedResults => _handler.AcceptedResults;
    public List<byte[]> AcceptedScreenshots => _handler.AcceptedScreenshots;

    public RuntimeHarness()
    {
        var store = new MemoryKeyStore();
        using var deviceKey = DeviceKey.Create();
        store.Save("device.key", deviceKey.ExportPkcs8());
        store.Save("state.json", JsonSerializer.SerializeToUtf8Bytes(new AgentState
        {
            ServerUrl = "http://localhost",
            DeviceId = "device-1",
            OrgId = "org-1",
            ServerPublicKeyPem = _server.ExportSubjectPublicKeyInfoPem(),
        }));
        Client = new AgentClient("http://localhost", store, _handler);
    }

    public void EnqueuePoll(params JsonObject[] envelopes) => _handler.Enqueue(envelopes);

    public JsonObject SessionBegin(string sessionId, JsonObject? requestDetails = null)
    {
        var body = new JsonObject
        {
        ["type"] = "session.begin", ["session_id"] = sessionId, ["device_id"] = "device-1",
        ["issued_at"] = DateTimeOffset.UtcNow.ToUnixTimeSeconds(), ["expires_at"] = DateTimeOffset.UtcNow.AddMinutes(5).ToUnixTimeSeconds(),
        ["nonce"] = Guid.NewGuid().ToString("N"),
        };
        if (requestDetails is not null) body["request_details"] = requestDetails;
        return Envelope(body);
    }

    public JsonObject Action(string commandId, string sessionId, string action, JsonObject parameters) => Envelope(new JsonObject
    {
        ["type"] = "action", ["command_id"] = commandId, ["device_id"] = "device-1", ["session_id"] = sessionId,
        ["action"] = action, ["params"] = parameters, ["procedure_hash"] = "procedure-1",
        ["issued_at"] = DateTimeOffset.UtcNow.ToUnixTimeSeconds(), ["expires_at"] = DateTimeOffset.UtcNow.AddMinutes(2).ToUnixTimeSeconds(),
        ["nonce"] = Guid.NewGuid().ToString("N"), ["risk"] = "READ_ONLY",
    });

    private JsonObject Envelope(JsonObject body)
    {
        var payload = Encoding.UTF8.GetBytes(body.ToJsonString());
        return new JsonObject
        {
            ["payload"] = Convert.ToBase64String(payload),
            ["signature"] = Convert.ToBase64String(_server.SignData(payload, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence)),
        };
    }

    public void Dispose() { Client.Dispose(); _server.Dispose(); }
}

sealed class CountingProvider : IActionProvider
{
    public int CallCount { get; private set; }
    public bool WaitForRelease { get; init; }
    public bool WaitForCancellation { get; init; }
    public bool CancellationObserved { get; private set; }
    public byte[]? Screenshot { get; init; }
    public TaskCompletionSource Entered { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource Release { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public IReadOnlyCollection<string> Capabilities { get; } = new[] { "system.info" };
    public IDictionary<string, string> Facts() => new Dictionary<string, string>();

    public async Task<ActionResult> ExecuteAsync(string action, JsonElement parameters, CancellationToken ct, ActionExecutionContext? context = null)
    {
        CallCount++;
        Entered.TrySetResult();
        if (WaitForRelease) await Release.Task.WaitAsync(ct);
        if (WaitForCancellation)
        {
            try { await Task.Delay(Timeout.InfiniteTimeSpan, ct); }
            catch (OperationCanceledException) { CancellationObserved = true; throw; }
        }
        return new ActionResult(
            new JsonObject { ["ok"] = true, ["calls"] = CallCount },
            Screenshot,
            Screenshot is null ? null : "image/png");
    }
}

sealed class AcceptingUi : IUserInteraction
{
    public string CurrentUserDisplayName => "Test User";
    public Task<bool> AskConsentAsync(string sessionId, NativeConsentRequest request, TimeSpan timeout, CancellationToken ct) => Task.FromResult(true);
    public void SessionStarted(string sessionId, string activeMessage, Action stopRequested) { }
    public void SessionEnded(string sessionId, string reason) { }
    public void ActionPerformed(string sessionId, string summary) { }
}

sealed class CapturingUi : IUserInteraction
{
    public NativeConsentRequest? Request { get; private set; }
    public string? ActiveMessage { get; private set; }
    public string CurrentUserDisplayName => "Test User";
    public Task<bool> AskConsentAsync(string sessionId, NativeConsentRequest request, TimeSpan timeout, CancellationToken ct)
    {
        Request = request;
        return Task.FromResult(true);
    }
    public void SessionStarted(string sessionId, string activeMessage, Action stopRequested) => ActiveMessage = activeMessage;
    public void SessionEnded(string sessionId, string reason) { }
    public void ActionPerformed(string sessionId, string summary) { }
}
