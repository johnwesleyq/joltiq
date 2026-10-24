using JoltIQ.Agent.Tray;
using Xunit;

namespace JoltIQ.Agent.Tests;

public sealed class EnrollmentCompletionTests
{
    [Fact]
    public void RuntimeStartsBeforeTheBlockingSuccessNotice()
    {
        var events = new List<string>();

        EnrollmentCompletion.Run(
            () => events.Add("runtime-started"),
            () => events.Add("success-notice"));

        Assert.Equal(["runtime-started", "success-notice"], events);
    }
}
