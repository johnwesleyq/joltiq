using JoltIQ.Agent.Core;

namespace JoltIQ.Agent.Windows.Desktop;

public interface IDesktopInput
{
    Task ClickAsync(int x, int y, string button, int count, string? modifiers, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct);
    Task DragAsync(int x, int y, int toX, int toY, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct);
    Task MoveAsync(int x, int y, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct);
    Task MouseAsync(string state, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct);
    Task TypeAsync(string text, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct);
    Task KeyAsync(string combo, int repeat, TimeSpan hold, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct);
    Task ScrollAsync(int x, int y, string direction, int amount, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct);
    DesktopPoint Cursor();
}

public sealed class DesktopInput(
    Func<DesktopFrame?> latestFrame,
    IDesktopPixelSource pixelSource,
    DesktopSessionGuard sessionGuard,
    IInputTargetInspector targetInspector,
    INativeInput native,
    Func<bool> licenseAllowed) : IDesktopInput
{
    private static readonly IReadOnlySet<string> DangerousChords = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        "ctrl+alt+delete", "control+alt+delete", "win+r", "windows+r", "win+l", "windows+l",
        "ctrl+shift+esc", "control+shift+escape", "alt+f4", "win+x", "windows+x",
    };

    private readonly InputTargetGuard _targets = new(targetInspector);
    private readonly object _heldMouseGate = new();
    private CancellationTokenRegistration _heldMouseStopRegistration;
    private bool _leftMouseHeld;

    public async Task ClickAsync(int x, int y, string button, int count, string? modifiers, long frameGeneration,
        string layoutHash, LocalSession session, CancellationToken ct)
    {
        if (count is < 1 or > 3) throw new DesktopInputRefusedException("click count is out of range");
        if (button is not ("left" or "right" or "middle")) throw new DesktopInputRefusedException("mouse button is unsupported");
        var point = PreparePoint(x, y, frameGeneration, layoutHash, session, ct);
        var approved = _targets.DemandPointTarget(point);
        Recheck(frameGeneration, layoutHash, session, ct);
        _targets.DemandUnchangedPointTarget(point, approved);
        await native.MoveAsync(point, ct);
        var heldModifiers = ParseCombo(modifiers);
        var pressedModifiers = new List<string>();
        var mouseDown = false;
        try
        {
            foreach (var modifier in heldModifiers)
            {
                ct.ThrowIfCancellationRequested();
                await native.KeyAsync(modifier, true, ct);
                pressedModifiers.Add(modifier);
            }
            for (var i = 0; i < count; i++)
            {
                Recheck(frameGeneration, layoutHash, session, ct);
                await native.MouseButtonAsync(button, true, ct);
                mouseDown = true;
                ct.ThrowIfCancellationRequested();
                await native.MouseButtonAsync(button, false, ct);
                mouseDown = false;
            }
        }
        finally
        {
            if (mouseDown) await native.MouseButtonAsync(button, false, CancellationToken.None);
            for (var i = pressedModifiers.Count - 1; i >= 0; i--)
                await native.KeyAsync(pressedModifiers[i], false, CancellationToken.None);
        }
    }

    public async Task DragAsync(int x, int y, int toX, int toY, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct)
    {
        var from = PreparePoint(x, y, frameGeneration, layoutHash, session, ct);
        var to = PreparePoint(toX, toY, frameGeneration, layoutHash, session, ct);
        var approved = _targets.DemandPointTarget(from);
        Recheck(frameGeneration, layoutHash, session, ct);
        _targets.DemandUnchangedPointTarget(from, approved);
        await native.MoveAsync(from, ct);
        var down = false;
        try
        {
            await native.MouseButtonAsync("left", true, ct);
            down = true;
            ct.ThrowIfCancellationRequested();
            await native.MoveAsync(to, ct);
        }
        finally
        {
            if (down) await native.MouseButtonAsync("left", false, CancellationToken.None);
        }
    }

    public async Task MoveAsync(int x, int y, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct)
    {
        var point = PreparePoint(x, y, frameGeneration, layoutHash, session, ct);
        Recheck(frameGeneration, layoutHash, session, ct);
        await native.MoveAsync(point, ct);
    }

    public async Task MouseAsync(string state, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct)
    {
        Recheck(frameGeneration, layoutHash, session, ct);
        var point = native.Cursor();
        var approved = _targets.DemandPointTarget(point);
        Recheck(frameGeneration, layoutHash, session, ct);
        _targets.DemandUnchangedPointTarget(point, approved);
        if (state is not ("down" or "up")) throw new DesktopInputRefusedException("mouse state is unsupported");
        if (state == "down")
        {
            lock (_heldMouseGate)
            {
                if (_leftMouseHeld) throw new DesktopInputRefusedException("left mouse button is already held");
            }
            await native.MouseButtonAsync("left", true, ct);
            lock (_heldMouseGate)
            {
                _leftMouseHeld = true;
                _heldMouseStopRegistration = session.Cancel.Token.Register(ReleaseHeldMouseAfterStop);
            }
            session.Cancel.Token.ThrowIfCancellationRequested();
            return;
        }

        CancellationTokenRegistration stopRegistration;
        lock (_heldMouseGate)
        {
            if (!_leftMouseHeld) throw new DesktopInputRefusedException("left mouse button is not held");
            _leftMouseHeld = false;
            stopRegistration = _heldMouseStopRegistration;
            _heldMouseStopRegistration = default;
        }
        stopRegistration.Dispose();
        await native.MouseButtonAsync("left", false, CancellationToken.None);
    }

    public async Task TypeAsync(string text, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct)
    {
        if (text.Length > 2000) throw new DesktopInputRefusedException("typed text is too long");
        Recheck(frameGeneration, layoutHash, session, ct);
        var approved = _targets.DemandFocusedTarget();
        Recheck(frameGeneration, layoutHash, session, ct);
        _targets.DemandUnchangedFocusedTarget(approved);
        await native.TextAsync(text, ct);
    }

    public async Task KeyAsync(string combo, int repeat, TimeSpan hold, long frameGeneration, string layoutHash,
        LocalSession session, CancellationToken ct)
    {
        var normalized = NormalizeCombo(combo);
        if (DangerousChords.Contains(normalized)) throw new DesktopInputRefusedException("dangerous system key chord is prohibited");
        if (repeat is < 1 or > 50 || hold < TimeSpan.Zero || hold > TimeSpan.FromSeconds(10))
            throw new DesktopInputRefusedException("key repeat or hold is out of range");
        Recheck(frameGeneration, layoutHash, session, ct);
        var approved = _targets.DemandFocusedTarget();
        Recheck(frameGeneration, layoutHash, session, ct);
        _targets.DemandUnchangedFocusedTarget(approved);
        var keys = ParseCombo(normalized);
        if (keys.Length == 0) throw new DesktopInputRefusedException("key combo is empty");
        for (var repetition = 0; repetition < repeat; repetition++)
        {
            var pressed = new List<string>();
            try
            {
                foreach (var key in keys)
                {
                    Recheck(frameGeneration, layoutHash, session, ct);
                    await native.KeyAsync(key, true, ct);
                    pressed.Add(key);
                }
                if (hold > TimeSpan.Zero) await Task.Delay(hold, ct);
            }
            finally
            {
                for (var i = pressed.Count - 1; i >= 0; i--)
                    await native.KeyAsync(pressed[i], false, CancellationToken.None);
            }
        }
    }

    public async Task ScrollAsync(int x, int y, string direction, int amount, long frameGeneration, string layoutHash,
        LocalSession session, CancellationToken ct)
    {
        if (direction is not ("up" or "down" or "left" or "right") || amount is < 1 or > 30)
            throw new DesktopInputRefusedException("scroll direction or amount is invalid");
        var point = PreparePoint(x, y, frameGeneration, layoutHash, session, ct);
        Recheck(frameGeneration, layoutHash, session, ct);
        await native.ScrollAsync(point, direction, amount, ct);
    }

    public DesktopPoint Cursor() => native.Cursor();

    private DesktopPoint PreparePoint(int x, int y, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct)
    {
        var frame = Recheck(frameGeneration, layoutHash, session, ct);
        try { return frame.ToVirtualPoint(x, y); }
        catch (ArgumentOutOfRangeException) { throw new DesktopInputRefusedException("coordinate is outside the bound screenshot"); }
    }

    private DesktopFrame Recheck(long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        if (!session.Active || session.Expired || session.Cancel.IsCancellationRequested)
            throw new OperationCanceledException("support session stopped", session.Cancel.Token);
        if (!string.Equals(session.ComputerUseMode, "fix", StringComparison.Ordinal))
            throw new DesktopInputRefusedException("Guide mode cannot inject mouse or keyboard input");
        if (!licenseAllowed()) throw new DesktopInputRefusedException("device license is not active");
        sessionGuard.DemandSafeInteractiveSession();
        var frame = latestFrame() ?? throw new DesktopInputRefusedException("no local screenshot is available");
        if (frame.FrameGeneration != frameGeneration)
            throw new DesktopInputRefusedException("stale frame generation");
        if (!string.Equals(frame.LayoutHash, layoutHash, StringComparison.Ordinal))
            throw new DesktopInputRefusedException("layout hash does not match the bound frame");
        var currentHash = DesktopCapture.HashLayout(pixelSource.ReadLayout());
        if (!string.Equals(currentHash, layoutHash, StringComparison.Ordinal))
            throw new DesktopInputRefusedException("desktop layout changed after the screenshot");
        return frame;
    }

    private void ReleaseHeldMouseAfterStop()
    {
        lock (_heldMouseGate)
        {
            if (!_leftMouseHeld) return;
            _leftMouseHeld = false;
            _heldMouseStopRegistration = default;
        }
        native.MouseButtonAsync("left", false, CancellationToken.None).GetAwaiter().GetResult();
    }

    private static string NormalizeCombo(string value) => string.Join('+', ParseCombo(value));

    private static string[] ParseCombo(string? value) => string.IsNullOrWhiteSpace(value)
        ? []
        : value.Split('+', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries)
            .Select(x => x.ToLowerInvariant() switch { "control" => "ctrl", "windows" => "win", "escape" => "esc", _ => x.ToLowerInvariant() })
            .ToArray();
}
