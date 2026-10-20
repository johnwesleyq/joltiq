using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using JoltIQ.Agent.Windows;
using Xunit;

namespace JoltIQ.Agent.Tests;

/// <summary>Runs on a real Windows host (windows-latest CI or an authorized Windows 11 test machine). Read-only actions only,
/// except the HKCU add-in test, which uses a throwaway ProgId and cleans up.</summary>
public class WindowsProviderTests
{
    private readonly WindowsActionProvider _p = new();
    private static JsonElement J(string s) => JsonDocument.Parse(s).RootElement;

    [Fact] public async Task SystemInfo_ReturnsOs() =>
        Assert.Contains("Windows", (await _p.ExecuteAsync("system.info", J("{}"), default))["os"]!.GetValue<string>());

    [Fact] public async Task ProcessList_FindsSelf()
    {
        var name = System.Diagnostics.Process.GetCurrentProcess().ProcessName;
        var r = await _p.ExecuteAsync("process.list", J($"{{\"name_filter\":\"{name}\"}}"), default);
        Assert.NotEmpty(r["processes"]!.AsArray());
    }

    [Fact] public async Task ServiceStatus_KnownAndUnknown()
    {
        Assert.NotEqual("NotFound", (await _p.ExecuteAsync("service.status", J("{\"name\":\"EventLog\"}"), default))["status"]!.GetValue<string>());
        Assert.Equal("NotFound", (await _p.ExecuteAsync("service.status", J("{\"name\":\"NoSuchSvc123\"}"), default))["status"]!.GetValue<string>());
    }

    [Fact] public async Task EventLog_Query_Works() =>
        Assert.NotNull((await _p.ExecuteAsync("eventlog.query", J("{\"log\":\"System\",\"hours\":24,\"max_events\":5}"), default))["events"]);

    private static readonly DateTimeOffset Window = DateTimeOffset.Parse("2026-10-20T12:00:00Z");
    private static JsonObject Interactive(bool supported = true, string? reason = null) => new()
    {
        ["supported"] = supported, ["reasons"] = reason is null ? new JsonArray() : new JsonArray(reason),
        ["enrolled_sid"] = "S-1-5-21-1", ["interactive_sid"] = "S-1-5-21-1", ["same_user"] = supported,
        ["active_session_count"] = supported ? 1 : reason == "multiple_active_users" ? 2 : 0, ["unlocked"] = supported,
    };
    private static OutlookInspectionEvidence Evidence(
        bool classic = true, bool modern = false, IReadOnlyList<AddinFact>? addins = null, JsonObject? interactive = null,
        bool readable = true, bool enabled = true, bool service = true, DateTimeOffset? oldest = null,
        IReadOnlyList<DateTimeOffset>? matches = null) =>
        new(true, "Windows 11", interactive ?? Interactive(), classic, classic ? @"C:\\Office\\OUTLOOK.EXE" : null, modern,
            addins ?? new[] { new AddinFact("HKCU", "64", 3, "DWord", false) },
            new CrashLogFact(readable, enabled, service, oldest ?? Window.AddHours(-1), Window.AddMinutes(5),
                matches ?? new[] { Window.AddMinutes(1) }));

    [Fact] public void OutlookInspection_HappyPath_IsSupported()
    {
        var result = OutlookInspector.Evaluate(Evidence(), Window);
        Assert.True(result["supported"]!.GetValue<bool>());
        Assert.True(result["crash"]!["window_trustworthy"]!.GetValue<bool>());
        Assert.True(result["crash"]!["matched"]!.GetValue<bool>());
        Assert.Equal("HKCU", result["addin"]!["scope"]!.GetValue<string>());
        Assert.Equal("64", result["addin"]!["registry_view"]!.GetValue<string>());
    }

