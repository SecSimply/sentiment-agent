using System.Net;
using System.Net.Sockets;

namespace FetchMcp.Net;

public static class SafeHttp
{
    /// <summary>
    /// Handler that resolves DNS itself and connects only to vetted IPs, so DNS rebinding
    /// between "check" and "connect" is impossible. No proxies, no auto-redirects.
    /// </summary>
    public static SocketsHttpHandler CreateHandler(Func<IPAddress, bool>? isAllowed = null)
    {
        isAllowed ??= IpGuard.IsPublic;
        return new SocketsHttpHandler
        {
            AllowAutoRedirect = false,
            UseProxy = false,
            UseCookies = false,
            AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate | DecompressionMethods.Brotli,
            ConnectTimeout = TimeSpan.FromSeconds(5),
            ConnectCallback = async (ctx, ct) =>
            {
                var host = ctx.DnsEndPoint.Host;
                var addrs = IPAddress.TryParse(host, out var literal)
                    ? [literal]
                    : await Dns.GetHostAddressesAsync(host, ct);

                // Strict: if ANY resolved address is non-public, refuse the host entirely.
                if (addrs.Length == 0 || !addrs.All(isAllowed))
                    throw new HttpRequestException("Destination address is not allowed.");

                var socket = new Socket(SocketType.Stream, ProtocolType.Tcp) { NoDelay = true };
                try
                {
                    await socket.ConnectAsync(addrs, ctx.DnsEndPoint.Port, ct);
                    return new NetworkStream(socket, ownsSocket: true);
                }
                catch
                {
                    socket.Dispose();
                    throw;
                }
            }
        };
    }
}
