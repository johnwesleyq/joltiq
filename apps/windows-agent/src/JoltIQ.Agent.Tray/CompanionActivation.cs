namespace JoltIQ.Agent.Tray;

public sealed record CompanionLaunchRequest(bool OpenSupportConversation)
{
    public static CompanionLaunchRequest Parse(IEnumerable<string> args) =>
        new(args.Any(arg => string.Equals(arg, "--ask", StringComparison.OrdinalIgnoreCase)));
}

internal sealed class CompanionActivation : IDisposable
{
    public const string MutexName = @"Local\JoltIQ.Companion";
    private const string EventName = @"Local\JoltIQ.Companion.OpenSupport";

    private readonly EventWaitHandle _openSupportEvent;

    public CompanionActivation()
    {
        _openSupportEvent = new EventWaitHandle(false, EventResetMode.AutoReset, EventName);
    }

    public WaitHandle OpenSupportWaitHandle => _openSupportEvent;

    public static bool RequestOpenSupport()
    {
        try
        {
            using var activation = EventWaitHandle.OpenExisting(EventName);
            return activation.Set();
        }
        catch (WaitHandleCannotBeOpenedException)
        {
            return false;
        }
    }

    public void Dispose() => _openSupportEvent.Dispose();
}
