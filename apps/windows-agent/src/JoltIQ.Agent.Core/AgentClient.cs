using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace JoltIQ.Agent.Core;

public sealed class AgentState
{
    public string ServerUrl { get; set; } = "";
    public string? DeviceId { get; set; }
    public string? OrgId { get; set; }
    public string? ServerPublicKeyPem { get; set; }
    public string? EnrolledUserSid { get; set; }
    public long Counter { get; set; }
    public SupportRequestBookmark? ActiveSupportRequest { get; set; }
}

public sealed record SupportRequestBookmark(string TicketId, int TicketNumber, string RunId, string State);

public sealed record SupportRequestResult(
    [property: JsonPropertyName("ticket_id")] string TicketId,
    [property: JsonPropertyName("ticket_number")] int TicketNumber,
    [property: JsonPropertyName("run_id")] string RunId,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("ai_notice")] string? AiNotice = null,
    [property: JsonPropertyName("reply")] SupportQuickReply? Reply = null,
    [property: JsonPropertyName("setup_required")] bool SetupRequired = false);

public sealed record SupportQuickReply(
    [property: JsonPropertyName("id")] string? Id,
    [property: JsonPropertyName("content")] string Content,
    [property: JsonPropertyName("live_model")] bool LiveModel = false);

public sealed record SupportComputerUseStatus(
    [property: JsonPropertyName("state")] string? State,
    [property: JsonPropertyName("label")] string? Label,
    [property: JsonPropertyName("next_step")] string? NextStep,
    [property: JsonPropertyName("needs_local_approval")] bool NeedsLocalApproval = false,
    [property: JsonPropertyName("model_notice")] string? ModelNotice = null);

public sealed record SupportConversationMessage(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("content")] string Content,
    [property: JsonPropertyName("at")] DateTimeOffset At);

public sealed record SupportRequestDetail(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("ticket_id")] string TicketId,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("paused_for")] string? PausedFor,
    [property: JsonPropertyName("outcome_stage")] string? OutcomeStage,
    [property: JsonPropertyName("messages")] IReadOnlyList<SupportConversationMessage> Messages,
    [property: JsonPropertyName("ai_notice")] string? AiNotice = null,
    [property: JsonPropertyName("computer_use")] SupportComputerUseStatus? ComputerUse = null);

public sealed record DirectComputerUseResult(
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("computer_use_run_id")] string? ComputerUseRunId,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("state")] string State);

/// <summary>Signed HTTPS client for the companion protocol (PROTOCOL.md). Persists device key + counter via IKeyStore.</summary>
public sealed class AgentClient : IDisposable
{
    public const string ApiPrefix = "/api/v1/agent/v1";
    private readonly HttpClient _http;
    private readonly IKeyStore _store;
    private readonly object _stateLock = new();
    private readonly SemaphoreSlim _sendGate = new(1, 1);
    public AgentState State { get; private set; }
    private DeviceKey? _key;

