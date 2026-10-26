using JoltIQ.Agent.Core;
using JoltIQ.Agent.Tray;
using Xunit;

namespace JoltIQ.Agent.Tests;

public sealed class SupportConversationTests
{
    [Theory]
    [InlineData(SupportPromptKind.LocalSessionConsent)]
    [InlineData(SupportPromptKind.ConsequentialActionApproval)]
    [InlineData(SupportPromptKind.OrdinaryQuestion)]
    public void ActionablePromptRequestsForegroundExactlyOnce(SupportPromptKind kind)
    {
        var window = new FakePromptWindow();
        var foreground = new SupportPromptForeground();

        var result = foreground.ShowForegroundPrompt("prompt-1", kind, window, screenLocked: false);

        Assert.Equal(ForegroundPromptResult.Activated, result);
        Assert.Equal(1, window.ShowCalls);
        Assert.Equal(1, window.ActivateCalls);
    }

    [Fact]
    public void StatusUpdateNeverRequestsForeground()
    {
        var window = new FakePromptWindow();
        var foreground = new SupportPromptForeground();

        var result = foreground.ShowForegroundPrompt("status-1", SupportPromptKind.StatusUpdate, window, screenLocked: false);

        Assert.Equal(ForegroundPromptResult.Ignored, result);
        Assert.Equal(0, window.ShowCalls);
        Assert.Equal(0, window.ActivateCalls);
    }

    [Fact]
    public void DuplicatePromptDoesNotRequestForegroundAgain()
    {
        var window = new FakePromptWindow();
        var foreground = new SupportPromptForeground();

        foreground.ShowForegroundPrompt("approval-1", SupportPromptKind.ConsequentialActionApproval, window, screenLocked: false);
        var duplicate = foreground.ShowForegroundPrompt("approval-1", SupportPromptKind.ConsequentialActionApproval, window, screenLocked: false);

        Assert.Equal(ForegroundPromptResult.Duplicate, duplicate);
        Assert.Equal(1, window.ActivateCalls);
    }

    [Fact]
    public void LockedScreenBlocksForegroundAndRecordsDiagnostic()
    {
        var diagnostics = new List<string>();
        var window = new FakePromptWindow();
        var foreground = new SupportPromptForeground(diagnostics.Add);

        var result = foreground.ShowForegroundPrompt("consent-locked", SupportPromptKind.LocalSessionConsent, window, screenLocked: true);

        Assert.Equal(ForegroundPromptResult.Blocked, result);
        Assert.Equal(0, window.ActivateCalls);
        Assert.Contains("screen_locked", Assert.Single(diagnostics), StringComparison.Ordinal);
    }

    [Fact]
    public void AlreadyVisiblePromptIsActivatedWithoutShowingOrRestoringIt()
    {
        var window = new FakePromptWindow { IsVisible = true };
        var foreground = new SupportPromptForeground();

        var result = foreground.ShowForegroundPrompt("question-1", SupportPromptKind.OrdinaryQuestion, window, screenLocked: false);

        Assert.Equal(ForegroundPromptResult.Activated, result);
        Assert.Equal(0, window.ShowCalls);
        Assert.Equal(0, window.RestoreCalls);
        Assert.Equal(1, window.ActivateCalls);
    }

    [Fact]
    public async Task FirstSendCreatesDeviceRequestAndClearsDraftOnlyAfterSuccess()
    {
        var client = new FakeSupportClient();
        var controller = new SupportConversationController(client);
        controller.SetDraft("Outlook cannot find Adobe Sign.");

        await controller.SendAsync(default);

        Assert.Single(client.Creates);
        Assert.Equal("Outlook cannot find Adobe Sign.", client.Creates[0].Message);
        Assert.Equal("run-1", controller.State.RunId);
        Assert.Equal(42, controller.State.TicketNumber);
        Assert.Empty(controller.State.Draft);
        Assert.False(controller.State.IsSending);
    }

    [Fact]
    public async Task FailedFirstSendRetainsDraftAndRetryReusesRequestId()
    {
        var client = new FakeSupportClient { FailCreates = 1 };
        var controller = new SupportConversationController(client);
        controller.SetDraft("Retry this exact request.");

        await controller.SendAsync(default);
        Assert.Equal("Retry this exact request.", controller.State.Draft);
        Assert.NotNull(controller.State.Error);
        await controller.SendAsync(default);

        Assert.Equal(2, client.Creates.Count);
        Assert.Equal(client.Creates[0].RequestId, client.Creates[1].RequestId);
        Assert.Empty(controller.State.Draft);
    }

