using System.Diagnostics;
using JoltIQ.Agent.Core;

namespace JoltIQ.Agent.Tray;

public interface ISupportRequestClient
{
    SupportRequestBookmark? ActiveRequest { get; }
    Task<SupportRequestResult> CreateSupportRequestAsync(Guid requestId, string message, CancellationToken ct);
    Task<SupportRequestResult> ContinueSupportRequestAsync(string runId, string message, CancellationToken ct);
    Task<SupportRequestDetail> GetSupportRequestAsync(string runId, CancellationToken ct);
    Task<DirectComputerUseResult> StartComputerUseAsync(string runId, string mode, CancellationToken ct);
}

public sealed class AgentSupportRequestClient(AgentClient client) : ISupportRequestClient
{
    public SupportRequestBookmark? ActiveRequest => client.ActiveSupportRequest;
    public Task<SupportRequestResult> CreateSupportRequestAsync(Guid requestId, string message, CancellationToken ct) =>
        client.CreateSupportRequestAsync(requestId, message, ct);

    public Task<SupportRequestResult> ContinueSupportRequestAsync(string runId, string message, CancellationToken ct) =>
        client.ContinueSupportRequestAsync(runId, message, ct);

    public Task<SupportRequestDetail> GetSupportRequestAsync(string runId, CancellationToken ct) =>
        client.GetSupportRequestAsync(runId, ct);
    public Task<DirectComputerUseResult> StartComputerUseAsync(string runId, string mode, CancellationToken ct) =>
        client.StartComputerUseAsync(runId, mode, ct);
}

public sealed record SupportConversationState
{
    public string Draft { get; init; } = "";
    public IReadOnlyList<SupportConversationMessage> Messages { get; init; } = [];
    public string? TicketId { get; init; }
    public int? TicketNumber { get; init; }
    public string? RunId { get; init; }
    public string Status { get; init; } = "Ready";
    public string? Error { get; init; }
    public bool IsSending { get; init; }
    public bool IsTerminal { get; init; }
    public bool IsAccessBlocked { get; init; }
    public string? ComputerUseSessionId { get; init; }
    public string? ComputerUseRunId { get; init; }
    public string? ComputerUseMode { get; init; }
    public string? ComputerUseStatus { get; init; }
    public bool IsStartingComputerUse { get; init; }
}

public sealed class SupportConversationController
{
    private static readonly HashSet<string> TerminalStates = new(StringComparer.OrdinalIgnoreCase)
    {
        "RESOLVED", "ESCALATED", "FAILED", "CANCELLED", "PARTIALLY_RESOLVED",
    };

    private readonly ISupportRequestClient _client;
    private Guid? _pendingCreateId;
    public SupportConversationState State { get; private set; } = new();
    public event Action<SupportConversationState>? StateChanged;

    public SupportConversationController(ISupportRequestClient client)
    {
        _client = client;
        if (client.ActiveRequest is { } active)
        {
            State = State with
            {
                TicketId = active.TicketId,
                TicketNumber = active.TicketNumber,
                RunId = active.RunId,
                Status = active.State,
            };
        }
    }

    public void SetDraft(string draft) => SetState(State with { Draft = draft, Error = null });

    public async Task SendAsync(CancellationToken ct)
    {
        var message = State.Draft.Trim();
        if (State.IsSending || State.IsAccessBlocked || message.Length == 0) return;
        SetState(State with { IsSending = true, Status = "Sending…", Error = null });
        try
        {
            SupportRequestResult result;
            if (State.RunId is null)
            {
                _pendingCreateId ??= Guid.NewGuid();
                result = await _client.CreateSupportRequestAsync(_pendingCreateId.Value, message, ct);
                _pendingCreateId = null;
            }
            else
            {
                result = await _client.ContinueSupportRequestAsync(State.RunId, message, ct);
            }
            SetState(State with
            {
                Draft = "",
                TicketId = result.TicketId,
                TicketNumber = result.TicketNumber,
                RunId = result.RunId,
                Status = result.State,
                Error = null,
                IsSending = false,
                IsTerminal = TerminalStates.Contains(result.State),
            });
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            SetState(State with { IsSending = false, Status = "Paused" });
            throw;
        }
        catch (DeviceRevokedException)
        {
            SetState(State with
            {
                IsSending = false,
                IsAccessBlocked = true,
                Status = "Access blocked",
                Error = "This device was revoked. Contact your administrator to enroll it again.",
            });
        }
        catch (Exception e) when (e is AgentProtocolException or HttpRequestException or TaskCanceledException)
        {
            SetState(State with
            {
                IsSending = false,
                Status = "Not sent",
                Error = $"Your message was not submitted. Check the connection and try again. ({Redactor.Redact(e.Message)})",
            });
        }
    }

