using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using JoltIQ.Agent.Core;

var failures = 0;
var passed = 0;
void T(string name, Action body)
{
    try { body(); passed++; Console.WriteLine($"PASS {name}"); }
    catch (Exception e) { failures++; Console.WriteLine($"FAIL {name}: {e.Message}"); }
}
void Assert(bool c, string m = "assertion failed") { if (!c) throw new Exception(m); }
JsonElement J(string s) => JsonDocument.Parse(s).RootElement;
var active = new LocalSession { SessionId = "s1", Active = true, ExpiresAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 600 };

T("string-to-sign matches server format", () =>
{
    var s = Encoding.UTF8.GetString(RequestSigning.StringToSign("post", "/api/v1/agent/v1/poll", "100", "abc", "7", Encoding.UTF8.GetBytes("{}")));
    Assert(s == "POST\n/api/v1/agent/v1/poll\n100\nabc\n7\n44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a", s);
});
T("device signature verifies with exported public key", () =>
{
    using var k = DeviceKey.Create();
    var msg = Encoding.UTF8.GetBytes("hello");
    using var pub = ECDsa.Create();
    pub.ImportFromPem(k.PublicKeyPem());
    Assert(pub.VerifyData(msg, Convert.FromBase64String(k.Sign(msg)), HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence));
    using var k2 = DeviceKey.FromPkcs8(k.ExportPkcs8());
    Assert(k2.PublicKeyPem() == k.PublicKeyPem(), "pkcs8 round trip");
});
T("envelope verifier rejects tampered payload and foreign key", () =>
{
    using var server = ECDsa.Create(ECCurve.NamedCurves.nistP256);
    var payload = Encoding.UTF8.GetBytes("{\"type\":\"action\"}");
    var sig = Convert.ToBase64String(server.SignData(payload, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence));
    using var v = new EnvelopeVerifier(server.ExportSubjectPublicKeyInfoPem());
    Assert(v.Verify(payload, sig), "valid");
    Assert(!v.Verify(Encoding.UTF8.GetBytes("{\"type\":\"actioN\"}"), sig), "tampered accepted");
    using var other = ECDsa.Create(ECCurve.NamedCurves.nistP256);
    using var v2 = new EnvelopeVerifier(other.ExportSubjectPublicKeyInfoPem());
    Assert(!v2.Verify(payload, sig), "foreign key accepted");
    Assert(!v.Verify(payload, "not-base64!!"), "garbage sig accepted");
});
T("replay guard: nonce reuse, expiry, age, future", () =>
{
    long now = 1_000_000;
    var g = new ReplayGuard(() => now);
    Assert(g.Check("n1", now, now + 60).ok);
    Assert(!g.Check("n1", now, now + 60).ok, "replay accepted");
    Assert(!g.Check("n2", now - 700, null).ok, "old accepted");
    Assert(!g.Check("n3", now, now - 1).ok, "expired accepted");
    Assert(!g.Check("n4", now + 500, null).ok, "future accepted");
    Assert(!g.Check(null, now, null).ok, "missing nonce accepted");
});
T("policy: requires active local session", () =>
{
    var p = new LocalPolicy();
    Assert(!p.Evaluate("system.info", J("{}"), null).Allowed);
    Assert(!p.Evaluate("system.info", J("{}"), new LocalSession { SessionId = "x", Active = false }).Allowed);
    Assert(!p.Evaluate("system.info", J("{}"), new LocalSession { SessionId = "x", Active = true, ExpiresAt = 1 }).Allowed, "expired");
    Assert(p.Evaluate("system.info", J("{}"), active).Allowed);
});
T("policy: no shell or unknown actions", () =>
{
    var p = new LocalPolicy();
    foreach (var a in new[] { "shell.exec", "powershell", "file.write", "registry.write", "delete.anything", "credential.read" })
        Assert(!p.Evaluate(a, J("{}"), active).Allowed, a);
});
T("policy: app/service allowlists", () =>
{
    var p = new LocalPolicy();
    Assert(p.Evaluate("app.restart", J("{\"process\":\"OUTLOOK.EXE\"}"), active).Allowed);
    Assert(!p.Evaluate("app.restart", J("{\"process\":\"lsass.exe\"}"), active).Allowed, "lsass");
    Assert(!p.Evaluate("app.restart", J("{\"process\":\"outlook.exe\",\"args\":\"/cleanprofile\"}"), active).Allowed, "args");
    Assert(p.Evaluate("service.restart", J("{\"name\":\"Spooler\"}"), active).Allowed);
    Assert(!p.Evaluate("service.restart", J("{\"name\":\"WinDefend\"}"), active).Allowed, "defender");
    Assert(!p.Evaluate("service.restart", J("{\"name\":\"a;b\"}"), active).Allowed, "injection");
});
T("policy: file + registry scoping", () =>
{
    var p = new LocalPolicy { ExpandEnv = s => s.Replace("%LOCALAPPDATA%", @"C:\Users\u\AppData\Local").Replace("%TEMP%", @"C:\T").Replace("%APPDATA%", @"C:\A").Replace("%ProgramData%", @"C:\PD") };
    Assert(p.Evaluate("file.inspect", J("{\"path\":\"%LOCALAPPDATA%\\\\Microsoft\\\\Outlook\\\\log.txt\"}"), active).Allowed);
    Assert(!p.Evaluate("file.inspect", J("{\"path\":\"%LOCALAPPDATA%\\\\Microsoft\\\\Outlook\\\\mail.ost\"}"), active).Allowed, "ost");
    Assert(!p.Evaluate("file.inspect", J("{\"path\":\"%LOCALAPPDATA%\\\\Microsoft\\\\Outlook\\\\..\\\\..\\\\x\"}"), active).Allowed, "traversal");
    Assert(!p.Evaluate("file.inspect", J("{\"path\":\"C:\\\\Windows\\\\System32\\\\config\\\\SAM\"}"), active).Allowed, "SAM");
    Assert(!p.Evaluate("file.inspect", J("{\"path\":\"\\\\\\\\server\\\\share\\\\x\"}"), active).Allowed, "UNC");
    Assert(p.Evaluate("config.read", J("{\"key\":\"HKCU\\\\Software\\\\Microsoft\\\\Office\\\\Outlook\\\\Addins\\\\X\",\"value\":\"LoadBehavior\"}"), active).Allowed);
    Assert(!p.Evaluate("config.read", J("{\"key\":\"HKLM\\\\SAM\\\\SAM\",\"value\":\"F\"}"), active).Allowed, "HKLM");
});
T("redactor scrubs secrets and secret-named keys", () =>
{
    var n = Redactor.RedactNode(JsonNode.Parse("{\"password\":\"x\",\"msg\":\"user bob@acme.com at C:\\\\Users\\\\bob\\\\a token=abc123\",\"list\":[\"4111 1111 1111 1111\"]}"))!;
    var s = n.ToJsonString();
    Assert(!s.Contains("bob@acme.com") && !s.Contains("abc123") && !s.Contains("4111") && !s.Contains("Users\\\\bob") && s.Contains("[REDACTED]"), s);
});
T("client refuses plain HTTP to non-local servers", () =>
{
    try { using var _ = new AgentClient("http://evil.example.com", new InsecureFileKeyStore(Path.GetTempPath())); throw new Exception("accepted"); }
    catch (ArgumentException) { }
});

Console.WriteLine($"\n{passed} passed, {failures} failed");
return failures == 0 ? 0 : 1;
