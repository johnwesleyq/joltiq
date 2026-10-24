using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;
using JoltIQ.Agent.Core;
using JoltIQ.Agent.Windows;
using JoltIQ.Agent.Windows.Desktop;

namespace JoltIQ.Agent.Tray;

/// <summary>
/// Visible, user-owned companion: tray icon, enrollment, consent dialog, always-on-top "session active" banner,
/// kill switch (tray menu + Ctrl+Alt+Shift+F12), local activity log, unenroll. No hidden persistence: autostart is a
/// per-user Run key the installer creates and the user can disable from the tray menu.
/// </summary>
internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        using var mutex = new Mutex(true, @"Local\JoltIQ.Companion", out var first);
        if (!first) return;
        ApplicationConfiguration.Initialize();
        CrashLog.Install();
        Application.Run(new TrayContext(args));
    }
}

internal sealed class TrayContext : ApplicationContext, IUserInteraction
{
    private readonly NotifyIcon _icon;
    private readonly SynchronizationContext _ui;
    private readonly Config _cfg = Config.Load();
    private AgentClient? _client;
    private AgentRuntime? _runtime;
    private CancellationTokenSource? _cts;
    private SessionBanner? _banner;
    private GuideOverlay? _guideOverlay;
    private GuideControlBar? _guideControls;
    private SupportConversationForm? _supportConversation;
    private ToolStripMenuItem? _askItem;
    private string? _activeSession;
    private readonly HotkeyWindow _hotkey;

    public TrayContext(string[] args)
    {
        _ui = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();
        _icon = new NotifyIcon { Icon = SystemIcons.Shield, Text = "JoltIQ Companion", Visible = true, ContextMenuStrip = BuildMenu() };
        _hotkey = new HotkeyWindow(() => StopActive("hotkey"));
        Start();
    }

    private ContextMenuStrip BuildMenu()
    {
        var m = new ContextMenuStrip();
        m.Items.Add("Status…", null, (_, _) => ShowStatus());
        _askItem = new ToolStripMenuItem("Ask JoltIQ…", null, (_, _) => ShowSupportConversation()) { Enabled = false };
        m.Items.Add(_askItem);
        m.Items.Add("Enroll this device…", null, (_, _) => Enroll());
        m.Items.Add(new ToolStripSeparator());
        m.Items.Add("STOP active support session", null, (_, _) => StopActive("tray menu"));
        m.Items.Add("View activity log…", null, (_, _) => ShowLog());
        m.Items.Add(new ToolStripSeparator());
        m.Items.Add("Start with Windows", null, (s, _) => { var it = (ToolStripMenuItem)s!; _cfg.AutoStart = !_cfg.AutoStart; Autostart.Set(_cfg.AutoStart); it.Checked = _cfg.AutoStart; _cfg.Save(); });
        m.Items.Add("Unenroll this device…", null, (_, _) => Unenroll());
        m.Items.Add("Exit", null, (_, _) => ExitThread());
        return m;
    }

    private void Start(AgentClient? enrolledClient = null, DpapiCommandJournal? journal = null)
    {
        if (enrolledClient is null)
        {
            if (string.IsNullOrEmpty(_cfg.ServerUrl)) return;
            var keyStore = new DpapiKeyStore();
            journal = new DpapiCommandJournal(keyStore);
            enrolledClient = new AgentClient(_cfg.ServerUrl, keyStore);
        }

        _client = enrolledClient;
        if (!_client.Enrolled) return;
        journal ??= new DpapiCommandJournal(new DpapiKeyStore());
        var provider = CreateProvider(_client.State.EnrolledUserSid, journal);
        _runtime = new AgentRuntime(_client, provider, this, journal);
        _cts = new CancellationTokenSource();
        _ = Task.Run(() => _runtime.RunAsync(_cts.Token));
        _icon.Text = $"JoltIQ Companion — connected ({_client.State.DeviceId?[..8]})";
        if (_askItem is not null) _askItem.Enabled = true;
    }

