using System.Drawing;
using JoltIQ.Agent.Core;
using JoltIQ.Agent.Windows;
using JoltIQ.Agent.Windows.Desktop;
using Xunit;

namespace JoltIQ.Agent.Tests;

public sealed class DesktopInputTests
{
    private const string Sid = "S-1-5-21-1000";
    private const string Hash = "e188758123212d41a19b15479a1296fea225af3dd3485563170a4e170846c409";

    [Fact]
    public async Task Click_MapsFrameCoordinatesAndBindsTheSameAutomationTargetBeforeInput()
    {
        var native = new FakeNativeInput();
        var target = SafeTarget("button-1");
        var input = Create(native, new FakeTargets(target, target));

        await input.ClickAsync(25, 10, "left", 1, null, 9, Hash, FixSession(), default);

        Assert.Equal(new[] { "move:-1870,20", "mouse:left:down", "mouse:left:up" }, native.Events);
    }

    [Theory]
    [InlineData(8, Hash, "stale frame")]
    [InlineData(9, "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "layout")]
    public async Task Input_RefusesStaleFrameOrLayout(long generation, string layoutHash, string expected)
    {
        var native = new FakeNativeInput();
        var input = Create(native, new FakeTargets(SafeTarget(), SafeTarget()));

        var error = await Assert.ThrowsAsync<DesktopInputRefusedException>(() =>
            input.ClickAsync(1, 1, "left", 1, null, generation, layoutHash, FixSession(), default));

        Assert.Contains(expected, error.Message, StringComparison.OrdinalIgnoreCase);
        Assert.Empty(native.Events);
    }

    [Fact]
    public async Task Input_RefusesOutOfBoundsCoordinatesAndGuideMode()
    {
        var native = new FakeNativeInput();
        var input = Create(native, new FakeTargets(SafeTarget(), SafeTarget()));

        await Assert.ThrowsAsync<DesktopInputRefusedException>(() =>
            input.ClickAsync(200, 1, "left", 1, null, 9, Hash, FixSession(), default));
        await Assert.ThrowsAsync<DesktopInputRefusedException>(() =>
            input.ClickAsync(1, 1, "left", 1, null, 9, Hash, FixSession("guide"), default));
        Assert.Empty(native.Events);
    }

    [Theory]
    [InlineData(true, "Search", "search", "Edit")]
    [InlineData(false, "Password", "password", "Edit")]
    [InlineData(false, "Verification code", "otp", "Edit")]
    [InlineData(false, "Credit card number", "card-number", "Edit")]
    public async Task Type_RefusesSecretOrSensitiveAutomationTargets(bool password, string name, string automationId, string controlType)
    {
        var native = new FakeNativeInput();
        var unsafeTarget = new InputTargetSnapshot("field-1", password, name, automationId, controlType,
            new DesktopRectangle(-1920, 0, 200, 40));
        var input = Create(native, new FakeTargets(unsafeTarget, unsafeTarget));

        await Assert.ThrowsAsync<DesktopInputRefusedException>(() =>
            input.TypeAsync("hello", 9, Hash, FixSession(), default));
        Assert.Empty(native.Events);
    }

    [Fact]
    public async Task Type_RefusesSecureDesktopUnknownFocusAndChangedElementIdentity()
    {
        var native = new FakeNativeInput();
        var secure = Create(native, new FakeTargets(SafeTarget(), SafeTarget()) { SecureDesktop = true });
        await Assert.ThrowsAsync<DesktopInputRefusedException>(() => secure.TypeAsync("hello", 9, Hash, FixSession(), default));

        var unknown = Create(native, new FakeTargets(null, null));
        await Assert.ThrowsAsync<DesktopInputRefusedException>(() => unknown.TypeAsync("hello", 9, Hash, FixSession(), default));

        var changed = Create(native, new FakeTargets(SafeTarget("one"), SafeTarget("two")));
        await Assert.ThrowsAsync<DesktopInputRefusedException>(() => changed.TypeAsync("hello", 9, Hash, FixSession(), default));
        Assert.Empty(native.Events);
    }

    [Theory]
    [InlineData("ctrl+alt+delete")]
    [InlineData("win+r")]
    [InlineData("win+l")]
    [InlineData("ctrl+shift+esc")]
    public async Task Key_RefusesDangerousSystemChords(string combo)
    {
        var native = new FakeNativeInput();
        var input = Create(native, new FakeTargets(SafeTarget(), SafeTarget()));

        await Assert.ThrowsAsync<DesktopInputRefusedException>(() =>
            input.KeyAsync(combo, 1, TimeSpan.Zero, 9, Hash, FixSession(), default));
        Assert.Empty(native.Events);
    }

    [Fact]
    public async Task HeldMouseAndKeysAreReleasedWhenCancellationArrivesAndNoFurtherInputRunsAfterStop()
    {
        using var stop = new CancellationTokenSource();
        var session = FixSession();
        var native = new FakeNativeInput { Cancel = stop, CancelOn = "mouse:left:down" };
        var input = Create(native, new FakeTargets(SafeTarget(), SafeTarget(), SafeTarget()));

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            input.ClickAsync(1, 1, "left", 3, null, 9, Hash, session, stop.Token));
        Assert.Equal(new[] { "move:-1918,2", "mouse:left:down", "mouse:left:up" }, native.Events);

        using var keyStop = new CancellationTokenSource();
        native = new FakeNativeInput { Cancel = keyStop, CancelOn = "key:ctrl:down" };
        input = Create(native, new FakeTargets(SafeTarget(), SafeTarget()));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            input.KeyAsync("ctrl+s", 1, TimeSpan.FromSeconds(1), 9, Hash, session, keyStop.Token));
        Assert.Equal(new[] { "key:ctrl:down", "key:ctrl:up" }, native.Events);
    }