    public async Task RefreshAsync(CancellationToken ct)
    {
        if (State.RunId is null || State.IsTerminal || State.IsAccessBlocked) return;
        try
        {
            var detail = await _client.GetSupportRequestAsync(State.RunId, ct);
            SetState(State with
            {
                TicketId = detail.TicketId,
                RunId = detail.Id,
                Status = detail.State,
                Messages = detail.Messages,
                IsTerminal = TerminalStates.Contains(detail.State),
                Error = null,
            });
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
        catch (DeviceRevokedException)
        {
            SetState(State with
            {
                IsAccessBlocked = true,
                Status = "Access blocked",
                Error = "This device was revoked. Contact your administrator to enroll it again.",
            });
        }
        catch (Exception e) when (e is AgentProtocolException or HttpRequestException or TaskCanceledException)
        {
            SetState(State with { Error = $"Could not refresh yet. ({Redactor.Redact(e.Message)})" });
        }
    }

    public async Task StartComputerUseAsync(string mode, CancellationToken ct)
    {
        if (State.RunId is null || State.IsAccessBlocked || State.IsStartingComputerUse) return;
        if (mode != "guide" && mode != "fix") throw new ArgumentException("Mode must be guide or fix", nameof(mode));
        SetState(State with { IsStartingComputerUse = true, ComputerUseStatus = "Starting…", Error = null });
        try
        {
            var result = await _client.StartComputerUseAsync(State.RunId, mode, ct);
            SetState(State with
            {
                IsStartingComputerUse = false,
                ComputerUseSessionId = result.SessionId,
                ComputerUseRunId = result.ComputerUseRunId,
                ComputerUseMode = result.Mode,
                ComputerUseStatus = result.State == "waiting_session_consent"
                    ? "Waiting for approval on this PC"
                    : result.State.Replace('_', ' '),
            });
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            SetState(State with { IsStartingComputerUse = false, ComputerUseStatus = "Paused" });
            throw;
        }
        catch (DeviceRevokedException)
        {
            SetState(State with { IsStartingComputerUse = false, IsAccessBlocked = true, ComputerUseStatus = null,
                Error = "This device was revoked. Contact your administrator to enroll it again." });
        }
        catch (Exception e) when (e is AgentProtocolException or HttpRequestException or TaskCanceledException)
        {
            SetState(State with { IsStartingComputerUse = false, ComputerUseStatus = null,
                Error = $"Could not start {mode} help. ({Redactor.Redact(e.Message)})" });
        }
    }

    private void SetState(SupportConversationState state)
    {
        State = state;
        StateChanged?.Invoke(state);
    }
}

public sealed class SupportConversationForm : Form
{
    private readonly SupportConversationController _controller;
    private readonly RichTextBox _transcript = new() { ReadOnly = true, Dock = DockStyle.Fill, AccessibleName = "JoltIQ conversation" };
    private readonly Label _status = new() { AutoSize = true, AccessibleName = "Conversation status" };
    private readonly Label _error = new() { AutoSize = false, ForeColor = Color.Firebrick, AccessibleName = "Conversation error" };
    private readonly TextBox _compose = new() { Multiline = true, AcceptsReturn = true, AccessibleName = "Describe your problem" };
    private readonly Button _send = new() { Text = "Send", AccessibleName = "Send message to JoltIQ" };
    private readonly Button _guide = new() { Text = "Guide me", AutoSize = true, AccessibleName = "Have JoltIQ guide me" };
    private readonly Button _fix = new() { Text = "Fix it for me", AutoSize = true, AccessibleName = "Allow JoltIQ to fix it for me" };
    private readonly Label _computerUseStatus = new() { AutoSize = true, ForeColor = SystemColors.GrayText };
    private readonly LinkLabel _portal = new() { Text = "Open ticket in web portal", AutoSize = true, Visible = false };
    private readonly System.Windows.Forms.Timer _poll = new() { Interval = 2000 };
    private readonly CancellationTokenSource _closed = new();
    private readonly string _serverUrl;
    private bool _refreshing;

    public SupportConversationForm(SupportConversationController controller, string serverUrl, string computerName)
    {
        _controller = controller;
        _serverUrl = serverUrl.TrimEnd('/');
        Text = "Ask JoltIQ";
        Width = 720;
        Height = 620;
        MinimumSize = new Size(560, 480);
        StartPosition = FormStartPosition.CenterScreen;

        var heading = new Label
        {
            Text = "What can I help you fix?",
            AutoSize = true,
            Font = new Font("Segoe UI", 15, FontStyle.Bold),
            Margin = new Padding(0, 0, 0, 4),
        };
        var privacy = new Label
        {
            Text = "Starting a conversation does not allow screen viewing or control. JoltIQ asks separately if either is needed.",
            AutoSize = true,
            MaximumSize = new Size(650, 0),
            ForeColor = SystemColors.GrayText,
        };
        var computer = new Label
        {
            Text = $"This computer: {computerName}",
            AutoSize = true,
            ForeColor = SystemColors.GrayText,
            AccessibleName = "Enrolled computer",
        };
        var header = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 112, FlowDirection = FlowDirection.TopDown, Padding = new Padding(12, 10, 12, 4) };
        header.Controls.AddRange([heading, computer, privacy]);