    public AgentClient(string serverUrl, IKeyStore store, HttpMessageHandler? handler = null)
    {
        _store = store;
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.Timeout = TimeSpan.FromSeconds(30);
        _http.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("JoltIQ-Companion", Version));
        var raw = store.Load("state.json");
        State = raw is null ? new AgentState() : JsonSerializer.Deserialize<AgentState>(raw) ?? new AgentState();
        State.ServerUrl = serverUrl.TrimEnd('/');
        if (!State.ServerUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase) &&
            !State.ServerUrl.StartsWith("http://127.0.0.1", StringComparison.Ordinal) &&
            !State.ServerUrl.StartsWith("http://localhost", StringComparison.Ordinal))
            throw new ArgumentException("Server URL must use HTTPS (plain HTTP is allowed only for localhost development)");
        var pk = store.Load("device.key");
        if (pk is not null) _key = DeviceKey.FromPkcs8(pk);
    }

    public static string Version => typeof(AgentClient).Assembly.GetName().Version?.ToString(3) ?? "1.0.0";
    public bool Enrolled => State.DeviceId is not null && _key is not null && State.ServerPublicKeyPem is not null;
    public SupportRequestBookmark? ActiveSupportRequest => State.ActiveSupportRequest;

    private void PersistLocked() => _store.Save("state.json", JsonSerializer.SerializeToUtf8Bytes(State));

    public async Task EnrollAsync(string code, string hostname, string osName, string osVersion, IEnumerable<string> capabilities,
        string? enrolledUserSid = null, CancellationToken ct = default)
    {
        _key?.Dispose();
        _key = DeviceKey.Create();
        var body = JsonSerializer.Serialize(new
        {
            code, public_key_pem = _key.PublicKeyPem(), hostname, os_name = osName, os_version = osVersion,
            agent_version = Version, capabilities = capabilities.ToArray(),
        });
        using var resp = await _http.PostAsync(State.ServerUrl + ApiPrefix + "/enroll", new StringContent(body, Encoding.UTF8, "application/json"), ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode) throw new AgentProtocolException($"Enrollment failed ({(int)resp.StatusCode}): {Truncate(text)}");
        var j = JsonNode.Parse(text)!;
        lock (_stateLock)
        {
            State.DeviceId = j["device_id"]!.GetValue<string>();
            State.OrgId = j["org_id"]!.GetValue<string>();
            State.ServerPublicKeyPem = j["server_public_key_pem"]!.GetValue<string>();
            State.EnrolledUserSid = enrolledUserSid;
            State.Counter = 0;
            State.ActiveSupportRequest = null;
            _store.Save("device.key", _key.ExportPkcs8());
            PersistLocked();
        }
    }

    public async Task<JsonNode?> SendAsync(HttpMethod method, string relPath, object? payload = null, CancellationToken ct = default)
    {
        var body = payload is null ? Array.Empty<byte>() : JsonSerializer.SerializeToUtf8Bytes(payload);
        return await SendSignedAsync(method, relPath, body, payload is null ? null : "application/json", ct);
    }

    public Task<JsonNode?> SendCommandScreenshotAsync(
        string commandId,
        byte[] bytes,
        string mediaType,
        CancellationToken ct = default)
    {
        if (mediaType is not ("image/png" or "image/jpeg"))
            throw new ArgumentException("Screenshot media type must be image/png or image/jpeg", nameof(mediaType));
        if (bytes.Length == 0 || bytes.Length > 4 * 1024 * 1024)
            throw new ArgumentException("Screenshot must contain 1 byte to 4 MiB", nameof(bytes));
        return SendSignedAsync(HttpMethod.Post, $"/commands/{commandId}/screenshot", bytes, mediaType, ct);
    }

    private async Task<JsonNode?> SendSignedAsync(
        HttpMethod method,
        string relPath,
        byte[] body,
        string? mediaType,
        CancellationToken ct)
    {
        if (!Enrolled) throw new InvalidOperationException("not enrolled");
        await _sendGate.WaitAsync(ct);
        try
        {
            var path = ApiPrefix + relPath;
            string ctr;
            lock (_stateLock)
            {
                State.Counter++;
                ctr = State.Counter.ToString();
                PersistLocked();
            }
            var ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString();
            var nonce = RequestSigning.NewNonce();
            var sig = _key!.Sign(RequestSigning.StringToSign(method.Method, path, ts, nonce, ctr, body));
            using var req = new HttpRequestMessage(method, State.ServerUrl + path);
            if (mediaType is not null)
                req.Content = new ByteArrayContent(body) { Headers = { ContentType = new MediaTypeHeaderValue(mediaType) } };
            req.Headers.Add("X-JQ-Device", State.DeviceId);
            req.Headers.Add("X-JQ-Timestamp", ts);
            req.Headers.Add("X-JQ-Nonce", nonce);
            req.Headers.Add("X-JQ-Counter", ctr);
            req.Headers.Add("X-JQ-Signature", sig);
            using var resp = await _http.SendAsync(req, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if ((int)resp.StatusCode == 403 && text.Contains("device_revoked")) throw new DeviceRevokedException();
            if (!resp.IsSuccessStatusCode) throw new AgentProtocolException($"{method} {relPath} failed ({(int)resp.StatusCode}): {Truncate(text)}");
            return text.Length == 0 ? null : JsonNode.Parse(text);
        }
        finally
        {
            _sendGate.Release();
        }
    }

    public Task<JsonNode?> SendCommandResultAsync(PendingCommandReport report, CancellationToken ct = default) =>
        SendAsync(HttpMethod.Post, $"/commands/{report.CommandId}/result", new { status = report.Status, result = report.Result }, ct);

    public async Task<SupportRequestResult> CreateSupportRequestAsync(
        Guid requestId,
        string message,
        CancellationToken ct = default)
    {
        ValidateSupportMessage(message);
        var node = await SendAsync(
            HttpMethod.Post,
            "/support-requests",
            new { client_request_id = requestId, message },
            ct);
        var result = ParseResponse<SupportRequestResult>(node, "create support request");
        RememberSupportRequest(result);
        return result;
    }

    public async Task<SupportRequestResult> ContinueSupportRequestAsync(
        string runId,
        string message,
        CancellationToken ct = default)
    {
        ValidateSupportMessage(message);
        var id = NormalizeRunId(runId);
        var node = await SendAsync(HttpMethod.Post, $"/support-requests/{id}/messages", new { message }, ct);
        var result = ParseResponse<SupportRequestResult>(node, "continue support request");
        RememberSupportRequest(result);
        return result;
    }

    public async Task<SupportRequestDetail> GetSupportRequestAsync(string runId, CancellationToken ct = default)
    {
        var id = NormalizeRunId(runId);
        var node = await SendAsync(HttpMethod.Get, $"/support-requests/{id}", ct: ct);
        var detail = ParseResponse<SupportRequestDetail>(node, "get support request");
        lock (_stateLock)
        {
            if (State.ActiveSupportRequest is { } active && active.RunId == detail.Id)
            {
                State.ActiveSupportRequest = active with { State = detail.State };
                PersistLocked();
            }
        }
        return detail;
    }

    public async Task<DirectComputerUseResult> StartComputerUseAsync(string runId, string mode, CancellationToken ct = default)
    {
        var id = NormalizeRunId(runId);
        if (mode != "guide" && mode != "fix") throw new ArgumentException("Mode must be guide or fix", nameof(mode));
        var node = await SendAsync(HttpMethod.Post, $"/support-requests/{id}/computer-use", new { mode }, ct);
        return ParseResponse<DirectComputerUseResult>(node, $"start {mode} help");
    }

    private void RememberSupportRequest(SupportRequestResult result)
    {
        lock (_stateLock)
        {
            State.ActiveSupportRequest = new SupportRequestBookmark(result.TicketId, result.TicketNumber, result.RunId, result.State);
            PersistLocked();
        }
    }

    public void ForgetEnrollment()
    {
        lock (_stateLock)
        {
            _store.Delete("device.key");
            _store.Delete("state.json");
            _key?.Dispose();
            _key = null;
            State = new AgentState { ServerUrl = State.ServerUrl };
        }
    }

    private static T ParseResponse<T>(JsonNode? node, string operation)
    {
        if (node is null) throw new AgentProtocolException($"Invalid response while attempting to {operation}");
        try
        {
            return node.Deserialize<T>() ?? throw new JsonException("empty response");
        }
        catch (JsonException e)
        {
            throw new AgentProtocolException($"Invalid response while attempting to {operation}", e);
        }
    }

    private static string NormalizeRunId(string runId) =>
        Guid.TryParse(runId, out var id) ? id.ToString() : throw new ArgumentException("Run id must be a UUID", nameof(runId));

    private static void ValidateSupportMessage(string message)
    {
        if (string.IsNullOrWhiteSpace(message) || message.Length > 20_000)
            throw new ArgumentException("Support message must contain 1 to 20,000 characters", nameof(message));
    }

    private static string Truncate(string s)
    {
        var redacted = Redactor.Redact(s);
        return redacted.Length > 300 ? redacted[..300] : redacted;
    }
    public void Dispose() { _http.Dispose(); _key?.Dispose(); _sendGate.Dispose(); }
}

public class AgentProtocolException : Exception
{
    public AgentProtocolException(string message) : base(message) { }
    public AgentProtocolException(string message, Exception innerException) : base(message, innerException) { }
}
public sealed class DeviceRevokedException() : AgentProtocolException("device revoked by administrator");
