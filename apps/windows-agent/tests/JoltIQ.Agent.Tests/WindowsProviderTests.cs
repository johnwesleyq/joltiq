using System.IO;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;
using System.Text.Json.Nodes;
using JoltIQ.Agent.Core;
using JoltIQ.Agent.Windows;
using JoltIQ.Agent.Windows.Desktop;
using Microsoft.Win32;
using Xunit;

namespace JoltIQ.Agent.Tests;

/// <summary>Runs on a real Windows host (windows-latest CI or an authorized Windows 11 test machine). Read-only actions only,
/// except the HKCU add-in test, which uses a throwaway ProgId and cleans up.</summary>
public class WindowsProviderTests
{
    private const string TestManagedAddinPath = @"Software\Microsoft\Office\16.0\Outlook\Resiliency\AddinList";
    private readonly WindowsActionProvider _p = new();
    private static JsonElement J(string s) => JsonDocument.Parse(s).RootElement;

    [Fact]
    public void ComputerUse_CapabilitiesRequireTheirNativeDependencies()
    {
        var unavailable = new WindowsActionProvider();
        Assert.DoesNotContain("screen.capture", unavailable.Capabilities);
        Assert.DoesNotContain("input.click", unavailable.Capabilities);
        Assert.DoesNotContain("url.open", unavailable.Capabilities);
        Assert.DoesNotContain("guide.highlight", unavailable.Capabilities);

        var capture = new FakeCapture();
        var input = new FakeDesktopInput();
        var available = new WindowsActionProvider(
            desktopCapture: capture,
            desktopInput: input,
            sessionResolver: _ => FixSession(),
            showGuide: _ => { },
            clearGuide: () => { },
            urlLauncher: _ => true);

        Assert.Contains("screen.capture", available.Capabilities);
        Assert.Contains("input.click", available.Capabilities);
        Assert.Contains("input.type", available.Capabilities);
        Assert.Contains("url.open", available.Capabilities);
        Assert.Contains("guide.highlight", available.Capabilities);
        Assert.Contains("guide.clear", available.Capabilities);
    }

    [Fact]
    public async Task ComputerUse_CaptureReturnsPngAndExactDisplayMetadata()
    {
        var capture = new FakeCapture();
        var provider = new WindowsActionProvider(desktopCapture: capture);

        var result = await provider.ExecuteAsync("screen.capture", J("{\"max_long_edge\":1280}"), default, ComputerUseContext());

        Assert.Equal("image/png", result.ScreenshotMediaType);
        Assert.Equal(FakeCapture.Png, result.Screenshot);
        Assert.Equal(1280, capture.LastMaxLongEdge);
        Assert.Equal(800, result["width"]!.GetValue<int>());
        Assert.Equal(600, result["height"]!.GetValue<int>());
        Assert.Equal(-100, result["virtual_bounds"]!["left"]!.GetValue<int>());
        Assert.Equal("layout-hash", result["layout_hash"]!.GetValue<string>());
        Assert.Equal(1, result["frame_generation"]!.GetValue<long>());
        Assert.Single(result["displays"]!.AsArray());
    }

    [Fact]
    public async Task ComputerUse_SuccessfulInputReturnsAPostActionFrame()
    {
        var capture = new FakeCapture();
        var input = new FakeDesktopInput();
        var session = FixSession();
        var provider = new WindowsActionProvider(
            desktopCapture: capture,
            desktopInput: input,
            sessionResolver: id => id == session.SessionId ? session : null);
        await provider.ExecuteAsync("screen.capture", J("{}"), default, ComputerUseContext());

        var result = await provider.ExecuteAsync("input.click",
            J("{\"x\":10,\"y\":20,\"button\":\"left\",\"count\":1,\"frame_generation\":1,\"layout_hash\":\"layout-hash\"}"),
            default, ComputerUseContext());

        Assert.Equal(new[] { "click:10,20:left:1" }, input.Events);
        Assert.Equal(2, capture.Count);
        Assert.Equal(2, result["frame_generation"]!.GetValue<long>());
        Assert.Equal(FakeCapture.Png, result.Screenshot);
    }

