using System.Collections.Concurrent;
using JoltIQ.Agent.Core;

namespace JoltIQ.Agent.Headless;

/// <summary>
/// Stand-in for the Windows tray/consent dialog. Consent requests wait for the person (DEV control endpoint
/// POST /consent/{sessionId}) unless AutoConsent is on; they are declined on timeout, like the real dialog.
/// Computer-use consent/approvals keep the interface default (always declined): there is no screen here.
/// </summary>
public sealed class HeadlessUi(bool autoConsent) : IUserInteraction
{
    public sealed record Pending(string SessionId, NativeConsentRequest Request, DateTimeOffset AskedAt, TaskCompletionSource<bool> Answer);
    private readonly ConcurrentDictionary<string, Pending> _pending = new();
    private readonly ConcurrentDictionary<string, Action> _stops = new();
    private readonly ConcurrentQueue<string> _events = new();

    public bool AutoConsent { get; } = autoConsent;
    public IReadOnlyCollection<Pending> PendingConsents => _pending.Values.ToArray();
    public IReadOnlyCollection<string> ActiveSessions => _stops.Keys.ToArray();
    public IReadOnlyList<string> Events => _events.ToArray();
    public string CurrentUserDisplayName => "Headless DEV user";

    private void Note(string e) { _events.Enqueue($"{DateTimeOffset.UtcNow:o} {e}"); while (_events.Count > 100) _events.TryDequeue(out _); }

    public async Task<bool> AskConsentAsync(string sessionId, NativeConsentRequest request, TimeSpan timeout, CancellationToken ct)
    {
        Note($"consent requested for {sessionId}: {request.Title}");
        if (AutoConsent) { Note($"consent auto-granted (AGENT_AUTO_CONSENT) for {sessionId}"); return true; }
        var p = new Pending(sessionId, request, DateTimeOffset.UtcNow, new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously));
        _pending[sessionId] = p;
        try
        {
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
            linked.CancelAfter(timeout);
            using var reg = linked.Token.Register(() => p.Answer.TrySetResult(false));
            var ok = await p.Answer.Task.ConfigureAwait(false);
            Note($"consent {(ok ? "granted" : "declined/timed out")} for {sessionId}");
            return ok;
        }
        finally { _pending.TryRemove(sessionId, out _); }
    }

    public bool Answer(string sessionId, bool accept) => _pending.TryGetValue(sessionId, out var p) && p.Answer.TrySetResult(accept);

    public bool Stop(string sessionId)
    {
        if (!_stops.TryGetValue(sessionId, out var stop)) return false;
        Note($"STOP pressed for {sessionId}");
        stop();
        return true;
    }

    public void SessionStarted(string sessionId, string activeMessage, Action stopRequested) { _stops[sessionId] = stopRequested; Note($"[banner] {activeMessage}"); }
    public void SessionEnded(string sessionId, string reason) { _stops.TryRemove(sessionId, out _); Note($"session {sessionId} ended: {reason}"); }
    public void ActionPerformed(string sessionId, string summary) => Note($"[toast] {summary}");
}

