using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace JoltIQ.Agent.Core;

/// <summary>OS-specific action implementation (Windows: JoltIQ.Agent.Windows; tests: SimulatedActionProvider).</summary>
public interface IActionProvider
{
    IReadOnlyCollection<string> Capabilities { get; }
    Task<ActionResult> ExecuteAsync(string action, JsonElement parameters, CancellationToken ct, ActionExecutionContext? context = null);
    IDictionary<string, string> Facts();
}

public sealed record ActionResult(
    JsonObject Data,
    byte[]? Screenshot = null,
    string? ScreenshotMediaType = null)
{
    public JsonNode? this[string propertyName] => Data[propertyName];
}

/// <summary>User-facing UI (Windows: tray + consent dialog + always-on-top session banner).</summary>
public interface IUserInteraction
{
    Task<bool> AskConsentAsync(string sessionId, NativeConsentRequest request, TimeSpan timeout, CancellationToken ct);
    void SessionStarted(string sessionId, string activeMessage, Action stopRequested);
    void SessionEnded(string sessionId, string reason);
    void ActionPerformed(string sessionId, string summary);
    string CurrentUserDisplayName { get; }
}

public sealed record NativeConsentRequest(string Title, string Body, string? AddinName, string? AddinProgId, string ActiveMessage);
public sealed record RuntimeLogEntry(DateTimeOffset At, string Level, string Message);
public sealed record StopTiming(string SessionId, DateTimeOffset RequestedAt, long MonotonicTimestamp);

/// <summary>Poll loop: verify envelope → replay check → local consent/policy → execute → redact → report.</summary>
public sealed class AgentRuntime
{
    private readonly AgentClient _client;
    private readonly IActionProvider _provider;
    private readonly IUserInteraction _ui;
    private readonly LocalPolicy _policy;
    private readonly ICommandJournal _journal;
    private readonly ReplayGuard _replay = new();
    private readonly Dictionary<string, LocalSession> _sessions = new();
    private readonly List<StopTiming> _stopEvents = new();
    private EnvelopeVerifier? _verifier;
    public List<RuntimeLogEntry> Log { get; } = new();
    public TimeSpan PollInterval { get; set; } = TimeSpan.FromSeconds(3);
    public TimeSpan ConsentTimeout { get; set; } = TimeSpan.FromMinutes(2);

    public AgentRuntime(AgentClient client, IActionProvider provider, IUserInteraction ui, ICommandJournal journal,
        LocalPolicy? policy = null)
    {
        _client = client; _provider = provider; _ui = ui; _journal = journal;
        _policy = policy ?? new LocalPolicy();
    }

    public IReadOnlyDictionary<string, LocalSession> Sessions => _sessions;
    public IReadOnlyList<StopTiming> StopEvents { get { lock (_stopEvents) return _stopEvents.ToArray(); } }

    private void L(string level, string msg)
    {
        lock (Log) { Log.Add(new(DateTimeOffset.UtcNow, level, Redactor.Redact(msg))); if (Log.Count > 2000) Log.RemoveRange(0, 500); }
    }

