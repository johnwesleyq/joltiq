using System.Security.Cryptography;
using System.Text;

namespace JoltIQ.Agent.Core;

/// <summary>Device key (ECDSA P-256). Persistence is delegated to an <see cref="IKeyStore"/> (DPAPI on Windows).</summary>
public sealed class DeviceKey : IDisposable
{
    private readonly ECDsa _key;
    private DeviceKey(ECDsa key) => _key = key;

    public static DeviceKey Create() => new(ECDsa.Create(ECCurve.NamedCurves.nistP256));

    public static DeviceKey FromPkcs8(byte[] pkcs8)
    {
        var k = ECDsa.Create();
        k.ImportPkcs8PrivateKey(pkcs8, out _);
        return new DeviceKey(k);
    }

    public byte[] ExportPkcs8() => _key.ExportPkcs8PrivateKey();
    public string PublicKeyPem() => _key.ExportSubjectPublicKeyInfoPem();

    public string Sign(byte[] message) =>
        Convert.ToBase64String(_key.SignData(message, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence));

    public void Dispose() => _key.Dispose();
}

public static class RequestSigning
{
    /// <summary>String-to-sign defined in PROTOCOL.md §2. Must match server `request_string`.</summary>
    public static byte[] StringToSign(string method, string path, string ts, string nonce, string counter, byte[] body)
    {
        var bodyHash = Convert.ToHexString(SHA256.HashData(body)).ToLowerInvariant();
        return Encoding.UTF8.GetBytes(string.Join('\n', method.ToUpperInvariant(), path, ts, nonce, counter, bodyHash));
    }

    public static string NewNonce()
    {
        Span<byte> b = stackalloc byte[18];
        RandomNumberGenerator.Fill(b);
        return Convert.ToBase64String(b).Replace('+', '-').Replace('/', '_').TrimEnd('=');
    }
}

/// <summary>Verifies server envelopes with the server public key pinned at enrollment.</summary>
public sealed class EnvelopeVerifier : IDisposable
{
    private readonly ECDsa _server;

    public EnvelopeVerifier(string serverPublicKeyPem)
    {
        _server = ECDsa.Create();
        _server.ImportFromPem(serverPublicKeyPem);
        if (_server.KeySize != 256) throw new CryptographicException("server key must be P-256");
    }

    public bool Verify(byte[] payload, string signatureB64)
    {
        try
        {
            return _server.VerifyData(payload, Convert.FromBase64String(signatureB64), HashAlgorithmName.SHA256,
                DSASignatureFormat.Rfc3279DerSequence);
        }
        catch (FormatException) { return false; }
    }

    public void Dispose() => _server.Dispose();
}

public interface IKeyStore
{
    byte[]? Load(string name);
    void Save(string name, byte[] data);
    void Delete(string name);
}

/// <summary>Plain file store. TEST/SIMULATOR ONLY — the Windows build uses DpapiKeyStore.</summary>
public sealed class InsecureFileKeyStore(string dir) : IKeyStore
{
    public byte[]? Load(string name) { var p = Path.Combine(dir, name); return File.Exists(p) ? File.ReadAllBytes(p) : null; }
    public void Save(string name, byte[] data) { Directory.CreateDirectory(dir); File.WriteAllBytes(Path.Combine(dir, name), data); }
    public void Delete(string name) { var p = Path.Combine(dir, name); if (File.Exists(p)) File.Delete(p); }
}
