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

    [Fact]
    public async Task SlowEnrollmentDoesNotBlockBeforeRuntimeStartup()
    {
        var enrollment = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var events = new List<string>();

        var completion = EnrollmentCompletion.RunAsync(
            () => enrollment.Task,
            () => events.Add("runtime-started"),
            () => events.Add("success-notice"));

        Assert.False(completion.IsCompleted);
        Assert.Empty(events);

        enrollment.SetResult();
        await completion;

        Assert.Equal(["runtime-started", "success-notice"], events);
    }
}