    private void Enroll()
    {
        using var f = new EnrollForm(_cfg.ServerUrl);
        if (f.ShowDialog() != DialogResult.OK) return;
        try
        {
            _cts?.Cancel();
            _cfg.ServerUrl = f.ServerUrl; _cfg.Save();
            var keyStore = new DpapiKeyStore();
            var journal = new DpapiCommandJournal(keyStore);
            _client = new AgentClient(f.ServerUrl, keyStore);
            var sid = WindowsIdentity.GetCurrent().User?.Value ?? throw new InvalidOperationException("Cannot identify the enrolling Windows user");
            var prov = CreateProvider(sid, journal);
            _client.EnrollAsync(f.Code, Environment.MachineName, "Windows", Environment.OSVersion.Version.ToString(), prov.Capabilities, sid).GetAwaiter().GetResult();
            EnrollmentCompletion.Run(
                () => Start(_client, journal),
                () => MessageBox.Show("This device is enrolled. Support sessions always ask for your permission first.", "JoltIQ", MessageBoxButtons.OK, MessageBoxIcon.Information));
        }
        catch (Exception e) { MessageBox.Show(Redactor.Redact(e.Message), "Enrollment failed", MessageBoxButtons.OK, MessageBoxIcon.Error); }
    }

    private void Unenroll()
    {
        if (_client is null || !_client.Enrolled) return;
        if (MessageBox.Show("Remove this device from your support organization?", "JoltIQ", MessageBoxButtons.YesNo) != DialogResult.Yes) return;
        StopActive("unenroll");
        try { _client.SendAsync(HttpMethod.Post, "/unenroll", new { }).GetAwaiter().GetResult(); } catch (Exception) { /* revoke locally anyway */ }
        _cts?.Cancel();
        _client.ForgetEnrollment();
        _supportConversation?.Close();
        _supportConversation = null;
        if (_askItem is not null) _askItem.Enabled = false;
        _icon.Text = "JoltIQ Companion — not enrolled";
    }

    private void ShowSupportConversation()
    {
        if (_client?.Enrolled != true) return;
        if (_supportConversation is { IsDisposed: false })
        {
            _supportConversation.Show();
            _supportConversation.Activate();
            return;
        }
        var controller = new SupportConversationController(new AgentSupportRequestClient(_client));
        _supportConversation = new SupportConversationForm(controller, _client.State.ServerUrl, Environment.MachineName);
        _supportConversation.FormClosed += (_, _) => _supportConversation = null;
        _supportConversation.Show();
    }

    private void StopActive(string via)
    {
        if (_activeSession is { } sid && _runtime is not null) _ = _runtime.StopFromDeviceAsync(sid);
    }

    private void ShowStatus() => MessageBox.Show(_client?.Enrolled == true
        ? $"Enrolled as device {_client.State.DeviceId}\nServer: {_client.State.ServerUrl}\nActive session: {_activeSession ?? "none"}"
        : "Not enrolled.", "JoltIQ Companion");