    [Fact]
    public async Task LaterMessagesContinueTheExistingRun()
    {
        var client = new FakeSupportClient();
        var controller = new SupportConversationController(client);
        controller.SetDraft("First message");
        await controller.SendAsync(default);
        controller.SetDraft("Second message");

        await controller.SendAsync(default);

        Assert.Single(client.Creates);
        Assert.Single(client.Continues);
        Assert.Equal(("run-1", "Second message"), client.Continues[0]);
    }

    [Fact]
    public async Task ReopenedConversationContinuesThePersistedRun()
    {
        var client = new FakeSupportClient();
        var firstWindow = new SupportConversationController(client);
        firstWindow.SetDraft("First message");
        await firstWindow.SendAsync(default);

        var reopenedWindow = new SupportConversationController(client);
        Assert.Equal("run-1", reopenedWindow.State.RunId);
        Assert.Equal(42, reopenedWindow.State.TicketNumber);
        reopenedWindow.SetDraft("Second message after reopening");
        await reopenedWindow.SendAsync(default);

        Assert.Single(client.Creates);
        Assert.Single(client.Continues);
        Assert.Equal(("run-1", "Second message after reopening"), client.Continues[0]);
    }

    [Fact]
    public async Task RefreshShowsServerTranscriptAndStopsPollingAtTerminalState()
    {
        var client = new FakeSupportClient();
        var controller = new SupportConversationController(client);
        controller.SetDraft("Start");
        await controller.SendAsync(default);
        client.Detail = Detail("RESOLVED", "The verified fix is complete.");

        await controller.RefreshAsync(default);
        await controller.RefreshAsync(default);

        Assert.True(controller.State.IsTerminal);
        Assert.Equal("The verified fix is complete.", Assert.Single(controller.State.Messages).Content);
        Assert.Equal(1, client.Gets);
    }

    [Fact]
    public async Task MessageAfterTerminalTicketCreatesANewRequest()
    {
        var client = new FakeSupportClient();
        var controller = new SupportConversationController(client);
        controller.SetDraft("First issue");
        await controller.SendAsync(default);
        client.Detail = Detail("ESCALATED", "A technician will take it from here.");
        await controller.RefreshAsync(default);
        controller.SetDraft("A different issue");

        await controller.SendAsync(default);

        Assert.Equal(2, client.Creates.Count);
        Assert.Empty(client.Continues);
        Assert.Equal("A different issue", client.Creates[1].Message);
    }

    [Fact]
    public async Task ReopenedTerminalTicketStartsANewRequest()
    {
        var client = new FakeSupportClient
        {
            ActiveRequestOverride = new SupportRequestBookmark("ticket-old", 41, "run-old", "ESCALATED"),
        };
        var controller = new SupportConversationController(client);
        controller.SetDraft("A different issue after reopening");

        await controller.SendAsync(default);

        Assert.Single(client.Creates);
        Assert.Empty(client.Continues);
    }