    [Fact]
    public async Task ComputerUse_BlockedInputDoesNotCaptureOrClaimSuccess()
    {
        var capture = new FakeCapture();
        var input = new FakeDesktopInput { Refuse = true };
        var provider = new WindowsActionProvider(
            desktopCapture: capture,
            desktopInput: input,
            sessionResolver: _ => FixSession());

        await Assert.ThrowsAsync<DesktopInputRefusedException>(() => provider.ExecuteAsync("input.type",
            J("{\"text\":\"hello\",\"frame_generation\":1,\"layout_hash\":\"layout-hash\"}"),
            default, ComputerUseContext()));
        Assert.Equal(0, capture.Count);
    }

    [Fact]
    public async Task ComputerUse_GuideUsesOnlyTheVisualBridge()
    {
        var input = new FakeDesktopInput();
        GuideInstruction? shown = null;
        var cleared = false;
        var provider = new WindowsActionProvider(
            desktopInput: input,
            sessionResolver: _ => FixSession("guide"),
            showGuide: value => shown = value,
            clearGuide: () => cleared = true);

        await provider.ExecuteAsync("guide.highlight",
            J("{\"x\":10,\"y\":20,\"width\":100,\"height\":40,\"frame_width\":800,\"frame_height\":600,\"instruction\":\"Click Settings\",\"expected_control_label\":\"Settings\"}"),
            default, ComputerUseContext("guide"));
        await provider.ExecuteAsync("guide.clear", J("{}"), default, ComputerUseContext("guide"));

        Assert.Equal("Click Settings", shown?.Instruction);
        Assert.True(cleared);
        Assert.Empty(input.Events);
    }

    [Fact]
    public async Task ComputerUse_UrlOpenAcceptsOnlyHttpAndHttps()
    {
        var opened = new List<Uri>();
        var provider = new WindowsActionProvider(urlLauncher: uri => { opened.Add(uri); return true; });

        var refused = await provider.ExecuteAsync("url.open", J("{\"url\":\"file:///C:/Windows/win.ini\"}"), default, ComputerUseContext());
        var accepted = await provider.ExecuteAsync("url.open", J("{\"url\":\"https://example.test/help\"}"), default, ComputerUseContext());

        Assert.False(refused["ok"]!.GetValue<bool>());
        Assert.True(accepted["ok"]!.GetValue<bool>());
        Assert.Equal("https://example.test/help", Assert.Single(opened).AbsoluteUri);
    }

    private sealed class MemoryJournal : ICommandJournal
    {
        private readonly Dictionary<string, MutationRecord> _records = new();
        private readonly InMemoryCommandJournal _commands = new();
        public bool RefuseWrites { get; set; }
        public bool RefuseAppliedMarker { get; set; }
        public bool MarkRunning(ActionExecutionContext context, string action) => _commands.MarkRunning(context, action);
        public bool TryGetCommand(string commandId, out CommandJournalEntry record) => _commands.TryGetCommand(commandId, out record);
        public bool MarkCompleted(string commandId, string status, JsonObject result, CommandEvidence? evidence = null) =>
            _commands.MarkCompleted(commandId, status, result, evidence);
        public IReadOnlyList<PendingCommandReport> PendingReports() => _commands.PendingReports();
        public bool TryLoadEvidence(string commandId, out CommandEvidence? evidence, out string? error) =>
            _commands.TryLoadEvidence(commandId, out evidence, out error);
        public bool MarkEvidenceUnavailable(string commandId, string reason) => _commands.MarkEvidenceUnavailable(commandId, reason);
        public bool AcknowledgeReport(string commandId) => _commands.AcknowledgeReport(commandId);
        public bool TrySaveMutation(MutationRecord record)
        {
            if (RefuseWrites || _records.ContainsKey(record.SourceCommandId)) return false;
            _records.Add(record.SourceCommandId, record);
            return true;
        }
        public bool TryGet(string commandId, out MutationRecord record) => _records.TryGetValue(commandId, out record!);
        public bool TryMarkMutationApplied(string commandId, RegistryValueSnapshot after)
        {
            if (RefuseAppliedMarker) return false;
            if (!_records.TryGetValue(commandId, out var record)) return false;
            _records[commandId] = record with { Applied = true, After = after };
            return true;
        }
        public bool TryMarkRestored(string commandId, string restoreCommandId)
        {
            if (!_records.TryGetValue(commandId, out var record) || !record.Applied || record.RestoreCommandId is not null) return false;
            _records[commandId] = record with { RestoreCommandId = restoreCommandId };
            return true;
        }
    }

    private static string Sid => WindowsIdentity.GetCurrent().User!.Value;
    private static ActionExecutionContext Context(string commandId, string? deviceId = "device-1", string? userSid = null) =>
        new(commandId, deviceId!, "session-1", userSid ?? Sid, "procedure-hash");