    private void ShowLog()
    {
        var lines = _runtime?.Log.TakeLast(200).Select(l => $"{l.At:HH:mm:ss} {l.Level,-8} {l.Message}") ?? Array.Empty<string>();
        using var f = new Form { Text = "JoltIQ activity (this device)", Width = 800, Height = 500 };
        f.Controls.Add(new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both, Dock = DockStyle.Fill, Text = string.Join(Environment.NewLine, lines), Font = new Font("Consolas", 9) });
        f.ShowDialog();
    }

    // ---- IUserInteraction ----
    public string CurrentUserDisplayName => Environment.UserName;

    public Task<bool> AskConsentAsync(string sessionId, NativeConsentRequest request, TimeSpan timeout, CancellationToken ct)
    {
        var tcs = new TaskCompletionSource<bool>();
        _ui.Post(_ =>
        {
            using var dlg = new ConsentForm(request, timeout);
            tcs.TrySetResult(dlg.ShowDialog() == DialogResult.Yes);
        }, null);
        return tcs.Task;
    }

    public void SessionStarted(string sessionId, string activeMessage, Action stopRequested) => _ui.Post(_ =>
    {
        _activeSession = sessionId;
        _banner = new SessionBanner(activeMessage, stopRequested);
        _banner.Show();
        _icon.Icon = SystemIcons.Warning;
        _icon.ShowBalloonTip(5000, "Support session active", activeMessage, ToolTipIcon.Warning);
    }, null);

    public void SessionEnded(string sessionId, string reason) => _ui.Post(_ =>
    {
        if (_activeSession == sessionId) _activeSession = null;
        _banner?.Close(); _banner = null;
        _icon.Icon = SystemIcons.Shield;
        _icon.ShowBalloonTip(3000, "Support session ended", reason, ToolTipIcon.Info);
    }, null);

    public void ActionPerformed(string sessionId, string summary) => _ui.Post(_ => _banner?.SetLast(summary), null);

    public Task<bool> AskComputerUseConsentAsync(ComputerUseConsentRequest request, TimeSpan timeout, CancellationToken ct)
    {
        var tcs = new TaskCompletionSource<bool>();
        _ui.Post(_ =>
        {
            using var dlg = new ComputerUseConsentForm(request, timeout);
            tcs.TrySetResult(dlg.ShowDialog() == DialogResult.Yes);
        }, null);
        return tcs.Task;
    }

    public void ComputerUseStarted(ComputerUseGrant grant, Func<GuideResponse, Task> respond, Action stopRequested) => _ui.Post(_ =>
    {
        _guideControls?.Close();
        if (grant.Mode == "guide")
        {
            _guideControls = new GuideControlBar(grant.Goal, respond, stopRequested);
            _guideControls.Show();
        }
        _icon.ShowBalloonTip(4000, $"JoltIQ {grant.Mode} started", grant.Goal, ToolTipIcon.Info);
    }, null);

    public void ComputerUseEnded(string runId, string reason) => _ui.Post(_ =>
    {
        _guideOverlay?.Close(); _guideOverlay = null;
        _guideControls?.Close(); _guideControls = null;
    }, null);

    public void ShowGuide(GuideInstruction instruction) => _ui.Post(_ =>
    {
        _guideOverlay ??= new GuideOverlay();
        _guideOverlay.SetInstruction(instruction);
    }, null);

    public void ClearGuide() => _ui.Post(_ =>
    {
        _guideOverlay?.Close(); _guideOverlay = null;
    }, null);

    private WindowsActionProvider CreateProvider(string? enrolledUserSid, ICommandJournal journal)
    {
        if (string.IsNullOrWhiteSpace(enrolledUserSid))
            return new WindowsActionProvider(enrolledUserSid, journal, ActionHost.InteractiveTray);
        var source = new WindowsDesktopPixelSource();
        var guard = new DesktopSessionGuard(new InteractiveSessionInspector(enrolledUserSid));
        var capture = new DesktopCapture(source, guard);
        WindowsActionProvider? provider = null;
        var input = new DesktopInput(
            () => provider?.LatestFrame,
            source,
            guard,
            new WindowsInputTargetInspector(),
            new NativeInput(),
            () => _client?.Enrolled == true);
        provider = new WindowsActionProvider(
            enrolledUserSid,
            journal,
            ActionHost.InteractiveTray,
            desktopCapture: capture,
            desktopInput: input,
            sessionResolver: sessionId => sessionId is not null && _runtime?.Sessions.TryGetValue(sessionId, out var session) == true ? session : null,
            showGuide: ShowGuide,
            clearGuide: ClearGuide,
            urlLauncher: uri => Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }) is not null);
        return provider;
    }

    protected override void ExitThreadCore()
    {
        StopActive("exit");
        _cts?.Cancel();
        _supportConversation?.Close();
        _icon.Visible = false;
        _hotkey.Dispose();
        base.ExitThreadCore();
    }
}

