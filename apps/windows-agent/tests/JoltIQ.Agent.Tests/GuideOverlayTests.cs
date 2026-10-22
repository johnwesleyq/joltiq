using System.Text.Json;
using JoltIQ.Agent.Core;
using JoltIQ.Agent.Tray;
using JoltIQ.Agent.Windows;
using Xunit;

namespace JoltIQ.Agent.Tests;

public sealed class GuideOverlayTests
{
    private static JsonElement Params(string json) => JsonDocument.Parse(json).RootElement.Clone();

    [Fact]
    public void GuideGrant_AllowsOnlyGuideActionsForItsExactRunAndMode()
    {
        var policy = new LocalPolicy();
        var guide = new LocalSession {
            SessionId = "session-1", Active = true, ComputerUseRunId = "run-1", ComputerUseMode = "guide"
        };
        var highlight = Params("""{"frame_generation":17,"layout_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","frame_width":1920,"frame_height":1080,"x":10,"y":20,"width":100,"height":40,"instruction":"Click Settings","expected_control_label":"Settings"}""");
        var click = Params("""{"frame_generation":17,"layout_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","x":10,"y":20}""");

        Assert.True(policy.Evaluate("guide.highlight", highlight, guide, "run-1", "guide").Allowed);
        Assert.False(policy.Evaluate("guide.highlight", highlight, guide, "run-2", "guide").Allowed);
        Assert.False(policy.Evaluate("input.click", click, guide, "run-1", "guide").Allowed);

        var fix = new LocalSession {
            SessionId = "session-1", Active = true, ComputerUseRunId = "run-1", ComputerUseMode = "fix"
        };
        Assert.False(policy.Evaluate("guide.highlight", highlight, fix, "run-1", "fix").Allowed);
        Assert.True(policy.Evaluate("input.click", click, fix, "run-1", "fix").Allowed);
    }

    [Fact]
    public void Overlay_IsNoActivateClickThroughAndRejectsUnboundedGeometry()
    {
        Assert.True(GuideOverlaySecurity.NoActivate);
        Assert.True(GuideOverlaySecurity.ClickThrough);
        Assert.True(GuideOverlaySecurity.TopMost);
        Assert.True(GuideOverlaySecurity.IsValidGeometry(10, 20, 100, 40, 1920, 1080));
        Assert.False(GuideOverlaySecurity.IsValidGeometry(-1, 20, 100, 40, 1920, 1080));
        Assert.False(GuideOverlaySecurity.IsValidGeometry(10, 20, 10001, 40, 1920, 1080));
        Assert.False(GuideOverlaySecurity.IsValidGeometry(1900, 20, 100, 40, 1920, 1080));
    }

    [Fact]
    public void Provider_AdvertisesVisualOnlyGuideActions()
    {
        var provider = new WindowsActionProvider();

        Assert.Contains("guide.highlight", provider.Capabilities);
        Assert.Contains("guide.clear", provider.Capabilities);
    }
}