    public async Task RunAsync(CancellationToken ct)
    {
        var backoff = PollInterval;
        var lastBeat = DateTimeOffset.MinValue;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                if (DateTimeOffset.UtcNow - lastBeat > TimeSpan.FromSeconds(60))
                {
                    await _client.SendAsync(HttpMethod.Post, "/heartbeat", new { agent_version = AgentClient.Version, facts = _provider.Facts(), capabilities = _provider.Capabilities }, ct);
                    lastBeat = DateTimeOffset.UtcNow;
                }
                await PollOnceAsync(ct);
                backoff = PollInterval;
            }
            catch (DeviceRevokedException)
            {
                L("warn", "Device revoked by administrator; stopping and forgetting enrollment.");
                foreach (var s in _sessions.Values) EndLocal(s, "device revoked");
                _client.ForgetEnrollment();
                return;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested) { return; }
            catch (Exception e)
            {
                // Reconnect with exponential backoff (max 60 s); sessions stay paused locally while offline.
                L("error", $"poll failed: {e.Message}");
                backoff = TimeSpan.FromSeconds(Math.Min(60, backoff.TotalSeconds * 2));
            }
            try { await Task.Delay(backoff, ct); } catch (OperationCanceledException) { return; }
        }
    }

    public async Task<int> PollOnceAsync(CancellationToken ct)
    {
        _verifier ??= new EnvelopeVerifier(_client.State.ServerPublicKeyPem!);
        await FlushPendingReportsAsync(ct);
        var res = await _client.SendAsync(HttpMethod.Get, "/poll", null, ct);
        var envs = res?["envelopes"]?.AsArray() ?? new JsonArray();
        var n = 0;
        foreach (var env in envs)
        {
            if (env is null) continue;
            await HandleEnvelopeAsync(env, ct);
            n++;
        }
        foreach (var s in _sessions.Values.Where(s => s.Active && s.Expired).ToList()) EndLocal(s, "consent expired");
        return n;
    }

    internal async Task HandleEnvelopeAsync(JsonNode env, CancellationToken ct)
    {
        var payloadB64 = env["payload"]?.GetValue<string>();
        var sig = env["signature"]?.GetValue<string>();
        if (payloadB64 is null || sig is null) { L("warn", "envelope missing payload/signature; ignored"); return; }
        byte[] payload;
        try { payload = Convert.FromBase64String(payloadB64); } catch (FormatException) { L("warn", "bad payload encoding"); return; }
        if (!_verifier!.Verify(payload, sig)) { L("critical", "envelope signature INVALID; ignored"); return; }
        using var doc = JsonDocument.Parse(payload);
        var b = doc.RootElement;
        var type = b.GetProperty("type").GetString();
        var nonce = b.TryGetProperty("nonce", out var nn) ? nn.GetString() : null;
        var issued = b.TryGetProperty("issued_at", out var ia) ? ia.GetInt64() : 0;
        long? exp = b.TryGetProperty("expires_at", out var ea) && ea.ValueKind == JsonValueKind.Number ? ea.GetInt64() : null;
        var sessionId = b.TryGetProperty("session_id", out var si) && si.ValueKind == JsonValueKind.String ? si.GetString() : null;

        if (b.TryGetProperty("device_id", out var did) && did.GetString() != _client.State.DeviceId) { L("critical", "envelope for another device"); return; }
        if (type == "action" && b.TryGetProperty("command_id", out var cidEl) &&
            _journal.TryGetCommand(cidEl.GetString()!, out var existing))
        {
            if (!Matches(existing, b, sessionId))
            {
                L("critical", $"conflicting duplicate command {existing.CommandId} was refused");
                return;
            }
            if (existing.State == LocalCommandState.Completed)
                await SendCachedReportAsync(existing, ct);
            else
                L("info", $"duplicate command {existing.CommandId} is already running");
            return;
        }
        if (type == "session.begin" && sessionId is not null && _sessions.ContainsKey(sessionId)) return;
        var (ok, why) = _replay.Check(nonce, issued, exp);
        if (!ok) { L("warn", $"envelope rejected: {why}"); return; }

        switch (type)
        {
            case "session.begin":
                await BeginSessionAsync(sessionId!, exp, b, ct);
                break;
            case "session.end":
                if (sessionId is not null && _sessions.TryGetValue(sessionId, out var s)) EndLocal(s, "ended by server");
                break;
            case "action":
                await RunActionAsync(b, sessionId, ct);
                break;
            default:
                L("warn", $"unknown envelope type {type}");
                break;
        }
    }

    private async Task BeginSessionAsync(string sessionId, long? exp, JsonElement envelope, CancellationToken ct)
    {
        var request = ConsentRequest(envelope);
        var accepted = await _ui.AskConsentAsync(sessionId, request, ConsentTimeout, ct);
        var s = new LocalSession { SessionId = sessionId, ExpiresAt = exp, Active = accepted };
        _sessions[sessionId] = s;
        await _client.SendAsync(HttpMethod.Post, $"/sessions/{sessionId}/local-consent", new { accepted, user_display_name = _ui.CurrentUserDisplayName }, ct);
        if (accepted)
        {
            _ui.SessionStarted(sessionId, request.ActiveMessage, () => _ = StopFromDeviceAsync(sessionId));
            L("info", $"session {sessionId} accepted locally");
        }
        else L("info", $"session {sessionId} declined locally");
    }

    private static NativeConsentRequest ConsentRequest(JsonElement envelope)
    {
        const string title = "JoltIQ — permission requested";
        const string body = "Your support team's JoltIQ assistant is asking to run diagnostics and approved fixes on this computer. " +
                            "You will see a banner while it is active and can stop it at any time.";
        const string active = "JoltIQ support is active on this PC — STOP ends control immediately.";
        if (!envelope.TryGetProperty("request_details", out var details) || details.ValueKind != JsonValueKind.Object)
            return new(title, body, null, null, active);
        string? Read(string key) => details.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() : null;
        return new(Read("native_title") ?? title, Read("native_message") ?? body, Read("addin_name"), Read("addin_progid"),
            Read("active_message") ?? active);
    }

    /// <summary>Kill switch: local cancellation happens first, so it works even if the server is unreachable.</summary>
    public async Task StopFromDeviceAsync(string sessionId)
    {
        lock (_stopEvents) _stopEvents.Add(new StopTiming(sessionId, DateTimeOffset.UtcNow, Stopwatch.GetTimestamp()));
        if (_sessions.TryGetValue(sessionId, out var s)) EndLocal(s, "stopped by user");
        try { await _client.SendAsync(HttpMethod.Post, $"/sessions/{sessionId}/stop", new { }, CancellationToken.None); }
        catch (Exception e) { L("warn", $"stop notification will not reach server now: {e.Message}"); }
    }

    private void EndLocal(LocalSession s, string reason)
    {
        lock (s.DispatchGate)
        {
            if (!s.Active && s.Cancel.IsCancellationRequested) return;
            s.Active = false;
            s.Cancel.Cancel();
        }
        _ui.SessionEnded(s.SessionId, reason);
        L("info", $"session {s.SessionId} ended: {reason}");
    }

    private async Task RunActionAsync(JsonElement b, string? sessionId, CancellationToken ct)
    {
        var cid = b.GetProperty("command_id").GetString()!;
        var action = b.GetProperty("action").GetString()!;
        var p = b.GetProperty("params");
        LocalSession? s = sessionId is not null && _sessions.TryGetValue(sessionId, out var ss) ? ss : null;
        var executionContext = new ActionExecutionContext(
            cid,
            _client.State.DeviceId ?? "",
            sessionId ?? "",
            "",
            b.TryGetProperty("procedure_hash", out var procedureHash) && procedureHash.ValueKind == JsonValueKind.String
                ? procedureHash.GetString()
                : null,
            HashParameters(p));
        if (!_journal.MarkRunning(executionContext, action))
        {
            L("critical", $"command {cid} could not enter the durable running state; it was not executed");
            return;
        }
        var verdict = _policy.Evaluate(action, p, s);
        if (!verdict.Allowed)
        {
            L("warn", $"action {action} refused by local policy: {verdict.Reason}");
            await CompleteAndReportAsync(cid, "rejected", new JsonObject { ["ok"] = false, ["error"] = $"refused by device policy: {verdict.Reason}" }, ct);
            return;
        }
        if (!_provider.Capabilities.Contains(action))
        {
            await CompleteAndReportAsync(cid, "rejected", new JsonObject { ["ok"] = false, ["error"] = "not supported on this device" }, ct);
            return;
        }
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct, s!.Cancel.Token);
        linked.CancelAfter(TimeSpan.FromSeconds(90));
        JsonObject result;
        CommandEvidence? evidence = null;
        string status;
        try
        {
            Task<ActionResult> execution;
            lock (s.DispatchGate)
            {
                linked.Token.ThrowIfCancellationRequested();
                execution = _provider.ExecuteAsync(action, p, linked.Token, executionContext);
            }
            var actionResult = await execution;
            result = actionResult.Data;
            if (actionResult.Screenshot is not null)
                evidence = new CommandEvidence(actionResult.ScreenshotMediaType ?? "", actionResult.Screenshot);
            result["ok"] ??= true;
            status = result["ok"]!.GetValue<bool>() ? "done" : "failed";
            _ui.ActionPerformed(s.SessionId, Describe(action, p));
        }
        catch (OperationCanceledException)
        {
            result = new JsonObject { ["ok"] = false, ["error"] = s.Cancel.IsCancellationRequested ? "session stopped by user" : "timed out" };
            status = "failed";
        }
        catch (Exception e)
        {
            result = new JsonObject { ["ok"] = false, ["error"] = Redactor.Redact(e.Message) };
            status = "failed";
        }
        await CompleteAndReportAsync(cid, status, result, ct, evidence);
    }

    private async Task CompleteAndReportAsync(
        string cid,
        string status,
        JsonObject result,
        CancellationToken ct,
        CommandEvidence? evidence = null)
    {
        var redacted = Redactor.RedactNode(result)!.AsObject();
        if (!_journal.MarkCompleted(cid, status, redacted, evidence))
        {
            L("critical", $"command {cid} completed but its result could not be persisted; no success was reported");
            StopSessionForCommand(cid, "result evidence could not be saved safely");
            return;
        }
        if (!_journal.TryGetCommand(cid, out var completed) || completed.CompletedAt is null) return;
        await SendReportAsync(new PendingCommandReport(
            cid,
            completed.SessionId,
            status,
            redacted,
            completed.CompletedAt.Value,
            completed.Evidence), ct);
    }

    private async Task FlushPendingReportsAsync(CancellationToken ct)
    {
        foreach (var report in _journal.PendingReports()) await SendReportAsync(report, ct);
    }

    private async Task SendCachedReportAsync(CommandJournalEntry entry, CancellationToken ct)
    {
        if (entry.Status is null || entry.Result is null || entry.CompletedAt is null) return;
        await SendReportAsync(new PendingCommandReport(
            entry.CommandId,
            entry.SessionId,
            entry.Status,
            entry.Result,
            entry.CompletedAt.Value,
            entry.Evidence), ct);
    }

    private async Task SendReportAsync(PendingCommandReport report, CancellationToken ct)
    {
        if (report.Evidence is not null)
        {
            if (!_journal.TryLoadEvidence(report.CommandId, out var evidence, out var error) || evidence is null)
            {
                var why = error ?? "protected evidence was unavailable";
                L("critical", $"command {report.CommandId} evidence failed validation; reporting an unknown outcome");
                StopSessionForCommand(report.CommandId, "post-action evidence was unavailable or corrupt");
                if (!_journal.MarkEvidenceUnavailable(report.CommandId, why)) return;
                var failed = _journal.PendingReports().SingleOrDefault(x => x.CommandId == report.CommandId);
                if (failed is null) return;
                await SendReportAsync(failed, ct);
                return;
            }
            await _client.SendCommandScreenshotAsync(report.CommandId, evidence.Bytes, evidence.MediaType, ct);
        }
        await _client.SendCommandResultAsync(report, ct);
        if (!_journal.AcknowledgeReport(report.CommandId))
            L("warn", $"server accepted command {report.CommandId}, but local acknowledgement was not saved; it will be retried safely");
    }

    private void StopSessionForCommand(string commandId, string reason)
    {
        if (_journal.TryGetCommand(commandId, out var entry) &&
            _sessions.TryGetValue(entry.SessionId, out var session))
            EndLocal(session, reason);
    }

    private static bool Matches(CommandJournalEntry entry, JsonElement envelope, string? sessionId)
    {
        var procedureHash = envelope.TryGetProperty("procedure_hash", out var hash) && hash.ValueKind == JsonValueKind.String
            ? hash.GetString()
            : null;
        return entry.DeviceId == envelope.GetProperty("device_id").GetString() && entry.SessionId == (sessionId ?? "") &&
               entry.Action == envelope.GetProperty("action").GetString() && entry.ProcedureHash == procedureHash &&
               entry.ParametersHash is not null && entry.ParametersHash == HashParameters(envelope.GetProperty("params"));
    }

    private static string HashParameters(JsonElement parameters) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(parameters.GetRawText()))).ToLowerInvariant();

    private static string Describe(string action, JsonElement p) => action switch
    {
        "app.restart" => $"Restarted {p.GetProperty("process").GetString()}",
        "service.restart" => $"Restarted service {p.GetProperty("name").GetString()}",
        "office.addin.set_load_behavior" => $"Changed add-in {p.GetProperty("addin_progid").GetString()}",
        _ => $"Ran {action}",
    };
}