        var footer = new TableLayoutPanel { Dock = DockStyle.Bottom, Height = 205, ColumnCount = 2, RowCount = 4, Padding = new Padding(12, 4, 12, 10) };
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 96));
        footer.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
        footer.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        footer.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
        footer.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        _status.Dock = DockStyle.Fill;
        _portal.Anchor = AnchorStyles.Right;
        _error.Dock = DockStyle.Fill;
        _compose.Dock = DockStyle.Fill;
        _send.Dock = DockStyle.Fill;
        footer.Controls.Add(_status, 0, 0);
        footer.Controls.Add(_portal, 1, 0);
        footer.Controls.Add(_error, 0, 1);
        footer.SetColumnSpan(_error, 2);
        var helpModes = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.LeftToRight, WrapContents = false };
        helpModes.Controls.AddRange([_guide, _fix, _computerUseStatus]);
        footer.Controls.Add(helpModes, 0, 2);
        footer.SetColumnSpan(helpModes, 2);
        footer.Controls.Add(_compose, 0, 3);
        footer.Controls.Add(_send, 1, 3);

        Controls.Add(_transcript);
        Controls.Add(footer);
        Controls.Add(header);

        _compose.TextChanged += (_, _) => _controller.SetDraft(_compose.Text);
        _send.Click += async (_, _) => await SendAsync();
        _guide.Click += async (_, _) => await StartComputerUseAsync("guide");
        _fix.Click += async (_, _) => await StartComputerUseAsync("fix");
        _compose.KeyDown += async (_, e) =>
        {
            if (e.KeyCode == Keys.Enter && e.Modifiers == Keys.Control)
            {
                e.SuppressKeyPress = true;
                await SendAsync();
            }
        };
        _portal.LinkClicked += (_, _) => OpenPortal();
        _controller.StateChanged += Render;
        _poll.Tick += async (_, _) => await RefreshAsync();
        _poll.Start();
        Render(_controller.State);
    }

    private async Task SendAsync()
    {
        try
        {
            await _controller.SendAsync(_closed.Token);
            await _controller.RefreshAsync(_closed.Token);
        }
        catch (OperationCanceledException) when (_closed.IsCancellationRequested) { }
    }

    private async Task RefreshAsync()
    {
        if (_refreshing) return;
        _refreshing = true;
        try { await _controller.RefreshAsync(_closed.Token); }
        catch (OperationCanceledException) when (_closed.IsCancellationRequested) { }
        finally { _refreshing = false; }
    }

    private async Task StartComputerUseAsync(string mode)
    {
        try { await _controller.StartComputerUseAsync(mode, _closed.Token); }
        catch (OperationCanceledException) when (_closed.IsCancellationRequested) { }
    }

    private void Render(SupportConversationState state)
    {
        if (InvokeRequired) { BeginInvoke(() => Render(state)); return; }
        _status.Text = state.TicketNumber is { } number ? $"Ticket #{number} · {FriendlyState(state.Status)}" : FriendlyState(state.Status);
        _error.Text = state.Error ?? "";
        if (_compose.Text != state.Draft) _compose.Text = state.Draft;
        _send.Enabled = !state.IsSending && !state.IsAccessBlocked && !string.IsNullOrWhiteSpace(state.Draft);
        _compose.Enabled = !state.IsSending && !state.IsAccessBlocked;
        _portal.Visible = state.TicketId is not null;
        _portal.Tag = state.TicketId;
        var canStart = state.RunId is not null && !state.IsAccessBlocked && !state.IsStartingComputerUse;
        _guide.Enabled = canStart;
        _fix.Enabled = canStart;
        _guide.Visible = state.RunId is not null;
        _fix.Visible = state.RunId is not null;
        _computerUseStatus.Text = state.ComputerUseStatus ?? "Choose how JoltIQ should help.";
        _transcript.Text = string.Join(
            Environment.NewLine + Environment.NewLine,
            state.Messages.Select(message => $"{(message.Role == "assistant" ? "JoltIQ" : "You")}: {message.Content}"));
    }

    private static string FriendlyState(string state) => state.Replace('_', ' ').ToLowerInvariant() switch
    {
        "ready" => "Ready",
        "not sent" => "Not sent",
        "access blocked" => "Access blocked",
        var value => char.ToUpperInvariant(value[0]) + value[1..],
    };

    private void OpenPortal()
    {
        if (_portal.Tag is not string ticketId) return;
        var url = $"{_serverUrl}/portal/tickets/{Uri.EscapeDataString(ticketId)}";
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        _poll.Stop();
        _closed.Cancel();
        _controller.StateChanged -= Render;
        _closed.Dispose();
        base.OnFormClosed(e);
    }
}
