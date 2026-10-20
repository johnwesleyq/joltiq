using System.IO;
using System.Security.Principal;
using System.Text.Json.Nodes;
using JoltIQ.Agent.Core;
using Microsoft.Win32;

namespace JoltIQ.Agent.Windows;

public enum ActionHost
{
    InteractiveTray,
    BackgroundService,
}

/// <summary>Per-user Office add-in mutation with local-only, typed rollback state.</summary>
public sealed class OfficeAddinManager
{
    private const string ValueName = "LoadBehavior";
    private readonly string? _enrolledUserSid;
    private readonly ICommandJournal? _journal;
    private readonly ActionHost _host;
    private readonly Func<string?> _currentSid;

    public OfficeAddinManager(string? enrolledUserSid, ICommandJournal? journal, ActionHost host,
        Func<string?>? currentSid = null)
    {
        _enrolledUserSid = enrolledUserSid;
        _journal = journal;
        _host = host;
        _currentSid = currentSid ?? (() => WindowsIdentity.GetCurrent().User?.Value);
    }

    public bool Available => _host == ActionHost.InteractiveTray && _journal is not null && !string.IsNullOrWhiteSpace(_enrolledUserSid);

    public JsonObject SetLoadBehavior(string app, string progId, bool enabled, ActionExecutionContext? context)
    {
        var identityError = ValidateIdentity(context, out var sid);
        if (identityError is not null) return Fail(identityError);
        if (PolicyControls(app, progId)) return Fail("add-in is controlled by company policy");

        var candidates = ExistingUserKeys(app, progId);
        if (candidates.Count == 0) return Fail("add-in is not installed for this user");
        if (candidates.Count != 1) return Fail("add-in registry view is ambiguous; refusing to guess");
        var (view, path) = candidates[0];
        var before = ReadSnapshot(view, path);
        var record = new MutationRecord(
            context!.CommandId, context.DeviceId, context.SessionId, sid!, context.ProcedureHash,
            "office.addin.set_load_behavior", before);
        if (!_journal!.TrySaveMutation(record)) return Fail("original setting could not be saved locally; nothing was changed");

        RegistryValueSnapshot after;
        try
        {
            using var root = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, ToView(view));
            using var key = root.OpenSubKey(path, writable: true);
            if (key is null) return Fail("add-in disappeared before it could be changed");
            key.SetValue(ValueName, enabled ? 3 : 2, RegistryValueKind.DWord);
            after = ReadSnapshot(view, path);
        }
        catch (Exception e) when (e is UnauthorizedAccessException or IOException or System.Security.SecurityException)
        {
            return Fail("add-in setting could not be changed");
        }
        var expected = enabled ? 3 : 2;
        if (!after.Exists || after.Kind != RegistryValueKind.DWord.ToString() || after.Value?.GetValue<int>() != expected)
            return Fail("add-in setting read-back did not match the requested value");
        if (!_journal.TryMarkMutationApplied(context.CommandId, after))
            return new JsonObject
            {
                ["ok"] = false,
                ["changed"] = true,
                ["source_command_id"] = context.CommandId,
                ["addin"] = progId,
                ["before"] = Public(before),
                ["after"] = Public(after),
                ["error"] = "the setting changed, but the local journal could not confirm completion; escalate and do not retry",
            };
        return new JsonObject
        {
            ["ok"] = true,
            ["source_command_id"] = context.CommandId,
            ["addin"] = progId,
            ["before"] = Public(before),
            ["after"] = Public(after),
        };
    }

    public JsonObject Restore(string sourceCommandId, ActionExecutionContext? context)
    {
        var identityError = ValidateIdentity(context, out var sid);
        if (identityError is not null) return Fail(identityError);
        if (!_journal!.TryGet(sourceCommandId, out var record) || !record.Applied)
            return Fail("no completed local change matches that command");
        if (record.RestoreCommandId is not null) return Fail("that local change has already been restored");
        if (record.Action != "office.addin.set_load_behavior" || record.DeviceId != context!.DeviceId || record.UserSid != sid ||
            record.ProcedureHash != context.ProcedureHash)
            return Fail("local rollback lineage does not match this device, user, procedure, and action");
        if (!TryParseOfficePath(record.Before.KeyPath, out var app, out var progId))
            return Fail("local rollback target is invalid");
        if (PolicyControls(app, progId)) return Fail("add-in is now controlled by company policy");

        try
        {
            using var root = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, ToView(record.Before.RegistryView));
            using var key = root.OpenSubKey(record.Before.KeyPath, writable: true);
            if (key is null) return Fail("the original add-in key no longer exists");
            if (record.Before.Exists)
            {
                if (!Enum.TryParse<RegistryValueKind>(record.Before.Kind, out var kind)) return Fail("saved registry value kind is invalid");
                key.SetValue(record.Before.ValueName, Decode(record.Before.Value, kind), kind);
            }
            else key.DeleteValue(record.Before.ValueName, throwOnMissingValue: false);
        }
        catch (Exception e) when (e is UnauthorizedAccessException or IOException or System.Security.SecurityException)
        {
            return Fail("the original add-in setting could not be restored");
        }

        var after = ReadSnapshot(record.Before.RegistryView, record.Before.KeyPath);
        if (!Equivalent(record.Before, after)) return Fail("restored add-in setting failed independent read-back");
        if (!_journal.TryMarkRestored(sourceCommandId, context.CommandId))
            return Fail("the local journal refused to complete this restore");
        return new JsonObject
        {
            ["ok"] = true,
            ["source_command_id"] = sourceCommandId,
            ["restore_command_id"] = context.CommandId,
            ["restored"] = Public(after),
        };
    }

    public JsonObject VerifyRestored(string sourceCommandId, ActionExecutionContext? context)
    {
        var identityError = ValidateIdentity(context, out var sid);
        if (identityError is not null) return Fail(identityError);
        if (!_journal!.TryGet(sourceCommandId, out var record) || !record.Applied || record.RestoreCommandId is null)
            return Fail("no completed local restore matches that command");
        if (record.Action != "office.addin.set_load_behavior" || record.DeviceId != context!.DeviceId || record.UserSid != sid ||
            record.ProcedureHash != context.ProcedureHash)
            return Fail("local verification lineage does not match this device, user, procedure, and action");
        var observed = ReadSnapshot(record.Before.RegistryView, record.Before.KeyPath);
        return new JsonObject
        {
            ["ok"] = true,
            ["source_command_id"] = sourceCommandId,
            ["matches_local_journal"] = Equivalent(record.Before, observed),
            ["observed"] = Public(observed),
        };
    }

    private string? ValidateIdentity(ActionExecutionContext? context, out string? sid)
    {
        sid = _currentSid();
        if (_host != ActionHost.InteractiveTray) return "Office add-in changes run only in the interactive tray process";
        if (_journal is null) return "the encrypted local command journal is unavailable";
        if (context is null || string.IsNullOrWhiteSpace(context.CommandId) || string.IsNullOrWhiteSpace(context.DeviceId))
            return "trusted command context is missing";
        if (string.IsNullOrWhiteSpace(sid) || sid == new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null).Value)
            return "no eligible interactive Windows user is running this action";
        if (string.IsNullOrWhiteSpace(_enrolledUserSid) || !string.Equals(sid, _enrolledUserSid, StringComparison.OrdinalIgnoreCase))
            return "the logged-in Windows user no longer matches the enrolled user";
        if (!string.IsNullOrWhiteSpace(context.UserSid) && !string.Equals(context.UserSid, sid, StringComparison.OrdinalIgnoreCase))
            return "the diagnosed Windows user no longer matches the current user";
        return null;
    }

    private static List<(string View, string Path)> ExistingUserKeys(string app, string progId)
    {
        var path = $@"Software\Microsoft\Office\{app}\Addins\{progId}";
        var found = new List<(string, string)>();
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            using var root = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, view);
            using var key = root.OpenSubKey(path);
            if (key is not null) found.Add((FromView(view), path));
        }
        return found;
    }

    private static bool PolicyControls(string app, string progId)
    {
        var paths = new[]
        {
            $@"Software\Policies\Microsoft\Office\16.0\{app}\Resiliency\AddinList",
            $@"Software\Microsoft\Office\16.0\{app}\Resiliency\AddinList",
        };
        foreach (var hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        foreach (var path in paths)
        {
            using var root = RegistryKey.OpenBaseKey(hive, view);
            using var key = root.OpenSubKey(path);
            if (key?.GetValue(progId) is not null) return true;
        }
        return false;
    }

    private static RegistryValueSnapshot ReadSnapshot(string view, string path)
    {
        using var root = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, ToView(view));
        using var key = root.OpenSubKey(path);
        var names = key?.GetValueNames() ?? Array.Empty<string>();
        var exists = names.Contains(ValueName, StringComparer.OrdinalIgnoreCase);
        if (!exists) return new(false, path, ValueName, view, null, null);
        var kind = key!.GetValueKind(ValueName);
        var value = key.GetValue(ValueName, null, RegistryValueOptions.DoNotExpandEnvironmentNames);
        return new(true, path, ValueName, view, kind.ToString(), Encode(value, kind));
    }

    private static JsonNode? Encode(object? value, RegistryValueKind kind) => kind switch
    {
        RegistryValueKind.DWord => JsonValue.Create(Convert.ToInt32(value)),
        RegistryValueKind.QWord => JsonValue.Create(Convert.ToInt64(value)),
        RegistryValueKind.MultiString => new JsonArray(((string[]?)value ?? Array.Empty<string>())
            .Select(item => (JsonNode?)JsonValue.Create(item)).ToArray()),
        RegistryValueKind.Binary or RegistryValueKind.None => JsonValue.Create(Convert.ToBase64String((byte[]?)value ?? Array.Empty<byte>())),
        _ => JsonValue.Create(value?.ToString()),
    };

    private static object Decode(JsonNode? value, RegistryValueKind kind) => kind switch
    {
        RegistryValueKind.DWord => value!.GetValue<int>(),
        RegistryValueKind.QWord => value!.GetValue<long>(),
        RegistryValueKind.MultiString => value!.AsArray().Select(x => x!.GetValue<string>()).ToArray(),
        RegistryValueKind.Binary or RegistryValueKind.None => Convert.FromBase64String(value!.GetValue<string>()),
        _ => value?.GetValue<string>() ?? "",
    };

    private static JsonObject Public(RegistryValueSnapshot state) => new()
    {
        ["exists"] = state.Exists,
        ["key"] = $@"HKCU\{state.KeyPath}",
        ["value_name"] = state.ValueName,
        ["registry_view"] = state.RegistryView,
        ["kind"] = state.Kind,
        ["value"] = state.Value?.DeepClone(),
    };

    private static bool Equivalent(RegistryValueSnapshot expected, RegistryValueSnapshot actual) =>
        expected.Exists == actual.Exists && expected.KeyPath == actual.KeyPath && expected.ValueName == actual.ValueName &&
        expected.RegistryView == actual.RegistryView && expected.Kind == actual.Kind &&
        (expected.Value?.ToJsonString() ?? "null") == (actual.Value?.ToJsonString() ?? "null");

    private static bool TryParseOfficePath(string path, out string app, out string progId)
    {
        var parts = path.Split('\\');
        app = parts.Length == 6 ? parts[3] : "";
        progId = parts.Length == 6 ? parts[5] : "";
        return parts.Length == 6 && parts[0] == "Software" && parts[1] == "Microsoft" && parts[2] == "Office" &&
            parts[4] == "Addins" && app is "Outlook" or "Excel" or "Word" && !string.IsNullOrWhiteSpace(progId);
    }

    private static RegistryView ToView(string view) => view == "32" ? RegistryView.Registry32 : RegistryView.Registry64;
    private static string FromView(RegistryView view) => view == RegistryView.Registry32 ? "32" : "64";
    private static JsonObject Fail(string error) => new() { ["ok"] = false, ["error"] = error };
}