/// <summary>On-device redaction before anything is uploaded (mirrors server redaction rules).</summary>
public static class Redactor
{
    private static readonly (Regex re, string repl)[] Rules =
    {
        (new(@"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED:private_key]"),
        (new(@"(?i)\b(pass(word|wd)?|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"), "[REDACTED:password_kv]"),
        (new(@"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED:email]"),
        (new(@"(?i)C:\\Users\\[^\\\s""]+"), @"C:\Users\[user]"),
        (new(@"\b(?:\d[ -]?){13,19}\b"), "[REDACTED:number]"),
        (new(@"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED:jwt]"),
    };
    private static readonly Regex SecretKey = new("(?i)(password|passwd|secret|token|cookie|authorization|api[_-]?key|mfa|otp|private[_-]?key)");

    public static string Redact(string s)
    {
        foreach (var (re, repl) in Rules) s = re.Replace(s, repl);
        return s;
    }

    public static JsonNode? RedactNode(JsonNode? n) => n switch
    {
        JsonObject o => new JsonObject(o.Select(kv => KeyValuePair.Create(kv.Key, SecretKey.IsMatch(kv.Key) ? JsonValue.Create("[REDACTED]") : RedactNode(kv.Value?.DeepClone())))),
        JsonArray a => new JsonArray(a.Select(x => RedactNode(x?.DeepClone())).ToArray()),
        JsonValue v when v.TryGetValue<string>(out var str) => JsonValue.Create(Redact(str)),
        _ => n?.DeepClone(),
    };

    public static string Utf8(byte[] b) => Encoding.UTF8.GetString(b);
}