    [Fact]
    public async Task SeparateMouseDownIsReleasedWhenTheSessionStopArrivesBeforeMouseUp()
    {
        var session = FixSession();
        var native = new FakeNativeInput();
        var input = Create(native, new FakeTargets(SafeTarget(), SafeTarget()));

        await input.MouseAsync("down", 9, Hash, session, default);
        session.Cancel.Cancel();

        Assert.Equal(new[] { "mouse:left:down", "mouse:left:up" }, native.Events);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            input.MouseAsync("up", 9, Hash, session, default));
        Assert.Equal(2, native.Events.Count);
    }

    private static DesktopInput Create(FakeNativeInput native, FakeTargets targets)
    {
        var layout = Layout();
        var source = new FakePixelSource(layout);
        var frame = new DesktopFrame([137, 80, 78, 71], 100, 50, layout.VirtualBounds, 2, 2, Hash, 9, layout.Displays);
        return new DesktopInput(() => frame, source, SupportedGuard(), targets, native, () => true);
    }

    private static DesktopLayout Layout() => new(
        new DesktopRectangle(-1920, 0, 200, 100),
        [new DesktopDisplay("primary", -1920, 0, 200, 100, 96, 96, true)]);

    private static InputTargetSnapshot SafeTarget(string id = "field") =>
        new(id, false, "Notes", "notes", "Edit", new DesktopRectangle(-1920, 0, 200, 100));

    private static LocalSession FixSession(string mode = "fix") => new()
    {
        SessionId = "session",
        Active = true,
        ExpiresAt = DateTimeOffset.UtcNow.AddMinutes(5).ToUnixTimeSeconds(),
        ComputerUseMode = mode,
    };

    private static DesktopSessionGuard SupportedGuard() => new(new FakeSessionInspector());

    private sealed class FakeSessionInspector() : InteractiveSessionInspector(Sid)
    {
        public override IReadOnlyList<InteractiveSessionFact> Sessions() =>
            [new InteractiveSessionFact(1, "user", Sid, true, true, true, false)];
    }

    private sealed class FakePixelSource(DesktopLayout layout) : IDesktopPixelSource
    {
        public DesktopLayout ReadLayout() => layout;
        public Bitmap Capture(DesktopRectangle bounds) => throw new NotSupportedException();
    }

    private sealed class FakeTargets(params InputTargetSnapshot?[] sequence) : IInputTargetInspector
    {
        private readonly Queue<InputTargetSnapshot?> _sequence = new(sequence);
        public bool SecureDesktop { get; init; }
        public bool IsSecureDesktopActive() => SecureDesktop;
        public InputTargetSnapshot? AtPoint(DesktopPoint point) => Next();
        public InputTargetSnapshot? Focused() => Next();
        private InputTargetSnapshot? Next() => _sequence.Count > 1 ? _sequence.Dequeue() : _sequence.Count == 1 ? _sequence.Peek() : null;
    }

    private sealed class FakeNativeInput : INativeInput
    {
        public List<string> Events { get; } = [];
        public CancellationTokenSource? Cancel { get; init; }
        public string? CancelOn { get; init; }
        private Task Add(string value)
        {
            Events.Add(value);
            if (value == CancelOn) Cancel?.Cancel();
            return Task.CompletedTask;
        }
        public Task MoveAsync(DesktopPoint point, CancellationToken ct) => Add($"move:{point.X},{point.Y}");
        public Task MouseButtonAsync(string button, bool down, CancellationToken ct) => Add($"mouse:{button}:{(down ? "down" : "up")}");
        public Task KeyAsync(string key, bool down, CancellationToken ct) => Add($"key:{key}:{(down ? "down" : "up")}");
        public Task TextAsync(string text, CancellationToken ct) => Add($"text:{text}");
        public Task ScrollAsync(DesktopPoint point, string direction, int amount, CancellationToken ct) => Add($"scroll:{point.X},{point.Y}:{direction}:{amount}");
        public DesktopPoint Cursor() => new(5, 6);
    }
}
