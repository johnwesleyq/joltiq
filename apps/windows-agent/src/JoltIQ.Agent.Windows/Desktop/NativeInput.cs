using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace JoltIQ.Agent.Windows.Desktop;

public interface INativeInput
{
    Task MoveAsync(DesktopPoint point, CancellationToken ct);
    Task MouseButtonAsync(string button, bool down, CancellationToken ct);
    Task KeyAsync(string key, bool down, CancellationToken ct);
    Task TextAsync(string text, CancellationToken ct);
    Task ScrollAsync(DesktopPoint point, string direction, int amount, CancellationToken ct);
    DesktopPoint Cursor();
}

public sealed class NativeInput : INativeInput
{
    internal readonly record struct UnicodeTextStroke(char Character, bool KeyUp);
    internal readonly record struct TextStroke(ushort VirtualKey, char Character, bool KeyUp, bool Unicode);

    public Task MoveAsync(DesktopPoint point, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var screen = SystemInformation.VirtualScreen;
        if (!screen.Contains(point.X, point.Y)) throw new ArgumentOutOfRangeException(nameof(point));
        var x = screen.Width <= 1 ? 0 : (int)Math.Round((point.X - screen.Left) * 65535d / (screen.Width - 1));
        var y = screen.Height <= 1 ? 0 : (int)Math.Round((point.Y - screen.Top) * 65535d / (screen.Height - 1));
        Send(Mouse(x, y, MouseMove | MouseAbsolute | MouseVirtualDesk));
        return Task.CompletedTask;
    }

    public Task MouseButtonAsync(string button, bool down, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var flag = (button.ToLowerInvariant(), down) switch
        {
            ("left", true) => MouseLeftDown,
            ("left", false) => MouseLeftUp,
            ("right", true) => MouseRightDown,
            ("right", false) => MouseRightUp,
            ("middle", true) => MouseMiddleDown,
            ("middle", false) => MouseMiddleUp,
            _ => throw new ArgumentOutOfRangeException(nameof(button)),
        };
        Send(Mouse(0, 0, flag));
        return Task.CompletedTask;
    }

    public Task KeyAsync(string key, bool down, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var vk = VirtualKey(key);
        Send(Keyboard(vk, '\0', down ? 0u : KeyUp));
        return Task.CompletedTask;
    }

    public Task TextAsync(string text, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var inputs = TextBatch(text, VkKeyScan)
            .Select(stroke => Keyboard(
                stroke.VirtualKey,
                stroke.Character,
                (stroke.Unicode ? KeyUnicode : 0) | (stroke.KeyUp ? KeyUp : 0)))
            .ToArray();
        Send(inputs);
        ct.ThrowIfCancellationRequested();
        return Task.CompletedTask;
    }

    internal static IReadOnlyList<TextStroke> TextBatch(string text, Func<char, short> map)
    {
        var strokes = new List<TextStroke>(text.Length * 4);
        foreach (var character in text)
        {
            var mapped = map(character);
            if (mapped == -1)
            {
                strokes.Add(new TextStroke(0, character, false, true));
                strokes.Add(new TextStroke(0, character, true, true));
                continue;
            }

            var virtualKey = (ushort)(mapped & 0xff);
            var modifierBits = (mapped >> 8) & 0xff;
            var modifiers = new List<ushort>(3);
            if ((modifierBits & 2) != 0) modifiers.Add(0x11); // CTRL
            if ((modifierBits & 1) != 0) modifiers.Add(0x10); // SHIFT
            if ((modifierBits & 4) != 0) modifiers.Add(0x12); // ALT
            foreach (var modifier in modifiers)
                strokes.Add(new TextStroke(modifier, '\0', false, false));
            strokes.Add(new TextStroke(virtualKey, '\0', false, false));
            strokes.Add(new TextStroke(virtualKey, '\0', true, false));
            for (var i = modifiers.Count - 1; i >= 0; i--)
                strokes.Add(new TextStroke(modifiers[i], '\0', true, false));
        }
        return strokes;
    }

    internal static IReadOnlyList<UnicodeTextStroke> UnicodeTextBatch(string text)
    {
        var strokes = new UnicodeTextStroke[text.Length * 2];
        for (var i = 0; i < text.Length; i++)
        {
            strokes[i * 2] = new UnicodeTextStroke(text[i], false);
            strokes[i * 2 + 1] = new UnicodeTextStroke(text[i], true);
        }
        return strokes;
    }