internal sealed class ComputerUseConsentForm : Form
{
    public ComputerUseConsentForm(ComputerUseConsentRequest request, TimeSpan timeout)
    {
        Text = $"JoltIQ — allow {request.Mode}?"; TopMost = true; FormBorderStyle = FormBorderStyle.FixedDialog;
        StartPosition = FormStartPosition.CenterScreen; Width = 600; Height = 330; MaximizeBox = MinimizeBox = false;
        var permission = request.Mode == "guide"
            ? "Guide mode can see the screen and point out what to click. It cannot click or type."
            : "Fix mode can see the screen, click, type non-secret text, and open web pages for this goal.";
        Controls.Add(new Label { Text = $"Requested goal:\n\n{request.Goal}\n\n{permission}\n\nThis permission applies only to this exact request.",
            Left = 20, Top = 18, Width = 545, Height = 210, Font = new Font("Segoe UI", 10) });
        var allow = new Button { Text = $"Allow {request.Mode}", DialogResult = DialogResult.Yes, Width = 140, Left = 285, Top = 240 };
        var deny = new Button { Text = "Decline", DialogResult = DialogResult.No, Width = 120, Left = 440, Top = 240 };
        Controls.AddRange(new Control[] { allow, deny });
        AcceptButton = deny; CancelButton = deny;
        var timer = new System.Windows.Forms.Timer { Interval = (int)timeout.TotalMilliseconds };
        timer.Tick += (_, _) => { timer.Stop(); DialogResult = DialogResult.No; };
        timer.Start();
    }
}

internal sealed class ConsentForm : Form
{
    public ConsentForm(NativeConsentRequest request, TimeSpan timeout)
    {
        Text = request.Title; TopMost = true; FormBorderStyle = FormBorderStyle.FixedDialog; StartPosition = FormStartPosition.CenterScreen;
        Width = 560; Height = 300; MaximizeBox = MinimizeBox = false;
        var body = new Label { Text = request.Body, Dock = DockStyle.Top, Height = 120, Padding = new Padding(16), Font = new Font(Font.FontFamily, 10) };
        var identity = new Label {
            Text = request.AddinName is null ? "" : $"Add-in: {request.AddinName}\nID: {request.AddinProgId}",
            Dock = DockStyle.Top, Height = request.AddinName is null ? 0 : 54, Padding = new Padding(16, 2, 16, 2),
            ForeColor = SystemColors.GrayText,
        };
        var allow = new Button { Text = "Allow", DialogResult = DialogResult.Yes, Width = 120, Left = 290, Top = 205 };
        var deny = new Button { Text = "Deny", DialogResult = DialogResult.No, Width = 120, Left = 420, Top = 205 };
        Controls.AddRange(new Control[] { identity, body, allow, deny });
        AcceptButton = deny; CancelButton = deny;  // safe default: Enter/Esc deny
        var t = new System.Windows.Forms.Timer { Interval = (int)timeout.TotalMilliseconds };
        t.Tick += (_, _) => { t.Stop(); DialogResult = DialogResult.No; };  // no answer = denied
        t.Start();
    }
}

internal sealed class SessionBanner : Form
{
    private readonly Label _last = new() { AutoSize = true, ForeColor = Color.White, Top = 50, Left = 12 };
    public SessionBanner(string activeMessage, Action stop)
    {
        FormBorderStyle = FormBorderStyle.None; TopMost = true; ShowInTaskbar = false; BackColor = Color.FromArgb(180, 30, 30);
        Width = 720; Height = 80; StartPosition = FormStartPosition.Manual;
        var wa = Screen.PrimaryScreen!.WorkingArea; Left = wa.Right - Width - 12; Top = wa.Top + 12;
        Controls.Add(new Label { Text = activeMessage, ForeColor = Color.White, AutoSize = false, Width = Width - 112,
            Height = 40, Top = 8, Left = 12, Font = new Font(Font, FontStyle.Bold) });
        Controls.Add(_last);
        var b = new Button { Text = "STOP", Width = 80, Height = 48, Left = Width - 92, Top = 12, BackColor = Color.White };
        b.Click += (_, _) => stop();
        Controls.Add(b);
    }
    public void SetLast(string s) => _last.Text = "Last: " + s;
    protected override bool ShowWithoutActivation => true;
}

