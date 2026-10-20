namespace JoltIQ.Agent.FixtureApp;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        var mode = args.FirstOrDefault() ?? "";
        var form = new Form { Text = "JoltIQ safe-restart fixture", Width = 420, Height = 160 };
        form.Controls.Add(new Label { Text = mode, Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter });
        if (mode == "--refuse-close") form.FormClosing += (_, e) => e.Cancel = true;
        form.Shown += (_, _) => File.WriteAllText(Path.Combine(Path.GetTempPath(), $"joltiq-fixture-{Environment.ProcessId}.ready"), mode);
        if (mode == "/safe") form.Shown += (_, _) => Thread.Sleep(Timeout.Infinite);
        Application.Run(form);
    }
}