    private static RegistryKey AddinKey(string progId, RegistryView view, bool writable = true) =>
        RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, view).CreateSubKey($@"Software\Microsoft\Office\Outlook\Addins\{progId}", writable)!;

    private static void DeleteAddin(string progId)
    {
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            using var root = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, view);
            root.DeleteSubKeyTree($@"Software\Microsoft\Office\Outlook\Addins\{progId}", false);
            using var policy = root.OpenSubKey(TestManagedAddinPath, writable: true);
            policy?.DeleteValue(progId, false);
        }
    }

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

    [Fact] public async Task AddinLoadBehavior_RecordsTypedPreStateBeforeWrite_AndRestoresItOnce()
    {
        var id = "JoltIQ.Test." + Guid.NewGuid().ToString("N")[..8];
        var journal = new MemoryJournal();
        try
        {
            using (var key = AddinKey(id, RegistryView.Registry64)) key.SetValue("LoadBehavior", 3, RegistryValueKind.DWord);
            var p = new WindowsActionProvider(Sid, journal, ActionHost.InteractiveTray, () => Sid);
            var changed = await p.ExecuteAsync("office.addin.set_load_behavior",
                J($"{{\"app\":\"Outlook\",\"addin_progid\":\"{id}\",\"enabled\":false}}"), default, Context("change-1"));
            Assert.True(changed["ok"]!.GetValue<bool>());
            Assert.Equal("change-1", changed["source_command_id"]!.GetValue<string>());
            Assert.Equal(3, changed["before"]!["value"]!.GetValue<int>());
            Assert.Equal(2, changed["after"]!["value"]!.GetValue<int>());
            Assert.True(journal.TryGet("change-1", out var saved));
            Assert.True(saved.Applied);
            Assert.Equal("DWord", saved.Before.Kind);
            Assert.Equal("shared", saved.Before.RegistryView);

            var restored = await p.ExecuteAsync("office.addin.restore_load_behavior",
                J("{\"source_command_id\":\"change-1\"}"), default, Context("restore-1"));
            Assert.True(restored["ok"]!.GetValue<bool>());
            using (var key = AddinKey(id, RegistryView.Registry64)) Assert.Equal(3, key.GetValue("LoadBehavior"));
            var verified = await p.ExecuteAsync("office.addin.verify_restored",
                J("{\"source_command_id\":\"change-1\"}"), default, Context("verify-restore-1"));
            Assert.True(verified["matches_local_journal"]!.GetValue<bool>());

            using (var key = AddinKey(id, RegistryView.Registry64)) key.SetValue("LoadBehavior", 2, RegistryValueKind.DWord);
            var tampered = await p.ExecuteAsync("office.addin.verify_restored",
                J("{\"source_command_id\":\"change-1\"}"), default, Context("verify-restore-2"));
            Assert.False(tampered["matches_local_journal"]!.GetValue<bool>());
            using (var key = AddinKey(id, RegistryView.Registry64)) key.SetValue("LoadBehavior", 3, RegistryValueKind.DWord);

            var duplicate = await p.ExecuteAsync("office.addin.restore_load_behavior",
                J("{\"source_command_id\":\"change-1\"}"), default, Context("restore-2"));
            Assert.False(duplicate["ok"]!.GetValue<bool>());
        }
        finally { DeleteAddin(id); }
    }

    [Fact] public async Task AddinLoadBehavior_RestoresOriginallyAbsentValue()
    {
        var id = "JoltIQ.Test." + Guid.NewGuid().ToString("N")[..8];
        var journal = new MemoryJournal();
        try
        {
            using (AddinKey(id, RegistryView.Registry64)) { }
            var p = new WindowsActionProvider(Sid, journal, ActionHost.InteractiveTray, () => Sid);
            Assert.True((await p.ExecuteAsync("office.addin.set_load_behavior",
                J($"{{\"app\":\"Outlook\",\"addin_progid\":\"{id}\",\"enabled\":false}}"), default, Context("absent-change")))["ok"]!.GetValue<bool>());
            Assert.True((await p.ExecuteAsync("office.addin.restore_load_behavior",
                J("{\"source_command_id\":\"absent-change\"}"), default, Context("absent-restore")))["ok"]!.GetValue<bool>());
            using var key = AddinKey(id, RegistryView.Registry64);
            Assert.DoesNotContain("LoadBehavior", key.GetValueNames());
        }
        finally { DeleteAddin(id); }
    }

    [Theory]
    [InlineData("missing")]
    [InlineData("policy")]
    [InlineData("journal_refused")]
    [InlineData("user_changed")]
    [InlineData("service_host")]
    public async Task AddinLoadBehavior_RefusesUnsafeMutation(string scenario)
    {
        var id = "JoltIQ.Test." + Guid.NewGuid().ToString("N")[..8];
        var journal = new MemoryJournal { RefuseWrites = scenario == "journal_refused" };
        try
        {
            if (scenario != "missing")
            {
                using var key = AddinKey(id, RegistryView.Registry64);
                key.SetValue("LoadBehavior", 3, RegistryValueKind.DWord);
            }
            if (scenario == "policy")
            {
                using var root = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64);
                using var policy = root.CreateSubKey(TestManagedAddinPath, true);
                policy!.SetValue(id, "1", RegistryValueKind.String);
            }
            var currentSid = scenario == "user_changed" ? "S-1-5-21-different" : Sid;
            var host = scenario == "service_host" ? ActionHost.BackgroundService : ActionHost.InteractiveTray;
            var p = new WindowsActionProvider(Sid, journal, host, () => currentSid);
            if (scenario == "service_host")
            {
                Assert.DoesNotContain("office.addin.set_load_behavior", p.Capabilities);
                Assert.DoesNotContain("office.addin.restore_load_behavior", p.Capabilities);
                Assert.DoesNotContain("office.addin.verify_restored", p.Capabilities);
            }
            var result = await p.ExecuteAsync("office.addin.set_load_behavior",
                J($"{{\"app\":\"Outlook\",\"addin_progid\":\"{id}\",\"enabled\":false}}"), default, Context("unsafe-" + scenario));
            Assert.False(result["ok"]!.GetValue<bool>());
            if (scenario != "missing")
            {
                using var key = AddinKey(id, RegistryView.Registry64);
                Assert.Equal(3, key.GetValue("LoadBehavior"));
            }
        }
        finally { DeleteAddin(id); }
    }

    [Fact] public async Task AddinLoadBehavior_ReportsCompletedWriteWhenJournalCompletionMarkerFails()
    {
        var id = "JoltIQ.Test." + Guid.NewGuid().ToString("N")[..8];
        try
        {
            using (var key = AddinKey(id, RegistryView.Registry64)) key.SetValue("LoadBehavior", 3, RegistryValueKind.DWord);
            var journal = new MemoryJournal { RefuseAppliedMarker = true };
            var p = new WindowsActionProvider(Sid, journal, ActionHost.InteractiveTray, () => Sid);
            var result = await p.ExecuteAsync("office.addin.set_load_behavior",
                J($"{{\"app\":\"Outlook\",\"addin_progid\":\"{id}\",\"enabled\":false}}"), default, Context("marker-failure"));

            Assert.False(result["ok"]!.GetValue<bool>());
            Assert.True(result["changed"]!.GetValue<bool>());
            Assert.Equal("marker-failure", result["source_command_id"]!.GetValue<string>());
            Assert.Equal(3, result["before"]!["value"]!.GetValue<int>());
            Assert.Equal(2, result["after"]!["value"]!.GetValue<int>());
            using var readbackKey = AddinKey(id, RegistryView.Registry64);
            Assert.Equal(2, readbackKey.GetValue("LoadBehavior"));
        }
        finally { DeleteAddin(id); }
    }

    [Fact] public async Task Restore_RefusesMissingOrWrongLineage_AndServerSuppliedRegistryState()
    {
        var journal = new MemoryJournal();
        var p = new WindowsActionProvider(Sid, journal, ActionHost.InteractiveTray, () => Sid);
        var missing = await p.ExecuteAsync("office.addin.restore_load_behavior",
            J("{\"source_command_id\":\"does-not-exist\"}"), default, Context("restore-missing"));
        Assert.False(missing["ok"]!.GetValue<bool>());

        Assert.True(journal.TrySaveMutation(new MutationRecord("source-wrong-device", "other-device", "session-1", Sid,
            "procedure-hash", "office.addin.set_load_behavior",
            new RegistryValueSnapshot(true, @"Software\Microsoft\Office\Outlook\Addins\Nope", "LoadBehavior", "64", "DWord", JsonValue.Create(3)))));
        Assert.True(journal.TryMarkMutationApplied("source-wrong-device",
            new RegistryValueSnapshot(true, @"Software\Microsoft\Office\Outlook\Addins\Nope", "LoadBehavior", "64", "DWord", JsonValue.Create(2))));
        var wrong = await p.ExecuteAsync("office.addin.restore_load_behavior",
            J("{\"source_command_id\":\"source-wrong-device\"}"), default, Context("restore-wrong"));
        Assert.False(wrong["ok"]!.GetValue<bool>());

        var verdict = new LocalPolicy().Evaluate("office.addin.restore_load_behavior",
            J("{\"source_command_id\":\"source-wrong-device\",\"key\":\"HKCU\\\\bad\",\"value\":3}"),
            new LocalSession { SessionId = "s", Active = true });
        Assert.False(verdict.Allowed);
    }

    [Fact] public async Task AddinLoadBehavior_TreatsHkcuSoftwareAsOneSharedRegistryView()
    {
        var id = "JoltIQ.Test." + Guid.NewGuid().ToString("N")[..8];
        try
        {
            using (var key = AddinKey(id, RegistryView.Registry32)) key.SetValue("LoadBehavior", 3, RegistryValueKind.DWord);
            var p = new WindowsActionProvider(Sid, new MemoryJournal(), ActionHost.InteractiveTray, () => Sid);
            var result = await p.ExecuteAsync("office.addin.set_load_behavior",
                J($"{{\"app\":\"Outlook\",\"addin_progid\":\"{id}\",\"enabled\":false}}"), default, Context("view-32"));
            Assert.True(result["ok"]!.GetValue<bool>());
            Assert.True(result["ok"]!.GetValue<bool>());
            Assert.Equal("shared", result["before"]!["registry_view"]!.GetValue<string>());
            var readback = await p.ExecuteAsync("config.read",
                J($"{{\"key\":\"HKCU\\\\Software\\\\Microsoft\\\\Office\\\\Outlook\\\\Addins\\\\{id}\",\"value\":\"LoadBehavior\"}}"),
                default, Context("read-view-32"));
            Assert.Equal("shared", readback["registry_view"]!.GetValue<string>());
            Assert.Equal("2", readback["data"]!.GetValue<string>());
            using var root64 = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64);
            Assert.NotNull(root64.OpenSubKey($@"Software\Microsoft\Office\Outlook\Addins\{id}"));
        }
        finally { DeleteAddin(id); }
    }

    [Fact] public async Task Restart_ClosesNormallyWithoutForce_AndReportsEvidence()
    {
        KillFixtures();
        try
        {
            using var fixture = StartFixture("");
            var p = new WindowsActionProvider(restartTimeout: TimeSpan.FromSeconds(5), restartSettle: TimeSpan.FromSeconds(2));
            var result = await p.ExecuteAsync("app.restart", J($"{{\"process\":\"{fixture.ProcessName}.exe\",\"args\":\"\"}}"), default,
                Context("restart-clean"));
            Assert.True(result["safe"]!.GetValue<bool>());
            Assert.True(result["was_running"]!.GetValue<bool>());
            Assert.True(result["close_requested"]!.GetValue<bool>());
            Assert.True(result["restarted"]!.GetValue<bool>());
            Assert.True(result["responding"]!.GetValue<bool>());
        }
        finally { KillFixtures(); }
    }

    [Fact] public async Task Restart_LeavesIgnoredClosePromptRunning()
    {
        KillFixtures();
        try
        {
            using var fixture = StartFixture("--refuse-close");
            var p = new WindowsActionProvider(restartTimeout: TimeSpan.FromSeconds(1), restartSettle: TimeSpan.FromMilliseconds(200));
            var result = await p.ExecuteAsync("app.restart", J($"{{\"process\":\"{fixture.ProcessName}.exe\",\"args\":\"\"}}"), default,
                Context("restart-refused"));
            Assert.False(result["safe"]!.GetValue<bool>());
            Assert.False(fixture.HasExited);
        }
        finally { KillFixtures(); }
    }

    [Fact] public async Task Restart_StartsRegisteredApplicationWhenInitiallyAbsent()
    {
        KillFixtures();
        var name = Path.GetFileName(FixturePath());
        var appPath = $@"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}";
        try
        {
            using (var key = Registry.CurrentUser.CreateSubKey(appPath, true)!) key.SetValue(null, FixturePath());
            var p = new WindowsActionProvider(restartTimeout: TimeSpan.FromSeconds(1), restartSettle: TimeSpan.FromSeconds(2));
            var result = await p.ExecuteAsync("app.restart", J($"{{\"process\":\"{name}\",\"args\":\"\"}}"), default,
                Context("start-absent"));
            Assert.True(result["safe"]!.GetValue<bool>());
            Assert.False(result["was_running"]!.GetValue<bool>());
            Assert.True(result["started"]!.GetValue<bool>());
            Assert.False(result["restarted"]!.GetValue<bool>());
        }
        finally
        {
            Registry.CurrentUser.DeleteSubKeyTree(appPath, false);
            KillFixtures();
        }
    }

    [Theory]
    [InlineData("missing_executable")]
    [InlineData("launch_failure")]
    [InlineData("nonresponding")]
    public async Task Restart_ReportsUnsafeStartFailures(string scenario)
    {
        var name = "JoltIQStart" + Guid.NewGuid().ToString("N")[..8] + ".exe";
        var appPath = $@"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}";
        var invalidExe = Path.Combine(Path.GetTempPath(), name);
        var nonrespondingDir = Path.Combine(Path.GetTempPath(), "joltiq-fixture-" + Guid.NewGuid().ToString("N"));
        var nonrespondingExe = Path.Combine(nonrespondingDir, name);
        try
        {
            if (scenario != "missing_executable")
            {
                using var key = Registry.CurrentUser.CreateSubKey(appPath, true)!;
                if (scenario == "launch_failure") File.WriteAllText(invalidExe, "not an executable");
                if (scenario == "nonresponding")
                {
                    Directory.CreateDirectory(nonrespondingDir);
                    foreach (var file in Directory.GetFiles(Path.GetDirectoryName(FixturePath())!))
                        File.Copy(file, Path.Combine(nonrespondingDir, Path.GetFileName(file)), overwrite: true);
                    File.Copy(FixturePath(), nonrespondingExe, overwrite: true);
                }
                key.SetValue(null, scenario == "launch_failure" ? invalidExe : nonrespondingExe);
            }
            var args = scenario == "nonresponding" ? "/safe" : "";
            var p = new WindowsActionProvider(restartTimeout: TimeSpan.FromSeconds(1), restartSettle: TimeSpan.FromSeconds(1));
            var result = await p.ExecuteAsync("app.restart", J($"{{\"process\":\"{name}\",\"args\":\"{args}\"}}"), default,
                Context("start-" + scenario));
            Assert.False(result["safe"]!.GetValue<bool>());
            Assert.False(result["responding"]!.GetValue<bool>());
        }
        finally
        {
            Registry.CurrentUser.DeleteSubKeyTree(appPath, false);
            File.Delete(invalidExe);
            foreach (var process in System.Diagnostics.Process.GetProcessesByName(Path.GetFileNameWithoutExtension(name)))
            {
                try { process.Kill(entireProcessTree: true); process.WaitForExit(3000); }
                catch (InvalidOperationException) { }
                finally { process.Dispose(); }
            }
            if (Directory.Exists(nonrespondingDir)) Directory.Delete(nonrespondingDir, recursive: true);
            KillFixtures();
        }
    }

    private static string FixturePath() => Path.Combine(AppContext.BaseDirectory, "FixtureApp", "JoltIQ.Agent.FixtureApp.exe");

    private static System.Diagnostics.Process StartFixture(string arg)
    {
        var process = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(FixturePath(), arg) { UseShellExecute = true })!;
        Assert.True(SpinWait.SpinUntil(() => process.HasExited || process.MainWindowHandle != IntPtr.Zero, TimeSpan.FromSeconds(5)));
        Assert.False(process.HasExited);
        return process;
    }

    private static void KillFixtures()
    {
        foreach (var process in System.Diagnostics.Process.GetProcessesByName("JoltIQ.Agent.FixtureApp"))
        {
            try { process.Kill(entireProcessTree: true); process.WaitForExit(3000); }
            catch (InvalidOperationException) { }
            finally { process.Dispose(); }
        }
    }

    [Fact] public void Dpapi_RoundTrip_And_WrongScopeFails()
    {
        var dir = Path.Combine(Path.GetTempPath(), "jqtest-" + Guid.NewGuid().ToString("N"));
        var s = new DpapiKeyStore(DataProtectionScope.CurrentUser, dir);
        s.Save("k", new byte[] { 1, 2, 3 });
        Assert.Equal(new byte[] { 1, 2, 3 }, s.Load("k"));
        Assert.NotEqual(new byte[] { 1, 2, 3 }, File.ReadAllBytes(Path.Combine(dir, "k.dpapi")));  // encrypted at rest
    }

    [Fact] public void DpapiCommandJournal_EncryptsAndReloadsPendingResult()
    {
        var dir = Path.Combine(Path.GetTempPath(), "jqjournal-" + Guid.NewGuid().ToString("N"));
        try
        {
            var first = new DpapiCommandJournal(DataProtectionScope.CurrentUser, dir);
            Assert.True(first.MarkRunning(Context("dpapi-command"), "system.info"));
            Assert.True(first.MarkCompleted("dpapi-command", "done", new JsonObject { ["ok"] = true, ["secret"] = "not-plaintext" }));

            var stored = File.ReadAllBytes(Path.Combine(dir, "command-journal.v1.json.dpapi"));
            Assert.DoesNotContain("not-plaintext", System.Text.Encoding.UTF8.GetString(stored));
            var restarted = new DpapiCommandJournal(DataProtectionScope.CurrentUser, dir);
            Assert.Equal("dpapi-command", Assert.Single(restarted.PendingReports()).CommandId);
        }
        finally { if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true); }
    }

    [Fact] public void DpapiCommandJournal_RefusesExistingUndecryptableState()
    {
        var dir = Path.Combine(Path.GetTempPath(), "jqjournal-corrupt-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(dir);
            File.WriteAllBytes(Path.Combine(dir, "command-journal.v1.json.dpapi"), RandomNumberGenerator.GetBytes(64));
            Assert.Throws<InvalidDataException>(() => new DpapiCommandJournal(DataProtectionScope.CurrentUser, dir));
        }
        finally { if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true); }
    }

    private static LocalSession FixSession(string mode = "fix") => new()
    {
        SessionId = "session-1",
        Active = true,
        ComputerUseRunId = "run-1",
        ComputerUseMode = mode,
        ExpiresAt = DateTimeOffset.UtcNow.AddMinutes(5).ToUnixTimeSeconds(),
    };

    private static ActionExecutionContext ComputerUseContext(string mode = "fix") => new(
        "command-1", "device-1", "session-1", Sid, null, null, "run-1", mode);

    private sealed class FakeCapture : IDesktopCapture
    {
        public static readonly byte[] Png = [137, 80, 78, 71, 13, 10, 26, 10];
        public int Count { get; private set; }
        public int LastMaxLongEdge { get; private set; }

        public Task<DesktopFrame> CaptureAsync(int maxLongEdge, CancellationToken ct)
        {
            ct.ThrowIfCancellationRequested();
            Count++;
            LastMaxLongEdge = maxLongEdge;
            var bounds = new DesktopRectangle(-100, 0, 1600, 1200);
            var displays = new[] { new DesktopDisplay("primary", -100, 0, 1600, 1200, 96, 96, true) };
            return Task.FromResult(new DesktopFrame(Png, 800, 600, bounds, 2, 2, "layout-hash", Count, displays));
        }
    }

    private sealed class FakeDesktopInput : IDesktopInput
    {
        public List<string> Events { get; } = [];
        public bool Refuse { get; init; }

        private Task Add(string value)
        {
            if (Refuse) throw new DesktopInputRefusedException("test refusal");
            Events.Add(value);
            return Task.CompletedTask;
        }

        public Task ClickAsync(int x, int y, string button, int count, string? modifiers, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct) =>
            Add($"click:{x},{y}:{button}:{count}");
        public Task DragAsync(int x, int y, int toX, int toY, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct) =>
            Add($"drag:{x},{y}:{toX},{toY}");
        public Task MoveAsync(int x, int y, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct) => Add($"move:{x},{y}");
        public Task MouseAsync(string state, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct) => Add($"mouse:{state}");
        public Task TypeAsync(string text, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct) => Add($"type:{text}");
        public Task KeyAsync(string combo, int repeat, TimeSpan hold, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct) => Add($"key:{combo}:{repeat}");
        public Task ScrollAsync(int x, int y, string direction, int amount, long frameGeneration, string layoutHash, LocalSession session, CancellationToken ct) =>
            Add($"scroll:{x},{y}:{direction}:{amount}");
        public DesktopPoint Cursor() => new(10, 20);
    }
}