    [Fact]
    public async Task RevokedDeviceFailsClosedAndKeepsWhatTheUserTyped()
    {
        var client = new FakeSupportClient { Revoked = true };
        var controller = new SupportConversationController(client);
        controller.SetDraft("Do not lose this message.");

        await controller.SendAsync(default);

        Assert.Equal("Do not lose this message.", controller.State.Draft);
        Assert.True(controller.State.IsAccessBlocked);
        Assert.Contains("administrator", controller.State.Error!, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task DirectConversationUsesOnlySupportRequestCallsAndNeverStartsControl()
    {
        var client = new FakeSupportClient();
        var controller = new SupportConversationController(client);
        controller.SetDraft("Help me");
        await controller.SendAsync(default);
        await controller.RefreshAsync(default);

        Assert.Single(client.Creates);
        Assert.Equal(1, client.Gets);
        Assert.Equal(0, client.ControlOrSessionCalls);
    }

    [Theory]
    [InlineData("guide")]
    [InlineData("fix")]
    public async Task CustomerCanStartGuideOrFixFromTheSameTicket(string mode)
    {
        var client = new FakeSupportClient();
        var controller = new SupportConversationController(client);
        controller.SetDraft("I can't find Adobe Sign in Outlook.");
        await controller.SendAsync(default);

        await controller.StartComputerUseAsync(mode, default);

        Assert.Equal(("run-1", mode), Assert.Single(client.ComputerUseStarts));
        Assert.Equal("session-1", controller.State.ComputerUseSessionId);
        Assert.Equal("Waiting for approval on this PC", controller.State.ComputerUseStatus);
    }

    [Fact]
    public async Task StartingFixAfterTicketEscalationKeepsPollingTheDirectControlConversation()
    {
        var client = new FakeSupportClient();
        var controller = new SupportConversationController(client);
        controller.SetDraft("Uninstall Adobe Acrobat but keep Adobe Genuine Service.");
        await controller.SendAsync(default);
        client.Detail = Detail("ESCALATED", "I've handed this to a technician.");
        await controller.RefreshAsync(default);
        Assert.True(controller.State.IsTerminal);

        await controller.StartComputerUseAsync("fix", default);
        client.Detail = Detail("AI_INVESTIGATING", "Which Adobe application should I uninstall?");
        await controller.RefreshAsync(default);

        Assert.False(controller.State.IsTerminal);
        Assert.Equal(2, client.Gets);
        Assert.Equal("Which Adobe application should I uninstall?", Assert.Single(controller.State.Messages).Content);
    }

    private static SupportRequestDetail Detail(string state, string content) => new(
        "run-1",
        "ticket-1",
        state,
        state == "RESOLVED" ? null : "customer",
        null,
        [new SupportConversationMessage("message-1", "assistant", content, DateTimeOffset.UtcNow)]);

    private sealed class FakeSupportClient : ISupportRequestClient
    {
        private SupportRequestBookmark? _activeRequest;
        public SupportRequestBookmark? ActiveRequest => ActiveRequestOverride ?? _activeRequest;
        public SupportRequestBookmark? ActiveRequestOverride { get; init; }
        public List<(Guid RequestId, string Message)> Creates { get; } = [];
        public List<(string RunId, string Message)> Continues { get; } = [];
        public int Gets { get; private set; }
        public int ControlOrSessionCalls { get; private set; }
        public List<(string RunId, string Mode)> ComputerUseStarts { get; } = [];
        public int FailCreates { get; set; }
        public bool Revoked { get; set; }
        public SupportRequestDetail Detail { get; set; } = SupportConversationTests.Detail("CLARIFYING", "What happens when you try?");

        public Task<SupportRequestResult> CreateSupportRequestAsync(Guid requestId, string message, CancellationToken ct)
        {
            Creates.Add((requestId, message));
            if (Revoked) throw new DeviceRevokedException();
            if (FailCreates-- > 0) throw new AgentProtocolException("network unavailable");
            _activeRequest = new SupportRequestBookmark("ticket-1", 42, "run-1", "NEW");
            return Task.FromResult(new SupportRequestResult("ticket-1", 42, "run-1", "NEW"));
        }

        public Task<SupportRequestResult> ContinueSupportRequestAsync(string runId, string message, CancellationToken ct)
        {
            Continues.Add((runId, message));
            _activeRequest = new SupportRequestBookmark("ticket-1", 42, runId, "CLARIFYING");
            return Task.FromResult(new SupportRequestResult("ticket-1", 42, runId, "CLARIFYING"));
        }

        public Task<SupportRequestDetail> GetSupportRequestAsync(string runId, CancellationToken ct)
        {
            Gets++;
            return Task.FromResult(Detail);
        }

        public Task<DirectComputerUseResult> StartComputerUseAsync(string runId, string mode, CancellationToken ct)
        {
            ComputerUseStarts.Add((runId, mode));
            ControlOrSessionCalls++;
            return Task.FromResult(new DirectComputerUseResult("session-1", null, mode, "waiting_session_consent"));
        }
    }

    private sealed class FakePromptWindow : ISupportPromptWindow
    {
        public bool IsMinimized { get; init; }
        public bool IsVisible { get; init; }
        public int RestoreCalls { get; private set; }
        public int ShowCalls { get; private set; }
        public int ActivateCalls { get; private set; }

        public void Restore() => RestoreCalls++;
        public void Show() => ShowCalls++;
        public bool TryActivate()
        {
            ActivateCalls++;
            return true;
        }
    }
}
