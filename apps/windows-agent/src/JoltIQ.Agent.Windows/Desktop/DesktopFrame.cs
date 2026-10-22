namespace JoltIQ.Agent.Windows.Desktop;

public sealed record DesktopPoint(int X, int Y);

public sealed record DesktopRectangle(int Left, int Top, int Width, int Height)
{
    public int Right => checked(Left + Width);
    public int Bottom => checked(Top + Height);
}

public sealed record DesktopDisplay(
    string Id,
    int Left,
    int Top,
    int Width,
    int Height,
    uint DpiX,
    uint DpiY,
    bool Primary);

public sealed record DesktopLayout(
    DesktopRectangle VirtualBounds,
    IReadOnlyList<DesktopDisplay> Displays);

public sealed record DesktopFrame(
    byte[] Png,
    int Width,
    int Height,
    DesktopRectangle VirtualBounds,
    double ScaleX,
    double ScaleY,
    string LayoutHash,
    long FrameGeneration,
    IReadOnlyList<DesktopDisplay> Displays)
{
    public DesktopPoint ToVirtualPoint(int scaledX, int scaledY)
    {
        if (scaledX < 0 || scaledX >= Width || scaledY < 0 || scaledY >= Height)
            throw new ArgumentOutOfRangeException(nameof(scaledX), "Point is outside the captured frame");
        return new DesktopPoint(
            VirtualBounds.Left + checked((int)Math.Floor(scaledX * ScaleX)),
            VirtualBounds.Top + checked((int)Math.Floor(scaledY * ScaleY)));
    }
}

public interface IDesktopCapture
{
    Task<DesktopFrame> CaptureAsync(int maxLongEdge, CancellationToken ct);
}
