using System.Text.Json;
using System.Text.Json.Nodes;

namespace JoltIQ.Agent.Core;

/// <summary>Trusted metadata from the verified command envelope. It is never sourced from action parameters.</summary>
public sealed record ActionExecutionContext(
    string CommandId,
    string DeviceId,
    string SessionId,
    string UserSid,
    string? ProcedureHash,
    string? ParametersHash = null);

/// <summary>Complete typed state for the single registry value an approved action may change.</summary>
public sealed record RegistryValueSnapshot(
    bool Exists,
    string KeyPath,
    string ValueName,
    string RegistryView,
    string? Kind,
    JsonNode? Value);

/// <summary>Local-only mutation lineage. Before is authoritative for rollback; server results are not.</summary>
public sealed record MutationRecord(
    string SourceCommandId,
    string DeviceId,
    string SessionId,
    string UserSid,
    string? ProcedureHash,
    string Action,
    RegistryValueSnapshot Before,
    bool Applied = false,
    RegistryValueSnapshot? After = null,
    string? RestoreCommandId = null);

public enum LocalCommandState
{
    Running,
    Completed,
}

/// <summary>Durable execution identity and terminal result for exactly-once local reconciliation.</summary>
public sealed record CommandJournalEntry(
    string CommandId,
    string DeviceId,
    string SessionId,
    string UserSid,
    string? ProcedureHash,
    string Action,
    LocalCommandState State,
    DateTimeOffset StartedAt,
    string? Status = null,
    JsonObject? Result = null,
    DateTimeOffset? CompletedAt = null,
    DateTimeOffset? AcknowledgedAt = null,
    string? ParametersHash = null);

public sealed record PendingCommandReport(string CommandId, string Status, JsonObject Result, DateTimeOffset CompletedAt);

/// <summary>
/// Durable command/result outbox plus local-only rollback state. Implementations must persist each successful method
/// before returning true. A mutation must refuse to run unless TrySaveMutation succeeds before the write.
/// </summary>
public interface ICommandJournal
{
    bool MarkRunning(ActionExecutionContext context, string action);
    bool TryGetCommand(string commandId, out CommandJournalEntry record);
    bool MarkCompleted(string commandId, string status, JsonObject result);
    IReadOnlyList<PendingCommandReport> PendingReports();
    bool AcknowledgeReport(string commandId);

    bool TrySaveMutation(MutationRecord record);
    bool TryGet(string commandId, out MutationRecord record);
    bool TryMarkMutationApplied(string commandId, RegistryValueSnapshot after);
    bool TryMarkRestored(string commandId, string restoreCommandId);
}

