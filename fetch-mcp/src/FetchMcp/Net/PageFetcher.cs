using System.Text;
using FetchMcp.Models;
using FetchMcp.Sanitize;
using Microsoft.Extensions.Options;

namespace FetchMcp.Net;

public sealed class FetchRefusedException(string message) : Exception(message);

public interface IPageFetcher
{
    Task<PageDoc> FetchAsync(string url, CancellationToken ct);
}

public sealed class PageFetcher(IHttpClientFactory httpFactory, Sanitizer sanitizer, IOptions<FetchOptions> options, TimeProvider clock) : IPageFetcher
{
    public const string ClientName = "safe";
    private static readonly string[] AllowedTypes = ["text/html", "text/plain", "application/json", "application/xml", "text/xml", "application/xhtml+xml", "application/rss+xml", "application/atom+xml"];

    public async Task<PageDoc> FetchAsync(string url, CancellationToken ct)
    {
        var o = options.Value;
        var uri = Validate(url, o);
        var client = httpFactory.CreateClient(ClientName);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(o.TimeoutSeconds));

        for (var hop = 0; ; hop++)
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, uri);
            req.Headers.UserAgent.ParseAdd("FetchMcp/1.0");
            using var resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, timeout.Token);

            if ((int)resp.StatusCode is >= 300 and < 400 && resp.Headers.Location is { } loc)
            {
                if (hop >= o.MaxRedirects) throw new FetchRefusedException("Too many redirects.");
                uri = Validate(new Uri(uri, loc).ToString(), o);   // re-validate every hop
                continue;
            }

            if (!resp.IsSuccessStatusCode) throw new FetchRefusedException($"Upstream returned {(int)resp.StatusCode}.");

            var mediaType = resp.Content.Headers.ContentType?.MediaType?.ToLowerInvariant() ?? "";
            if (!AllowedTypes.Contains(mediaType)) throw new FetchRefusedException("Content type not allowed.");

            var body = await ReadCappedAsync(resp, o.MaxBytes, timeout.Token);
            var clean = sanitizer.Sanitize(body, mediaType);
            return new PageDoc(uri.ToString(), uri.Host, clean.Title, clean.PublishedAt, clean.Text, clock.GetUtcNow(), clean.Info);
        }
    }

    public static Uri Validate(string url, FetchOptions o)
    {
        if (string.IsNullOrWhiteSpace(url) || url.Length > o.MaxUrlLength) throw new FetchRefusedException("Invalid URL.");
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)) throw new FetchRefusedException("Invalid URL.");
        if (uri.Scheme is not ("http" or "https")) throw new FetchRefusedException("Only http/https allowed.");
        if (!string.IsNullOrEmpty(uri.UserInfo)) throw new FetchRefusedException("Credentials in URL not allowed.");
        if (o.AllowedDomains.Length > 0 && !o.AllowedDomains.Any(d =>
                uri.Host.Equals(d, StringComparison.OrdinalIgnoreCase) || uri.Host.EndsWith("." + d, StringComparison.OrdinalIgnoreCase)))
            throw new FetchRefusedException("Domain not allowed.");
        return uri;
    }

    private static async Task<string> ReadCappedAsync(HttpResponseMessage resp, int maxBytes, CancellationToken ct)
    {
        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var ms = new MemoryStream();
        var buf = new byte[16 * 1024];
        int n;
        while ((n = await stream.ReadAsync(buf, ct)) > 0)
        {
            if (ms.Length + n > maxBytes) throw new FetchRefusedException("Response too large.");
            ms.Write(buf, 0, n);
        }
        Encoding enc;
        try { enc = Encoding.GetEncoding(resp.Content.Headers.ContentType?.CharSet ?? "utf-8"); }
        catch (ArgumentException) { enc = Encoding.UTF8; }
        return enc.GetString(ms.GetBuffer(), 0, (int)ms.Length);
    }
}
