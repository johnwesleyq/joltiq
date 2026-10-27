using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Forms;

namespace JoltIQ.Agent.Windows.Desktop;

public interface IDesktopPixelSource
{
    DesktopLayout ReadLayout();
    Bitmap Capture(DesktopRectangle bounds);
}

public sealed class DesktopCapture(
    IDesktopPixelSource source,
    DesktopSessionGuard sessionGuard) : IDesktopCapture
{
    private long _generation;

    public Task<DesktopFrame> CaptureAsync(int maxLongEdge, CancellationToken ct)
    {
        if (maxLongEdge is < 1 or > 4096)
            throw new ArgumentOutOfRangeException(nameof(maxLongEdge), "Maximum long edge must be between 1 and 4096 pixels");
        sessionGuard.DemandSafeInteractiveSession();
        ct.ThrowIfCancellationRequested();

        var layout = source.ReadLayout();
        ValidateLayout(layout);
        var primary = layout.Displays.Single(x => x.Primary);
        var captureBounds = new DesktopRectangle(primary.Left, primary.Top, primary.Width, primary.Height);
        using var original = source.Capture(captureBounds);
        ct.ThrowIfCancellationRequested();
        sessionGuard.DemandSafeInteractiveSession();
        var confirmedLayout = source.ReadLayout();
        ValidateLayout(confirmedLayout);
        if (!CryptographicOperations.FixedTimeEquals(
                Convert.FromHexString(HashLayout(layout)),
                Convert.FromHexString(HashLayout(confirmedLayout))))
            throw new InvalidOperationException("Desktop layout changed during capture");

        var ratio = Math.Min(1d, maxLongEdge / (double)Math.Max(original.Width, original.Height));
        var width = Math.Max(1, (int)Math.Round(original.Width * ratio, MidpointRounding.AwayFromZero));
        var height = Math.Max(1, (int)Math.Round(original.Height * ratio, MidpointRounding.AwayFromZero));
        using var encoded = width == original.Width && height == original.Height
            ? new Bitmap(original)
            : Resize(original, width, height);
        using var stream = new MemoryStream();
        encoded.Save(stream, ImageFormat.Png);
        ct.ThrowIfCancellationRequested();

        return Task.FromResult(new DesktopFrame(
            stream.ToArray(),
            width,
            height,
            captureBounds,
            captureBounds.Width / (double)width,
            captureBounds.Height / (double)height,
            HashLayout(layout),
            Interlocked.Increment(ref _generation),
            layout.Displays.ToArray()));
    }

    private static Bitmap Resize(Bitmap source, int width, int height)
    {
        var result = new Bitmap(width, height, PixelFormat.Format32bppArgb);
        using var graphics = Graphics.FromImage(result);
        graphics.CompositingMode = CompositingMode.SourceCopy;
        graphics.CompositingQuality = CompositingQuality.HighQuality;
        graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
        graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
        graphics.SmoothingMode = SmoothingMode.HighQuality;
        graphics.DrawImage(source, new Rectangle(0, 0, width, height));
        return result;
    }

    private static void ValidateLayout(DesktopLayout layout)
    {
        if (layout.VirtualBounds.Width <= 0 || layout.VirtualBounds.Height <= 0 || layout.Displays.Count == 0)
            throw new InvalidOperationException("Windows returned an empty desktop layout");
        if (layout.Displays.Any(x => x.Width <= 0 || x.Height <= 0 || x.DpiX == 0 || x.DpiY == 0))
            throw new InvalidOperationException("Windows returned invalid display geometry");
        if (layout.Displays.Count(x => x.Primary) != 1)
            throw new InvalidOperationException("Windows returned an invalid primary display layout");
    }

    internal static string HashLayout(DesktopLayout layout)
    {
        var value = new StringBuilder()
            .Append(layout.VirtualBounds.Left).Append('|')
            .Append(layout.VirtualBounds.Top).Append('|')
            .Append(layout.VirtualBounds.Width).Append('|')
            .Append(layout.VirtualBounds.Height);
        foreach (var display in layout.Displays.OrderBy(x => x.Id, StringComparer.Ordinal))
            value.Append(';').Append(display.Id).Append('|').Append(display.Left).Append('|').Append(display.Top)
                .Append('|').Append(display.Width).Append('|').Append(display.Height).Append('|').Append(display.DpiX)
                .Append('|').Append(display.DpiY).Append('|').Append(display.Primary);
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value.ToString()))).ToLowerInvariant();
    }
}

public sealed class WindowsDesktopPixelSource : IDesktopPixelSource
{
    public DesktopLayout ReadLayout()
    {
        var virtualScreen = SystemInformation.VirtualScreen;
        var displays = Screen.AllScreens.Select(screen =>
        {
            var point = new NativePoint(screen.Bounds.Left, screen.Bounds.Top);
            var monitor = MonitorFromPoint(point, MonitorDefaultToNearest);
            uint dpiX = 96;
            uint dpiY = 96;
            _ = GetDpiForMonitor(monitor, MonitorDpiType.Effective, out dpiX, out dpiY);
            return new DesktopDisplay(
                screen.DeviceName,
                screen.Bounds.Left,
                screen.Bounds.Top,
                screen.Bounds.Width,
                screen.Bounds.Height,
                dpiX,
                dpiY,
                screen.Primary);
        }).ToArray();
        return new DesktopLayout(
            new DesktopRectangle(virtualScreen.Left, virtualScreen.Top, virtualScreen.Width, virtualScreen.Height),
            displays);
    }

    public Bitmap Capture(DesktopRectangle bounds)
    {
        var bitmap = new Bitmap(bounds.Width, bounds.Height, PixelFormat.Format32bppArgb);
        try
        {
            using var graphics = Graphics.FromImage(bitmap);
            graphics.CopyFromScreen(bounds.Left, bounds.Top, 0, 0, bitmap.Size, CopyPixelOperation.SourceCopy);
            return bitmap;
        }
        catch
        {
            bitmap.Dispose();
            throw;
        }
    }

    private const uint MonitorDefaultToNearest = 2;

    [StructLayout(LayoutKind.Sequential)]
    private readonly record struct NativePoint(int X, int Y);

    private enum MonitorDpiType { Effective = 0 }

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromPoint(NativePoint point, uint flags);

    [DllImport("shcore.dll")]
    private static extern int GetDpiForMonitor(IntPtr monitor, MonitorDpiType type, out uint dpiX, out uint dpiY);
}
