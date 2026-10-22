using JoltIQ.Agent.Core;

namespace JoltIQ.Agent.Tray;

/// <summary>Testable invariants for the visual-only Guide layer.</summary>
public static class GuideOverlaySecurity
{
    public const bool NoActivate = true;
    public const bool ClickThrough = true;
    public const bool TopMost = true;

    public static bool IsValidGeometry(int x, int y, int width, int height, int frameWidth, int frameHeight) =>
        frameWidth is >= 1 and <= 10000 && frameHeight is >= 1 and <= 10000 &&
        x >= 0 && y >= 0 && width >= 1 && height >= 1 &&
        x <= frameWidth - width && y <= frameHeight - height;
}

/// <summary>A transparent, click-through highlight. It cannot receive focus or input.</summary>
public sealed class GuideOverlay : Form
{
    private const int WS_EX_TRANSPARENT = 0x20;
    private const int WS_EX_NOACTIVATE = 0x08000000;
    private const int WS_EX_TOOLWINDOW = 0x80;
    private const int WM_NCHITTEST = 0x84;
    private const int HTTRANSPARENT = -1;
    private Rectangle _highlight;
    private string _instruction = "";

    public GuideOverlay()
    {
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        TopMost = true;
        StartPosition = FormStartPosition.Manual;
        Bounds = SystemInformation.VirtualScreen;
        BackColor = Color.Magenta;
        TransparencyKey = Color.Magenta;
    }

    protected override bool ShowWithoutActivation => true;
    protected override CreateParams CreateParams
    {
        get
        {
            var cp = base.CreateParams;
            cp.ExStyle |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
            return cp;
        }
    }

    public void SetInstruction(GuideInstruction instruction)
    {
        if (!GuideOverlaySecurity.IsValidGeometry(
                instruction.X, instruction.Y, instruction.Width, instruction.Height,
                instruction.FrameWidth, instruction.FrameHeight))
            throw new ArgumentOutOfRangeException(nameof(instruction), "Guide geometry is invalid");
        var virtualScreen = SystemInformation.VirtualScreen;
        var sx = virtualScreen.Width / (double)instruction.FrameWidth;
        var sy = virtualScreen.Height / (double)instruction.FrameHeight;
        _highlight = new Rectangle(
            (int)Math.Round(instruction.X * sx), (int)Math.Round(instruction.Y * sy),
            Math.Max(24, (int)Math.Round(instruction.Width * sx)), Math.Max(24, (int)Math.Round(instruction.Height * sy)));
        _instruction = instruction.Instruction;
        Bounds = virtualScreen;
        Invalidate();
        if (!Visible) Show();
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        if (_highlight.IsEmpty) return;
        using var pen = new Pen(Color.FromArgb(255, 255, 196, 0), 5);
        e.Graphics.DrawRectangle(pen, _highlight);
        var bubble = new Rectangle(_highlight.Left, Math.Max(8, _highlight.Top - 54), Math.Min(520, Width - _highlight.Left - 8), 44);
        using var bg = new SolidBrush(Color.FromArgb(235, 20, 25, 35));
        using var fg = new SolidBrush(Color.White);
        using var font = new Font("Segoe UI", 10, FontStyle.Bold);
        using var format = new StringFormat { LineAlignment = StringAlignment.Center };
        e.Graphics.FillRectangle(bg, bubble);
        e.Graphics.DrawString(_instruction, font, fg, bubble, format);
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WM_NCHITTEST) { m.Result = (IntPtr)HTTRANSPARENT; return; }
        base.WndProc(ref m);
    }
}

internal sealed class GuideControlBar : Form
{
    public GuideControlBar(string goal, Func<GuideResponse, Task> respond, Action stop)
    {
        Text = "JoltIQ Guide"; TopMost = true; ShowInTaskbar = false; FormBorderStyle = FormBorderStyle.FixedToolWindow;
        Width = 560; Height = 120; StartPosition = FormStartPosition.Manual;
        var wa = Screen.PrimaryScreen!.WorkingArea; Left = wa.Right - Width - 16; Top = wa.Bottom - Height - 16;
        Controls.Add(new Label { Text = goal, Left = 12, Top = 10, Width = 520, Height = 36, AutoEllipsis = true });
        var done = new Button { Text = "I did it", Left = 150, Top = 52, Width = 110 };
        var fix = new Button { Text = "Switch to Fix", Left = 270, Top = 52, Width = 120 };
        var stopButton = new Button { Text = "Stop", Left = 400, Top = 52, Width = 110 };
        done.Click += async (_, _) => await respond(GuideResponse.Done);
        fix.Click += async (_, _) => await respond(GuideResponse.SwitchToFix);
        stopButton.Click += (_, _) => stop();
        Controls.AddRange(new Control[] { done, fix, stopButton });
    }
}