    public async Task ScrollAsync(DesktopPoint point, string direction, int amount, CancellationToken ct)
    {
        await MoveAsync(point, ct);
        ct.ThrowIfCancellationRequested();
        var horizontal = direction is "left" or "right";
        var sign = direction is "up" or "right" ? 1 : -1;
        Send(Mouse(0, 0, horizontal ? MouseHWheel : MouseWheel, unchecked((uint)(sign * amount * WheelDelta))));
    }

    public DesktopPoint Cursor()
    {
        if (!GetCursorPos(out var point)) throw new Win32Exception(Marshal.GetLastWin32Error());
        return new DesktopPoint(point.X, point.Y);
    }

    private static ushort VirtualKey(string key)
    {
        var normalized = key.Trim().ToLowerInvariant();
        if (NamedKeys.TryGetValue(normalized, out var named)) return named;
        if (normalized.Length == 1)
        {
            var value = VkKeyScan(normalized[0]);
            if (value != -1) return (ushort)(value & 0xff);
        }
        throw new DesktopInputRefusedException($"unsupported key '{key}'");
    }

    private static readonly IReadOnlyDictionary<string, ushort> NamedKeys = new Dictionary<string, ushort>(StringComparer.OrdinalIgnoreCase)
    {
        ["ctrl"] = 0x11, ["control"] = 0x11, ["shift"] = 0x10, ["alt"] = 0x12, ["win"] = 0x5B,
        ["return"] = 0x0D, ["enter"] = 0x0D, ["tab"] = 0x09, ["escape"] = 0x1B, ["esc"] = 0x1B,
        ["backspace"] = 0x08, ["delete"] = 0x2E, ["up"] = 0x26, ["down"] = 0x28, ["left"] = 0x25,
        ["right"] = 0x27, ["home"] = 0x24, ["end"] = 0x23, ["pageup"] = 0x21, ["pagedown"] = 0x22,
        ["space"] = 0x20, ["f1"] = 0x70, ["f2"] = 0x71, ["f3"] = 0x72, ["f4"] = 0x73, ["f5"] = 0x74,
        ["f6"] = 0x75, ["f7"] = 0x76, ["f8"] = 0x77, ["f9"] = 0x78, ["f10"] = 0x79, ["f11"] = 0x7A, ["f12"] = 0x7B,
    };

    private static Input Mouse(int x, int y, uint flags, uint data = 0) => new()
    {
        Type = InputMouse,
        Union = new InputUnion { Mouse = new MouseInput { X = x, Y = y, MouseData = data, Flags = flags } },
    };

    private static Input Keyboard(ushort vk, char scan, uint flags) => new()
    {
        Type = InputKeyboard,
        Union = new InputUnion { Keyboard = new KeyboardInput { VirtualKey = vk, ScanCode = scan, Flags = flags } },
    };

    private static void Send(Input input)
    {
        if (SendInput(1, [input], Marshal.SizeOf<Input>()) != 1)
            throw new Win32Exception(Marshal.GetLastWin32Error());
    }

    private static void Send(Input[] inputs)
    {
        if (inputs.Length == 0) return;
        if (SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<Input>()) != inputs.Length)
            throw new Win32Exception(Marshal.GetLastWin32Error());
    }

    private const int WheelDelta = 120;
    private const uint InputMouse = 0;
    private const uint InputKeyboard = 1;
    private const uint MouseMove = 0x0001;
    private const uint MouseLeftDown = 0x0002;
    private const uint MouseLeftUp = 0x0004;
    private const uint MouseRightDown = 0x0008;
    private const uint MouseRightUp = 0x0010;
    private const uint MouseMiddleDown = 0x0020;
    private const uint MouseMiddleUp = 0x0040;
    private const uint MouseWheel = 0x0800;
    private const uint MouseHWheel = 0x1000;
    private const uint MouseVirtualDesk = 0x4000;
    private const uint MouseAbsolute = 0x8000;
    private const uint KeyUp = 0x0002;
    private const uint KeyUnicode = 0x0004;

    [StructLayout(LayoutKind.Sequential)]
    private struct Input { public uint Type; public InputUnion Union; }

    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] public MouseInput Mouse;
        [FieldOffset(0)] public KeyboardInput Keyboard;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MouseInput
    {
        public int X;
        public int Y;
        public uint MouseData;
        public uint Flags;
        public uint Time;
        public UIntPtr ExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KeyboardInput
    {
        public ushort VirtualKey;
        public ushort ScanCode;
        public uint Flags;
        public uint Time;
        public UIntPtr ExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint { public int X; public int Y; }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, Input[] inputs, int size);

    [DllImport("user32.dll")]
    private static extern short VkKeyScan(char character);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool GetCursorPos(out NativePoint point);
}
