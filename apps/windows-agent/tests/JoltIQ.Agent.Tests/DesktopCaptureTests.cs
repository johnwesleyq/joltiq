using System.Drawing;
using JoltIQ.Agent.Windows;
using JoltIQ.Agent.Windows.Desktop;
using Xunit;

namespace JoltIQ.Agent.Tests;

public sealed class DesktopCaptureTests
{
    private const string Sid = "S-1-5-21-1000";

    [Fact]
    public async Task Capture_PreservesVirtualCoordinatesAndResizesToMaximumLongEdge()
    {
        var displays = new[]
        {
            new DesktopDisplay("left", -1920, 0, 1920, 1080, 96, 96, false),
            new DesktopDisplay("primary", 0, 0, 2560, 1440, 144, 144, true),
        };
        var source = new FakePixelSource(displays, -1920, 0, 4480, 1440);
        var capture = new DesktopCapture(source, SupportedGuard());

        var frame = await capture.CaptureAsync(1120, default);

        Assert.Equal(1120, frame.Width);
        Assert.Equal(360, frame.Height);
        Assert.Equal(new DesktopRectangle(-1920, 0, 4480, 1440), frame.VirtualBounds);
        Assert.Equal(4, frame.ScaleX, 6);
        Assert.Equal(4, frame.ScaleY, 6);
        Assert.Equal(new DesktopPoint(-1920, 0), frame.ToVirtualPoint(0, 0));
        Assert.Equal(new DesktopPoint(2556, 1436), frame.ToVirtualPoint(1119, 359));
        Assert.Equal(new DesktopPoint(480, 720), frame.ToVirtualPoint(600, 180));
        Assert.True(frame.Png.Length > 8);
        Assert.Equal(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }, frame.Png[..8]);
    }

    [Fact]
    public async Task Capture_HashIsStableButGenerationAlwaysIncreasesAndLayoutChangesHash()
    {
        var source = new FakePixelSource(
            new[] { new DesktopDisplay("primary", 0, 0, 1920, 1080, 96, 96, true) },
            0, 0, 1920, 1080);
        var capture = new DesktopCapture(source, SupportedGuard());

        var first = await capture.CaptureAsync(960, default);
        var second = await capture.CaptureAsync(960, default);
        Assert.Equal(first.LayoutHash, second.LayoutHash);
        Assert.True(second.FrameGeneration > first.FrameGeneration);

        source.SetLayout(
            new[] { new DesktopDisplay("primary", 0, 0, 1080, 1920, 144, 144, true) },
            0, 0, 1080, 1920);
        var changed = await capture.CaptureAsync(960, default);
        Assert.NotEqual(first.LayoutHash, changed.LayoutHash);
        Assert.True(changed.FrameGeneration > second.FrameGeneration);
        Assert.Equal((uint)144, changed.Displays.Single().DpiX);
    }

    [Fact]
    public async Task Capture_DiscardsPixelsIfTheSessionLocksDuringCapture()
    {
        var sessions = new Queue<IReadOnlyList<InteractiveSessionFact>>([
            [new InteractiveSessionFact(1, "user", Sid, true, true, true, false)],
            [new InteractiveSessionFact(1, "user", Sid, true, false, true, false)],
        ]);
        var inspector = new SequencedSessionInspector(Sid, sessions);
        var source = new FakePixelSource(
            new[] { new DesktopDisplay("primary", 0, 0, 100, 100, 96, 96, true) },
            0, 0, 100, 100);
        var capture = new DesktopCapture(source, new DesktopSessionGuard(inspector));

        var error = await Assert.ThrowsAsync<DesktopSessionException>(() => capture.CaptureAsync(100, default));

        Assert.Contains("screen_locked", error.Reasons);
        Assert.Equal(1, source.CaptureCount);
    }

    [Theory]
    [MemberData(nameof(UnsupportedSessions))]
    public async Task Capture_RefusesUnsafeInteractiveSessions(
        IReadOnlyList<InteractiveSessionFact> sessions,
        string enrolledSid,
        string expectedReason)
    {
        var guard = new DesktopSessionGuard(new FakeSessionInspector(enrolledSid, sessions));
        var source = new FakePixelSource(
            new[] { new DesktopDisplay("primary", 0, 0, 100, 100, 96, 96, true) },
            0, 0, 100, 100);
        var capture = new DesktopCapture(source, guard);

        var error = await Assert.ThrowsAsync<DesktopSessionException>(() => capture.CaptureAsync(100, default));

        Assert.Contains(expectedReason, error.Reasons);
        Assert.Equal(0, source.CaptureCount);
    }

    public static IEnumerable<object[]> UnsupportedSessions()
    {
        yield return new object[]
        {
            new[] { new InteractiveSessionFact(1, "user", Sid, true, false, true, false) },
            Sid,
            "screen_locked",
        };
        yield return new object[]
        {
            new[]
            {
                new InteractiveSessionFact(1, "user", Sid, true, true, true, false),
                new InteractiveSessionFact(2, "other", "S-1-5-21-2000", false, false, false, true),
            },
            Sid,
            "multiple_interactive_users",
        };
        yield return new object[]
        {
            new[] { new InteractiveSessionFact(1, "user", Sid, true, true, true, true) },
            Sid,
            "remote_session",
        };
        yield return new object[]
        {
            new[] { new InteractiveSessionFact(1, "other", "S-1-5-21-2000", true, true, true, false) },
            Sid,
            "enrolled_user_mismatch",
        };
    }

    private static DesktopSessionGuard SupportedGuard() => new(new FakeSessionInspector(
        Sid,
        new[] { new InteractiveSessionFact(1, "user", Sid, true, true, true, false) }));

    private sealed class FakeSessionInspector(string enrolledSid, IReadOnlyList<InteractiveSessionFact> sessions)
        : InteractiveSessionInspector(enrolledSid)
    {
        public override IReadOnlyList<InteractiveSessionFact> Sessions() => sessions;
    }

    private sealed class SequencedSessionInspector(
        string enrolledSid,
        Queue<IReadOnlyList<InteractiveSessionFact>> sessions) : InteractiveSessionInspector(enrolledSid)
    {
        public override IReadOnlyList<InteractiveSessionFact> Sessions() => sessions.Count > 1 ? sessions.Dequeue() : sessions.Peek();
    }

    private sealed class FakePixelSource(
        IReadOnlyList<DesktopDisplay> displays,
        int left,
        int top,
        int width,
        int height) : IDesktopPixelSource
    {
        private IReadOnlyList<DesktopDisplay> _displays = displays;
        private DesktopRectangle _bounds = new(left, top, width, height);
        public int CaptureCount { get; private set; }

        public void SetLayout(IReadOnlyList<DesktopDisplay> nextDisplays, int nextLeft, int nextTop, int nextWidth, int nextHeight)
        {
            _displays = nextDisplays;
            _bounds = new(nextLeft, nextTop, nextWidth, nextHeight);
        }

        public DesktopLayout ReadLayout() => new(_bounds, _displays);

        public Bitmap Capture(DesktopRectangle bounds)
        {
            CaptureCount++;
            Assert.Equal(_bounds, bounds);
            return new Bitmap(bounds.Width, bounds.Height);
        }
    }
}
