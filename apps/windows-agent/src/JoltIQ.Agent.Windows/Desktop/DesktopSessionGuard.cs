namespace JoltIQ.Agent.Windows.Desktop;

public sealed class DesktopSessionException(IReadOnlyList<string> reasons)
    : InvalidOperationException($"Desktop capture refused: {string.Join(", ", reasons)}")
{
    public IReadOnlyList<string> Reasons { get; } = reasons;
}

public sealed class DesktopSessionGuard(InteractiveSessionInspector inspector)
{
    public void DemandSafeInteractiveSession()
    {
        var fact = inspector.Inspect();
        if (fact["supported"]?.GetValue<bool>() is true) return;
        var reasons = fact["reasons"]?.AsArray()
            .Select(x => x?.GetValue<string>())
            .Where(x => !string.IsNullOrWhiteSpace(x))
            .Cast<string>()
            .ToArray() ?? ["interactive_session_unknown"];
        throw new DesktopSessionException(reasons);
    }
}