/// <summary>JSON journal persisted through an injected key store. Windows production supplies a CurrentUser DPAPI store.</summary>
public class PersistentCommandJournal : ICommandJournal
{
    private sealed class JournalDocument
    {
        public Dictionary<string, CommandJournalEntry> Commands { get; set; } = new(StringComparer.Ordinal);
        public Dictionary<string, MutationRecord> Mutations { get; set; } = new(StringComparer.Ordinal);
    }

    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = false };
    private readonly object _gate = new();
    private readonly IKeyStore _store;
    private readonly string _storeName;
    private readonly Func<DateTimeOffset> _now;
    private readonly int _maxAcknowledgedEntries;
    private readonly TimeSpan _ordinaryRetention;
    private readonly TimeSpan _mutationRetention;
    private JournalDocument _document;

    public PersistentCommandJournal(
        IKeyStore store,
        string storeName = "command-journal.v1.json",
        Func<DateTimeOffset>? now = null,
        int maxAcknowledgedEntries = 1000,
        TimeSpan? ordinaryRetention = null,
        TimeSpan? mutationRetention = null)
    {
        _store = store;
        _storeName = storeName;
        _now = now ?? (() => DateTimeOffset.UtcNow);
        _maxAcknowledgedEntries = Math.Max(1, maxAcknowledgedEntries);
        _ordinaryRetention = ordinaryRetention ?? TimeSpan.FromDays(7);
        _mutationRetention = mutationRetention ?? TimeSpan.FromDays(90);
        _document = Load();
        RecoverInterruptedCommands();
    }

    public bool MarkRunning(ActionExecutionContext context, string action) => Change(candidate =>
    {
        if (candidate.Commands.ContainsKey(context.CommandId)) return false;
        candidate.Commands[context.CommandId] = new CommandJournalEntry(
            context.CommandId, context.DeviceId, context.SessionId, context.UserSid, context.ProcedureHash,
            action, LocalCommandState.Running, _now(), ParametersHash: context.ParametersHash);
        return true;
    });

    public bool TryGetCommand(string commandId, out CommandJournalEntry record)
    {
        lock (_gate)
        {
            if (_document.Commands.TryGetValue(commandId, out var found))
            {
                record = Clone(found);
                return true;
            }
        }
        record = null!;
        return false;
    }

    public bool MarkCompleted(string commandId, string status, JsonObject result)
    {
        if (status is not ("done" or "failed" or "rejected")) return false;
        return Change(candidate =>
        {
            if (!candidate.Commands.TryGetValue(commandId, out var found) || found.State != LocalCommandState.Running) return false;
            candidate.Commands[commandId] = found with
            {
                State = LocalCommandState.Completed,
                Status = status,
                Result = result.DeepClone().AsObject(),
                CompletedAt = _now(),
            };
            return true;
        });
    }

    public IReadOnlyList<PendingCommandReport> PendingReports()
    {
        lock (_gate)
        {
            return _document.Commands.Values
                .Where(x => x.State == LocalCommandState.Completed && x.AcknowledgedAt is null && x.Status is not null && x.Result is not null)
                .OrderBy(x => x.CompletedAt)
                .Select(x => new PendingCommandReport(x.CommandId, x.Status!, x.Result!.DeepClone().AsObject(), x.CompletedAt!.Value))
                .ToArray();
        }
    }

    public bool AcknowledgeReport(string commandId) => Change(candidate =>
    {
        if (!candidate.Commands.TryGetValue(commandId, out var found) || found.State != LocalCommandState.Completed) return false;
        if (found.AcknowledgedAt is not null) return true;
        candidate.Commands[commandId] = found with { AcknowledgedAt = _now() };
        return true;
    });

    public bool TrySaveMutation(MutationRecord record) => Change(candidate =>
    {
        if (candidate.Mutations.ContainsKey(record.SourceCommandId)) return false;
        candidate.Mutations[record.SourceCommandId] = Clone(record);
        return true;
    });

    public bool TryGet(string commandId, out MutationRecord record)
    {
        lock (_gate)
        {
            if (_document.Mutations.TryGetValue(commandId, out var found))
            {
                record = Clone(found);
                return true;
            }
        }
        record = null!;
        return false;
    }

    public bool TryMarkMutationApplied(string commandId, RegistryValueSnapshot after) => Change(candidate =>
    {
        if (!candidate.Mutations.TryGetValue(commandId, out var found) || found.Applied) return false;
        candidate.Mutations[commandId] = found with { Applied = true, After = Clone(after) };
        return true;
    });

    public bool TryMarkRestored(string commandId, string restoreCommandId) => Change(candidate =>
    {
        if (!candidate.Mutations.TryGetValue(commandId, out var found) || !found.Applied || found.RestoreCommandId is not null) return false;
        candidate.Mutations[commandId] = found with { RestoreCommandId = restoreCommandId };
        return true;
    });

    private bool Change(Func<JournalDocument, bool> apply)
    {
        lock (_gate)
        {
            var candidate = Clone(_document);
            if (!apply(candidate)) return false;
            Prune(candidate);
            try { _store.Save(_storeName, JsonSerializer.SerializeToUtf8Bytes(candidate, JsonOptions)); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Security.Cryptography.CryptographicException)
            {
                return false;
            }
            _document = candidate;
            return true;
        }
    }

    private JournalDocument Load()
    {
        var raw = _store.Load(_storeName);
        if (raw is null) return new JournalDocument();
        try
        {
            return JsonSerializer.Deserialize<JournalDocument>(raw, JsonOptions)
                   ?? throw new InvalidDataException("the command journal contains no document");
        }
        catch (JsonException e)
        {
            throw new InvalidDataException("the command journal is corrupt; refusing to forget handled commands", e);
        }
    }

    private void RecoverInterruptedCommands()
    {
        var interrupted = _document.Commands.Values.Where(x => x.State == LocalCommandState.Running).ToArray();
        if (interrupted.Length == 0) return;
        var completedAt = _now();
        foreach (var entry in interrupted)
        {
            var hasMutation = _document.Mutations.TryGetValue(entry.CommandId, out var mutation);
            var changed = hasMutation && mutation!.Applied;
            var result = new JsonObject
            {
                ["ok"] = false,
                ["error"] = "the companion restarted while this command was running; the final outcome is unknown",
                ["outcome"] = "unknown",
                ["change_state"] = changed ? "confirmed" : hasMutation ? "unknown" : "not_applicable",
            };
            if (changed)
            {
                result["changed"] = true;
                result["source_command_id"] = entry.CommandId;
            }
            _document.Commands[entry.CommandId] = entry with
            {
                State = LocalCommandState.Completed,
                Status = "failed",
                Result = result,
                CompletedAt = completedAt,
            };
        }
        _store.Save(_storeName, JsonSerializer.SerializeToUtf8Bytes(_document, JsonOptions));
    }

    private void Prune(JournalDocument candidate)
    {
        var now = _now();
        var removable = candidate.Commands.Values
            .Where(x => x.AcknowledgedAt is not null)
            .Where(x => !candidate.Mutations.TryGetValue(x.CommandId, out var mutation) ||
                        mutation.RestoreCommandId is not null || now - x.StartedAt > _mutationRetention)
            .OrderByDescending(x => x.AcknowledgedAt)
            .ToList();
        foreach (var entry in removable.Where(x => now - x.AcknowledgedAt!.Value > _ordinaryRetention)
                     .Concat(removable.Skip(_maxAcknowledgedEntries)).DistinctBy(x => x.CommandId))
        {
            candidate.Commands.Remove(entry.CommandId);
            if (candidate.Mutations.TryGetValue(entry.CommandId, out var mutation) &&
                (mutation.RestoreCommandId is not null || now - entry.StartedAt > _mutationRetention))
                candidate.Mutations.Remove(entry.CommandId);
        }
    }

    private static T Clone<T>(T value) => JsonSerializer.Deserialize<T>(JsonSerializer.SerializeToUtf8Bytes(value, JsonOptions), JsonOptions)!;
}

/// <summary>Deterministic non-persistent journal for simulator and core tests.</summary>
public sealed class InMemoryCommandJournal : PersistentCommandJournal
{
    private sealed class MemoryStore : IKeyStore
    {
        private readonly Dictionary<string, byte[]> _values = new(StringComparer.Ordinal);
        public byte[]? Load(string name) => _values.TryGetValue(name, out var value) ? value.ToArray() : null;
        public void Save(string name, byte[] data) => _values[name] = data.ToArray();
        public void Delete(string name) => _values.Remove(name);
    }

    public InMemoryCommandJournal(Func<DateTimeOffset>? now = null) : base(new MemoryStore(), now: now) { }
}
