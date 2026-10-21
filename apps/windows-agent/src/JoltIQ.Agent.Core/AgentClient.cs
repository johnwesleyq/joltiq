using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace JoltIQ.Agent.Core;

public sealed class AgentState
{
    public string ServerUrl { get; set; } = "";
    public string? DeviceId { get; set; }
    public string? OrgId { get; set; }
    public string? ServerPublicKeyPem { get; set; }
    public string? EnrolledUserSid { get; set; }
    public long Counter { get; set; }
}

/// <summary>Signed HTTPS client for the companion protocol (PROTOCOL.md). Persists device key + counter via IKeyStore.</summary>
public sealed class AgentClient : IDisposable
{
    public const string ApiPrefix = "/api/v1/agent/v1";
    private readonly HttpClient _http;
    private readonly IKeyStore _store;
    private readonly object _ctrLock = new();
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

    private void Persist() => _store.Save("state.json", JsonSerializer.SerializeToUtf8Bytes(State));

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
        State.DeviceId = j["device_id"]!.GetValue<string>();
        State.OrgId = j["org_id"]!.GetValue<string>();
        State.ServerPublicKeyPem = j["server_public_key_pem"]!.GetValue<string>();
        State.EnrolledUserSid = enrolledUserSid;
        State.Counter = 0;
        _store.Save("device.key", _key.ExportPkcs8());
        Persist();
    }

    public async Task<JsonNode?> SendAsync(HttpMethod method, string relPath, object? payload = null, CancellationToken ct = default)
    {
        if (!Enrolled) throw new InvalidOperationException("not enrolled");
        var path = ApiPrefix + relPath;
        var body = payload is null ? Array.Empty<byte>() : JsonSerializer.SerializeToUtf8Bytes(payload);
        string ctr;
        lock (_ctrLock) { State.Counter++; ctr = State.Counter.ToString(); Persist(); }
        var ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString();
        var nonce = RequestSigning.NewNonce();
        var sig = _key!.Sign(RequestSigning.StringToSign(method.Method, path, ts, nonce, ctr, body));
        using var req = new HttpRequestMessage(method, State.ServerUrl + path);
        if (payload is not null) req.Content = new ByteArrayContent(body) { Headers = { ContentType = new MediaTypeHeaderValue("application/json") } };
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

    public Task<JsonNode?> SendCommandResultAsync(PendingCommandReport report, CancellationToken ct = default) =>
        SendAsync(HttpMethod.Post, $"/commands/{report.CommandId}/result", new { status = report.Status, result = report.Result }, ct);

    public void ForgetEnrollment()
    {
        _store.Delete("device.key");
        _store.Delete("state.json");
        _key?.Dispose();
        _key = null;
        State = new AgentState { ServerUrl = State.ServerUrl };
    }

    private static string Truncate(string s) => s.Length > 300 ? s[..300] : s;
    public void Dispose() { _http.Dispose(); _key?.Dispose(); }
}

public class AgentProtocolException(string m) : Exception(m);
public sealed class DeviceRevokedException() : AgentProtocolException("device revoked by administrator");
