using System.Diagnostics;
using System.Runtime.Versioning;
using Microsoft.Win32;

namespace JoltIQ.OutlookCrashFixture;

internal static class FixtureCommand
{
    private const string Marker = "JoltIQ acceptance fixture; safe to remove";

#pragma warning disable CA1416 // Install/remove are runtime-gated by WindowsOnly; analyzer cannot follow the delegate.
    internal static int Run(string[] args)
    {
        try
        {
            if (args.Length == 0) return Usage();
            return args[0] switch
            {
                "validate-harness" => HarnessContractSelfTests.Run(Value(args, "--cases")),
                "install" => WindowsOnly(() => Install(Value(args, "--progid"))),
                "remove" => WindowsOnly(() => Remove(Value(args, "--progid"))),
                "emit" => Emit(Value(args, "--kind"), Value(args, "--module")),
                "version" => Version(),
                _ => Usage()
            };
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
    }
#pragma warning restore CA1416

    [SupportedOSPlatform("windows")]
    private static int Install(string progId)
    {
        RequireWindows();
        ValidateProgId(progId);
        using var key = Registry.CurrentUser.CreateSubKey($@"Software\Microsoft\Office\Outlook\Addins\{progId}", true)
            ?? throw new InvalidOperationException("could not create HKCU fixture key");
        key.SetValue("LoadBehavior", 3, RegistryValueKind.DWord);
        key.SetValue("FriendlyName", "JoltIQ Outlook PDF acceptance fixture", RegistryValueKind.String);
        key.SetValue("Description", Marker, RegistryValueKind.String);
        Console.WriteLine($"Installed harmless HKCU fixture identity {progId}; no COM server was registered.");
        return 0;
    }

    [SupportedOSPlatform("windows")]
    private static int Remove(string progId)
    {
        RequireWindows();
        ValidateProgId(progId);
        var path = $@"Software\Microsoft\Office\Outlook\Addins\{progId}";
        using var key = Registry.CurrentUser.OpenSubKey(path);
        if (key?.GetValue("Description")?.ToString() != Marker)
            throw new InvalidOperationException("refusing to remove a registry key not owned by this fixture");
        Registry.CurrentUser.DeleteSubKeyTree(path, false);
        Console.WriteLine($"Removed fixture identity {progId}.");
        return 0;
    }

    private static int Emit(string kind, string module)
    {
        RequireWindows();
        if (kind is not ("matching" or "nonmatching")) throw new ArgumentException("--kind must be matching or nonmatching");
        var selectedModule = kind == "matching" ? module : "JoltIQ.NonMatchingFixture.dll";
        var description = $"Faulting application name: OUTLOOK.EXE; Faulting module name: {selectedModule}; authorized synthetic JoltIQ acceptance event; no process crashed.";
        using var process = Process.Start(new ProcessStartInfo
        {
            FileName = Path.Combine(Environment.SystemDirectory, "eventcreate.exe"),
            UseShellExecute = false,
            ArgumentList = { "/T", "ERROR", "/ID", "1000", "/L", "APPLICATION", "/SO", "JoltIQ Outlook Fixture", "/D", description }
        }) ?? throw new InvalidOperationException("eventcreate.exe did not start");
        process.WaitForExit();
        if (process.ExitCode != 0) throw new InvalidOperationException($"eventcreate.exe failed with exit code {process.ExitCode}; run the authorized fixture in an elevated terminal");
        Console.WriteLine($"Emitted {kind} synthetic Application event; no Outlook process was touched.");
        return 0;
    }

    private static int Version()
    {
        Console.WriteLine(typeof(FixtureCommand).Assembly.GetName().Version?.ToString() ?? "unknown");
        return 0;
    }

    private static string Value(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        if (index < 0 || index + 1 >= args.Length || string.IsNullOrWhiteSpace(args[index + 1]))
            throw new ArgumentException($"missing {name}");
        return args[index + 1];
    }

    private static void ValidateProgId(string value)
    {
        if (value.Length > 128 || value.Any(c => !(char.IsLetterOrDigit(c) || c is '.' or '-' or '_')))
            throw new ArgumentException("invalid fixture ProgID");
    }

    private static void RequireWindows()
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("fixture mutation/event commands require Windows");
    }

    private static int Usage()
    {
        Console.Error.WriteLine("Usage: validate-harness --cases PATH | install/remove --progid ID | emit --kind matching|nonmatching --module NAME | version");
        return 2;
    }

    private static int WindowsOnly(Func<int> action)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("fixture mutation/event commands require Windows");
        return action();
    }
}