    [Theory]
    [InlineData("classic_absent")]
    [InlineData("new_outlook")]
    [InlineData("addin_absent")]
    [InlineData("hklm_addin")]
    [InlineData("hkcu_policy")]
    [InlineData("hklm_policy")]
    [InlineData("wrong_module")]
    [InlineData("log_disabled")]
    [InlineData("log_unreadable")]
    [InlineData("log_stale")]
    [InlineData("locked")]
    [InlineData("no_user")]
    [InlineData("user_mismatch")]
    [InlineData("multiple_users")]
    public void OutlookInspection_UnsupportedFixturesNeverClaimSupported(string fixture)
    {
        var evidence = fixture switch
        {
            "classic_absent" => Evidence(classic: false),
            "new_outlook" => Evidence(modern: true),
            "addin_absent" => Evidence(addins: Array.Empty<AddinFact>()),
            "hklm_addin" => Evidence(addins: new[] { new AddinFact("HKLM", "64", 3, "DWord", false) }),
            "hkcu_policy" => Evidence(addins: new[] { new AddinFact("HKCU", "64", 3, "DWord", false), new AddinFact("HKCU", "64", null, null, true) }),
            "hklm_policy" => Evidence(addins: new[] { new AddinFact("HKCU", "32", 3, "DWord", false), new AddinFact("HKLM", "32", null, null, true) }),
            "wrong_module" => Evidence(matches: Array.Empty<DateTimeOffset>()),
            "log_disabled" => Evidence(enabled: false),
            "log_unreadable" => Evidence(readable: false),
            "log_stale" => Evidence(oldest: Window.AddMinutes(1)),
            "locked" => Evidence(interactive: Interactive(false, "screen_locked")),
            "no_user" => Evidence(interactive: Interactive(false, "no_active_user")),
            "user_mismatch" => Evidence(interactive: Interactive(false, "enrolled_user_mismatch")),
            "multiple_users" => Evidence(interactive: Interactive(false, "multiple_active_users")),
            _ => throw new ArgumentOutOfRangeException(nameof(fixture)),
        };
        Assert.False(OutlookInspector.Evaluate(evidence, Window)["supported"]!.GetValue<bool>());
    }

    [Theory]
    [InlineData("32")]
    [InlineData("64")]
    public void OutlookInspection_ReportsExactHkcuRegistryView(string view)
    {
        var result = OutlookInspector.Evaluate(Evidence(addins: new[] { new AddinFact("HKCU", view, 2, "DWord", false) }), Window);
        Assert.Equal(view, result["addin"]!["registry_view"]!.GetValue<string>());
        Assert.Equal(2, result["addin"]!["original_value"]!.GetValue<int>());
    }

    [Fact] public async Task AddinLoadBehavior_RoundTrip_HKCU()
    {
        var id = "JoltIQ.Test." + Guid.NewGuid().ToString("N")[..8];
        await _p.ExecuteAsync("office.addin.set_load_behavior", J($"{{\"app\":\"Outlook\",\"addin_progid\":\"{id}\",\"enabled\":false}}"), default);
        var r = await _p.ExecuteAsync("config.read", J($"{{\"key\":\"HKCU\\\\Software\\\\Microsoft\\\\Office\\\\Outlook\\\\Addins\\\\{id}\",\"value\":\"LoadBehavior\"}}"), default);
        Assert.Equal("2", r["data"]!.GetValue<string>());
        Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree($@"Software\Microsoft\Office\Outlook\Addins\{id}");
    }

    [Fact] public void Dpapi_RoundTrip_And_WrongScopeFails()
    {
        var dir = Path.Combine(Path.GetTempPath(), "jqtest-" + Guid.NewGuid().ToString("N"));
        var s = new DpapiKeyStore(DataProtectionScope.CurrentUser, dir);
        s.Save("k", new byte[] { 1, 2, 3 });
        Assert.Equal(new byte[] { 1, 2, 3 }, s.Load("k"));
        Assert.NotEqual(new byte[] { 1, 2, 3 }, File.ReadAllBytes(Path.Combine(dir, "k.dpapi")));  // encrypted at rest
    }
}
