using System.Security.Cryptography;
using System.Text.Json;
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
