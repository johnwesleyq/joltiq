using JoltIQ.Agent.Tray;
using Xunit;

namespace JoltIQ.Agent.Tests;

public sealed class CompanionLaunchRequestTests
{
    [Fact]
    public void AskArgumentOpensTheSupportConversation()
    {
        var request = CompanionLaunchRequest.Parse(["--ask"]);

        Assert.True(request.OpenSupportConversation);
    }

    [Fact]
    public void BackgroundLaunchKeepsTheTrayQuiet()
    {
        var request = CompanionLaunchRequest.Parse([]);

        Assert.False(request.OpenSupportConversation);
    }

    [Fact]
    public void AskArgumentIsCaseInsensitive()
    {
        var request = CompanionLaunchRequest.Parse(["--ASK"]);

        Assert.True(request.OpenSupportConversation);
    }
}
