using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Automation;

namespace JoltIQ.Agent.Windows.Desktop;

public sealed class DesktopInputRefusedException(string reason) : InvalidOperationException($"Desktop input refused: {reason}");

public sealed record InputTargetSnapshot(
    string RuntimeId,
    bool IsPassword,
    string Name,
    string AutomationId,
    string ControlType,
    DesktopRectangle Bounds);

public interface IInputTargetInspector
{
    bool IsSecureDesktopActive();
    InputTargetSnapshot? AtPoint(DesktopPoint point);
    InputTargetSnapshot? Focused();
}

public sealed class InputTargetGuard(IInputTargetInspector inspector)
{
    private static readonly string[] SecretMarkers =
    [
        "password", "passcode", "one-time", "one time", "otp", "verification code", "security code",
        "credit card", "card number", "card-number", "cvv", "cvc",
    ];

    public InputTargetSnapshot DemandPointTarget(DesktopPoint point)
    {
        DemandNormalDesktop();
        return DemandSafe(inspector.AtPoint(point), "no UI Automation target exists at the approved coordinate");
    }

    public InputTargetSnapshot DemandFocusedTarget()
    {
        DemandNormalDesktop();
        return DemandSafe(inspector.Focused(), "the current keyboard focus is unknown");
    }

    public void DemandUnchangedPointTarget(DesktopPoint point, InputTargetSnapshot approved) =>
        DemandSame(DemandPointTarget(point), approved);

    public void DemandUnchangedFocusedTarget(InputTargetSnapshot approved) =>
        DemandSame(DemandFocusedTarget(), approved);

    private void DemandNormalDesktop()
    {
        if (inspector.IsSecureDesktopActive())
            throw new DesktopInputRefusedException("Windows secure desktop is active");
    }

    private static InputTargetSnapshot DemandSafe(InputTargetSnapshot? target, string missingReason)
    {
        if (target is null) throw new DesktopInputRefusedException(missingReason);
        var label = $"{target.Name} {target.AutomationId} {target.ControlType}".ToLowerInvariant();
        if (target.IsPassword || SecretMarkers.Any(label.Contains))
            throw new DesktopInputRefusedException("password, OTP, card, and other secret-entry controls cannot receive JoltIQ input");
        return target;
    }

    private static void DemandSame(InputTargetSnapshot current, InputTargetSnapshot approved)
    {
        if (!string.Equals(current.RuntimeId, approved.RuntimeId, StringComparison.Ordinal) || current.Bounds != approved.Bounds)
            throw new DesktopInputRefusedException("the UI Automation target changed before input");
    }
}

public sealed class WindowsInputTargetInspector : IInputTargetInspector
{
    public bool IsSecureDesktopActive()
    {
        var desktop = OpenInputDesktop(0, false, DesktopReadObjects);
        if (desktop == IntPtr.Zero) return true;
        try
        {
            uint needed = 0;
            _ = GetUserObjectInformation(desktop, UoiName, null, 0, ref needed);
            if (needed == 0) return true;
            var name = new StringBuilder((int)(needed / sizeof(char)) + 1);
            return !GetUserObjectInformation(desktop, UoiName, name, needed, ref needed) ||
                   !string.Equals(name.ToString(), "Default", StringComparison.OrdinalIgnoreCase);
        }
        finally { _ = CloseDesktop(desktop); }
    }

    public InputTargetSnapshot? AtPoint(DesktopPoint point)
    {
        try { return Snapshot(AutomationElement.FromPoint(new System.Windows.Point(point.X, point.Y))); }
        catch (ElementNotAvailableException) { return null; }
    }

    public InputTargetSnapshot? Focused()
    {
        try { return Snapshot(AutomationElement.FocusedElement); }
        catch (ElementNotAvailableException) { return null; }
    }

    private static InputTargetSnapshot? Snapshot(AutomationElement? element)
    {
        if (element is null) return null;
        try
        {
            var current = element.Current;
            var bounds = current.BoundingRectangle;
            return new InputTargetSnapshot(
                string.Join(".", element.GetRuntimeId()),
                current.IsPassword,
                current.Name ?? "",
                current.AutomationId ?? "",
                current.ControlType?.ProgrammaticName ?? "",
                new DesktopRectangle((int)Math.Floor(bounds.Left), (int)Math.Floor(bounds.Top),
                    Math.Max(0, (int)Math.Ceiling(bounds.Width)), Math.Max(0, (int)Math.Ceiling(bounds.Height))));
        }
        catch (ElementNotAvailableException) { return null; }
    }

    private const uint DesktopReadObjects = 0x0001;
    private const int UoiName = 2;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint desiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool CloseDesktop(IntPtr desktop);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder? info, uint length, ref uint needed);
}
