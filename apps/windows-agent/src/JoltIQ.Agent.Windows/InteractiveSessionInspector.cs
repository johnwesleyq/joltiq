using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Text.Json.Nodes;

namespace JoltIQ.Agent.Windows;

public sealed record InteractiveSessionFact(
    int SessionId,
    string UserName,
    string? Sid,
    bool Active,
    bool Unlocked,
    bool IsTraySession,
    bool Remote);

/// <summary>Inspects Windows interactive sessions without impersonating another user.</summary>
public class InteractiveSessionInspector
{
    private readonly string? _enrolledSid;
    public InteractiveSessionInspector(string? enrolledSid = null) => _enrolledSid = enrolledSid;

    public virtual IReadOnlyList<InteractiveSessionFact> Sessions()
    {
        var result = new List<InteractiveSessionFact>();
        var traySession = Process.GetCurrentProcess().SessionId;
        var traySid = WindowsIdentity.GetCurrent().User?.Value;
        if (!WTSEnumerateSessions(IntPtr.Zero, 0, 1, out var buffer, out var count))
            return new[] { new InteractiveSessionFact(traySession, Environment.UserName, traySid, true, IsInputDesktopUnlocked(), true, IsRemoteSession()) };
        try
        {
            var size = Marshal.SizeOf<WtsSessionInfo>();
            for (var i = 0; i < count; i++)
            {
                var item = Marshal.PtrToStructure<WtsSessionInfo>(IntPtr.Add(buffer, i * size));
                if (item.State is not (WtsConnectState.Active or WtsConnectState.Connected or WtsConnectState.Disconnected)) continue;
                var user = QueryString(item.SessionId, WtsInfoClass.UserName);
                var domain = QueryString(item.SessionId, WtsInfoClass.DomainName);
                if (string.IsNullOrWhiteSpace(user)) continue;
                var account = string.IsNullOrWhiteSpace(domain) ? user : $"{domain}\\{user}";
                string? sid = item.SessionId == traySession ? traySid : TranslateSid(account);
                result.Add(new(item.SessionId, account, sid, item.State == WtsConnectState.Active,
                    item.SessionId == traySession && IsInputDesktopUnlocked(), item.SessionId == traySession,
                    QueryUShort(item.SessionId, WtsInfoClass.ClientProtocolType) != 0));
            }
        }
        finally { WTSFreeMemory(buffer); }
        return result;
    }

    public virtual JsonObject Inspect()
    {
        var sessions = Sessions();
        var active = sessions.Where(x => x.Active).ToList();
        var tray = sessions.SingleOrDefault(x => x.IsTraySession);
        var reasons = new JsonArray();
        if (_enrolledSid is null) reasons.Add("enrolled_user_unknown");
        if (active.Count == 0) reasons.Add("no_active_user");
        if (active.Count > 1) reasons.Add("multiple_active_users");
        if (sessions.Count > 1) reasons.Add("multiple_interactive_users");
        if (sessions.Any(x => x.Remote)) reasons.Add("remote_session");
        if (tray is null || !tray.Active) reasons.Add("tray_user_not_active");
        if (tray is not null && !tray.Unlocked) reasons.Add("screen_locked");
        if (active.Count == 1 && active[0].Sid != _enrolledSid) reasons.Add("enrolled_user_mismatch");
        return new JsonObject
        {
            ["enrolled_sid"] = _enrolledSid,
            ["interactive_sid"] = active.Count == 1 ? active[0].Sid : null,
            ["session_id"] = tray?.SessionId,
            ["active_session_count"] = active.Count,
            ["unlocked"] = tray?.Unlocked ?? false,
            ["same_user"] = active.Count == 1 && active[0].Sid == _enrolledSid,
            ["supported"] = reasons.Count == 0,
            ["reasons"] = reasons,
        };
    }

    private static string? TranslateSid(string account)
    {
        try { return ((SecurityIdentifier)new NTAccount(account).Translate(typeof(SecurityIdentifier))).Value; }
        catch (IdentityNotMappedException) { return null; }
    }

    private static string QueryString(int sessionId, WtsInfoClass info)
    {
        if (!WTSQuerySessionInformation(IntPtr.Zero, sessionId, info, out var p, out _)) return "";
        try { return Marshal.PtrToStringUni(p) ?? ""; }
        finally { WTSFreeMemory(p); }
    }

    private static ushort QueryUShort(int sessionId, WtsInfoClass info)
    {
        if (!WTSQuerySessionInformation(IntPtr.Zero, sessionId, info, out var p, out var bytes) || bytes < sizeof(ushort)) return 0;
        try { return unchecked((ushort)Marshal.ReadInt16(p)); }
        finally { WTSFreeMemory(p); }
    }

    private static bool IsInputDesktopUnlocked()
    {
        var desktop = OpenInputDesktop(0, false, 0x0001);
        if (desktop == IntPtr.Zero) return false;
        try
        {
            GetUserObjectInformation(desktop, 2, null, 0, out var needed);
            var name = new StringBuilder((int)needed / 2 + 1);
            return GetUserObjectInformation(desktop, 2, name, needed, out _) &&
                   string.Equals(name.ToString(), "Default", StringComparison.OrdinalIgnoreCase);
        }
        finally { CloseDesktop(desktop); }
    }

    private static bool IsRemoteSession() => GetSystemMetrics(0x1000) != 0;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WtsSessionInfo { public int SessionId; public string WinStationName; public WtsConnectState State; }
    private enum WtsConnectState { Active, Connected, ConnectQuery, Shadow, Disconnected, Idle, Listen, Reset, Down, Init }
    private enum WtsInfoClass
    {
        InitialProgram = 0,
        ApplicationName = 1,
        WorkingDirectory = 2,
        OemId = 3,
        SessionId = 4,
        UserName = 5,
        WinStationName = 6,
        DomainName = 7,
        ClientProtocolType = 16,
    }

    [DllImport("wtsapi32.dll", SetLastError = true)] private static extern bool WTSEnumerateSessions(IntPtr server, int reserved, int version, out IntPtr sessions, out int count);
    [DllImport("wtsapi32.dll")] private static extern void WTSFreeMemory(IntPtr memory);
    [DllImport("wtsapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool WTSQuerySessionInformation(IntPtr server, int sessionId, WtsInfoClass info, out IntPtr buffer, out int bytes);
    [DllImport("user32.dll", SetLastError = true)] private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool GetUserObjectInformation(IntPtr obj, int index, StringBuilder? info, uint length, out uint needed);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
}
