using System.Text.Json.Nodes;

namespace JoltIQ.Agent.Core;

/// <summary>Trusted metadata from the verified command envelope. It is never sourced from action parameters.</summary>
public sealed record ActionExecutionContext(
    string CommandId,
    string DeviceId,
    string SessionId,
    string UserSid,
    string? ProcedureHash);

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

/// <summary>
/// Minimal mutation journal contract. Task 6 supplies the durable DPAPI implementation and result outbox.
/// A mutation must refuse to run unless TrySaveMutation succeeds before the write.
/// </summary>
public interface ICommandJournal
{
    bool TrySaveMutation(MutationRecord record);
    bool TryGet(string commandId, out MutationRecord record);
    bool TryMarkMutationApplied(string commandId, RegistryValueSnapshot after);
    bool TryMarkRestored(string commandId, string restoreCommandId);
}
