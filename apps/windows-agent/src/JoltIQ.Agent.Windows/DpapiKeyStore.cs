using System.IO;
using System.Security.Cryptography;
using JoltIQ.Agent.Core;

namespace JoltIQ.Agent.Windows;

/// <summary>Stores the device private key and state encrypted with Windows DPAPI, bound to the current user
/// (tray, per-user install) and with additional entropy so other apps of the same user can't trivially decrypt it.</summary>
public sealed class DpapiKeyStore : IKeyStore
{
    private static readonly byte[] Entropy = "JoltIQ.Agent.v1.device-key"u8.ToArray();
    private readonly string _dir;
    private readonly DataProtectionScope _scope;

    public DpapiKeyStore(DataProtectionScope scope = DataProtectionScope.CurrentUser, string? dir = null)
    {
        _scope = scope;
        _dir = dir ?? Path.Combine(Environment.GetFolderPath(scope == DataProtectionScope.CurrentUser
            ? Environment.SpecialFolder.LocalApplicationData : Environment.SpecialFolder.CommonApplicationData), "JoltIQ", "Agent");
        Directory.CreateDirectory(_dir);
    }

    public byte[]? Load(string name)
    {
        var p = Path.Combine(_dir, name + ".dpapi");
        if (!File.Exists(p)) return null;
        try { return ProtectedData.Unprotect(File.ReadAllBytes(p), Entropy, _scope); }
        catch (CryptographicException) { return null; } // copied from another user/machine → treated as not enrolled
    }

    public bool Exists(string name) => File.Exists(Path.Combine(_dir, name + ".dpapi"));

    public void Save(string name, byte[] data)
    {
        var p = Path.Combine(_dir, name + ".dpapi");
        var tmp = p + ".tmp";
        File.WriteAllBytes(tmp, ProtectedData.Protect(data, Entropy, _scope));
        File.Move(tmp, p, overwrite: true);
    }

    public void Delete(string name)
    {
        var p = Path.Combine(_dir, name + ".dpapi");
        if (File.Exists(p)) File.Delete(p);
    }
}