public static class EnrollmentCompletion
{
    public static void Run(Action startRuntime, Action showSuccessNotice)
    {
        ArgumentNullException.ThrowIfNull(startRuntime);
        ArgumentNullException.ThrowIfNull(showSuccessNotice);
        startRuntime();
        showSuccessNotice();
    }
}

internal sealed class EnrollForm : Form
{
    private readonly TextBox _url = new() { Width = 360, Left = 120, Top = 20 };
    private readonly TextBox _code = new() { Width = 200, Left = 120, Top = 56, CharacterCasing = CharacterCasing.Upper };
    public string ServerUrl => _url.Text.Trim();
    public string Code => _code.Text.Trim();
    public EnrollForm(string url)
    {
        Text = "Enroll this device"; Width = 520; Height = 190; FormBorderStyle = FormBorderStyle.FixedDialog; StartPosition = FormStartPosition.CenterScreen;
        _url.Text = string.IsNullOrEmpty(url) ? "https://app.joltiq.io" : url;
        Controls.Add(new Label { Text = "Server", Left = 16, Top = 24 }); Controls.Add(_url);
        Controls.Add(new Label { Text = "Enrollment code", Left = 16, Top = 60, Width = 100 }); Controls.Add(_code);
        var ok = new Button { Text = "Enroll", DialogResult = DialogResult.OK, Left = 380, Top = 100 };
        Controls.Add(ok); AcceptButton = ok;
    }
}

internal sealed class HotkeyWindow : NativeWindow, IDisposable
{
    private const int WM_HOTKEY = 0x0312, ID = 0x4A51;
    private readonly Action _onHotkey;
    [DllImport("user32.dll")] private static extern bool RegisterHotKey(IntPtr h, int id, uint mods, uint vk);
    [DllImport("user32.dll")] private static extern bool UnregisterHotKey(IntPtr h, int id);
    public HotkeyWindow(Action onHotkey)
    {
        _onHotkey = onHotkey;
        CreateHandle(new CreateParams());
        RegisterHotKey(Handle, ID, 0x0002 | 0x0001 | 0x0004 | 0x4000 /*Ctrl+Alt+Shift, no repeat*/, 0x7B /*F12*/);
    }
    protected override void WndProc(ref Message m) { if (m.Msg == WM_HOTKEY) _onHotkey(); base.WndProc(ref m); }
    public void Dispose() { UnregisterHotKey(Handle, ID); DestroyHandle(); }
}

internal sealed class Config
{
    public string ServerUrl { get; set; } = "";
    public bool AutoStart { get; set; } = true;
    private static string PathOf => System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "JoltIQ", "Agent", "config.json");
    public static Config Load() => File.Exists(PathOf) ? System.Text.Json.JsonSerializer.Deserialize<Config>(File.ReadAllText(PathOf)) ?? new() : new();
    public void Save() { Directory.CreateDirectory(System.IO.Path.GetDirectoryName(PathOf)!); File.WriteAllText(PathOf, System.Text.Json.JsonSerializer.Serialize(this)); }
}

internal static class Autostart
{
    public static void Set(bool on)
    {
        using var k = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");
        if (on) k.SetValue("JoltIQCompanion", $"\"{Environment.ProcessPath}\""); else k.DeleteValue("JoltIQCompanion", false);
    }
}

/// <summary>Crash logs are redacted and kept locally (10 files max); never auto-uploaded.</summary>
internal static class CrashLog
{
    public static void Install()
    {
        var dir = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "JoltIQ", "Agent", "crash");
        Directory.CreateDirectory(dir);
        void Write(Exception? e)
        {
            File.WriteAllText(System.IO.Path.Combine(dir, $"crash-{DateTime.UtcNow:yyyyMMddHHmmss}.log"), Redactor.Redact(e?.ToString() ?? "unknown"));
            foreach (var old in new DirectoryInfo(dir).GetFiles().OrderByDescending(f => f.CreationTimeUtc).Skip(10)) old.Delete();
        }
        Application.ThreadException += (_, e) => Write(e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, e) => Write(e.ExceptionObject as Exception);
    }
}
