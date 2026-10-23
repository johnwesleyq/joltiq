using JoltIQ.Agent.Core;
using JoltIQ.Agent.Tray;
using Xunit;

namespace JoltIQ.Agent.Tests;

public sealed class SupportConversationTests
{
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

    private static SupportRequestDetail Detail(string state, string content) => new(
        "run-1",
        "ticket-1",
        state,
        state == "RESOLVED" ? null : "customer",
        null,
        [new SupportConversationMessage("message-1", "assistant", content, DateTimeOffset.UtcNow)]);

    private sealed class FakeSupportClient : ISupportRequestClient
    {
        public SupportRequestBookmark? ActiveRequest { get; private set; }
        public List<(Guid RequestId, string Message)> Creates { get; } = [];
        public List<(string RunId, string Message)> Continues { get; } = [];
        public int Gets { get; private set; }
        public int ControlOrSessionCalls { get; private set; }
        public int FailCreates { get; set; }
        public bool Revoked { get; set; }
        public SupportRequestDetail Detail { get; set; } = SupportConversationTests.Detail("CLARIFYING", "What happens when you try?");

        public Task<SupportRequestResult> CreateSupportRequestAsync(Guid requestId, string message, CancellationToken ct)
        {
            Creates.Add((requestId, message));
            if (Revoked) throw new DeviceRevokedException();
            if (FailCreates-- > 0) throw new AgentProtocolException("network unavailable");
            ActiveRequest = new SupportRequestBookmark("ticket-1", 42, "run-1", "NEW");
            return Task.FromResult(new SupportRequestResult("ticket-1", 42, "run-1", "NEW"));
        }

        public Task<SupportRequestResult> ContinueSupportRequestAsync(string runId, string message, CancellationToken ct)
        {
            Continues.Add((runId, message));
            ActiveRequest = new SupportRequestBookmark("ticket-1", 42, runId, "CLARIFYING");
            return Task.FromResult(new SupportRequestResult("ticket-1", 42, runId, "CLARIFYING"));
        }

        public Task<SupportRequestDetail> GetSupportRequestAsync(string runId, CancellationToken ct)
        {
            Gets++;
            return Task.FromResult(Detail);
        }
    }
}
