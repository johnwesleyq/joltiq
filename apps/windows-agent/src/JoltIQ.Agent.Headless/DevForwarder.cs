using System.Net;
using System.Net.Sockets;

namespace JoltIQ.Agent.Headless;

/// <summary>
/// DEV ONLY. AgentClient refuses plain HTTP except to localhost (production must be HTTPS); in compose the API is
/// reachable as http://api:8000. Rather than weaken that rule, the headless host can listen on 127.0.0.1:{port}
/// inside its own container and pipe bytes to the API. Enabled only by AGENT_DEV_FORWARD=host:port.
/// </summary>
public static class DevForwarder
{
    public static async Task RunAsync(int listenPort, string targetHost, int targetPort, CancellationToken ct)
    {
        var listener = new TcpListener(IPAddress.Loopback, listenPort);
        listener.Start();
        try
        {
            while (!ct.IsCancellationRequested)
            {
                var client = await listener.AcceptTcpClientAsync(ct).ConfigureAwait(false);
                _ = Task.Run(async () =>
                {
                    using (client)
                    using (var upstream = new TcpClient())
                    {
                        try
                        {
                            await upstream.ConnectAsync(targetHost, targetPort, ct).ConfigureAwait(false);
                            var a = client.GetStream(); var b = upstream.GetStream();
                            await Task.WhenAny(a.CopyToAsync(b, ct), b.CopyToAsync(a, ct)).ConfigureAwait(false);
                        }
                        catch (Exception e) when (e is IOException or SocketException or OperationCanceledException) { }
                    }
                }, ct);
            }
        }
        catch (OperationCanceledException) { }
        finally { listener.Stop(); }
    }
}

