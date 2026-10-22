using System.IO;
using System.Security.Cryptography;
using JoltIQ.Agent.Core;

namespace JoltIQ.Agent.Windows;

/// <summary>
/// Production per-user command journal. The complete serialized journal, including mutation pre-state and pending
/// reports, is encrypted by Windows DPAPI and can be opened only by the logged-in tray user's Windows account.
/// Screenshot evidence uses separate command-derived store names through the same CurrentUser DPAPI key store.
/// </summary>
public sealed class DpapiCommandJournal : PersistentCommandJournal
{
    private const string JournalName = "command-journal.v1.json";

    public DpapiCommandJournal(DpapiKeyStore store) : base(Validate(store), JournalName) { }

    public DpapiCommandJournal(DataProtectionScope scope = DataProtectionScope.CurrentUser, string? dir = null)
        : this(new DpapiKeyStore(scope, dir)) { }

    private static DpapiKeyStore Validate(DpapiKeyStore store)
    {
        if (store.Exists(JournalName) && store.Load(JournalName) is null)
            throw new InvalidDataException("the DPAPI command journal cannot be decrypted; refusing to forget handled commands");
        return store;
    }
}
