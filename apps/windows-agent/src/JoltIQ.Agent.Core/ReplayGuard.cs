namespace JoltIQ.Agent.Core;

/// <summary>Rejects reused nonces and expired envelopes. Nonces are kept until their envelope would have expired.</summary>
public sealed class ReplayGuard
{
    private readonly Dictionary<string, long> _seen = new();
    private readonly object _lock = new();
    private readonly Func<long> _now;
    public const long MaxAgeSeconds = 600;

    public ReplayGuard(Func<long>? now = null) => _now = now ?? (() => DateTimeOffset.UtcNow.ToUnixTimeSeconds());

    public (bool ok, string reason) Check(string? nonce, long issuedAt, long? expiresAt)
    {
        var now = _now();
        if (string.IsNullOrEmpty(nonce)) return (false, "missing nonce");
        if (issuedAt > now + 120) return (false, "issued in the future");
        if (now - issuedAt > MaxAgeSeconds) return (false, "envelope too old");
        if (expiresAt is { } e && e < now) return (false, "envelope expired");
        lock (_lock)
        {
            foreach (var k in _seen.Where(kv => kv.Value < now - MaxAgeSeconds).Select(kv => kv.Key).ToList()) _seen.Remove(k);
            if (_seen.ContainsKey(nonce)) return (false, "nonce replayed");
            _seen[nonce] = issuedAt;
        }
        return (true, "");
    }
}
