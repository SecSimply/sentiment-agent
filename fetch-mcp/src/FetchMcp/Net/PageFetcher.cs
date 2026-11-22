using System.Text;
using FetchMcp.Models;
using FetchMcp.Sanitize;
using Microsoft.Extensions.Options;

namespace FetchMcp.Net;

/// <summary>A deliberate refusal. Code is a stable, server-controlled identifier safe to show to callers.</summary>
public sealed class FetchRefusedException(string message, string code = "refused") : Exception(message)
{
    public string Code { get; } = code;
}

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
        var checks = new List<Check>();
        var uri = Validate(url, o);
        checks.Add(new Check("url.valid", CheckStatus.Pass, "absolute http(s) URL, no embedded credentials, length within limit"));
        if (o.AllowedDomains.Length > 0)
            checks.Add(new Check("url.domain_allowlist", CheckStatus.Pass, "host is on the allow-list"));

        var client = httpFactory.CreateClient(ClientName);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(o.TimeoutSeconds));

        for (var hop = 0; ; hop++)
        {
            using var req = new HttpRequestMessage(HttpMethod.Get, uri);
            req.Options.Set(SafeHttp.TraceKey, checks);
            req.Headers.UserAgent.ParseAdd("FetchMcp/1.0");

            HttpResponseMessage resp;
            try { resp = await client.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, timeout.Token); }
            catch (HttpRequestException) when (checks.Any(c => c.Id == "ssrf.dns_ip" && c.Status == CheckStatus.Block))
            {
                throw new FetchRefusedException("Destination address is not allowed.", "ssrf.dns_ip");
            }

            using (resp)
            {
                if ((int)resp.StatusCode is >= 300 and < 400 && resp.Headers.Location is { } loc)
                {
                    if (hop >= o.MaxRedirects) throw new FetchRefusedException("Too many redirects.", "redirect.limit");
                    uri = Validate(new Uri(uri, loc).ToString(), o);   // re-validate every hop
                    checks.Add(new Check("redirect.revalidated", CheckStatus.Pass, $"hop {hop + 1}: redirect target re-validated ({uri.Host})"));
                    continue;
                }

                if (!resp.IsSuccessStatusCode) throw new FetchRefusedException($"Upstream returned {(int)resp.StatusCode}.", "http.status");
                checks.Add(new Check("http.status", CheckStatus.Pass, $"{(int)resp.StatusCode} {resp.ReasonPhrase}"));

                var mediaType = resp.Content.Headers.ContentType?.MediaType?.ToLowerInvariant() ?? "";
                if (!AllowedTypes.Contains(mediaType)) throw new FetchRefusedException("Content type not allowed.", "content.type");
                checks.Add(new Check("content.type", CheckStatus.Pass, $"{mediaType} is an allowed text type"));

                var (body, bytes) = await ReadCappedAsync(resp, o.MaxBytes, timeout.Token);
                checks.Add(new Check("content.size", CheckStatus.Pass, $"{bytes} bytes (limit {o.MaxBytes})"));

                var clean = sanitizer.Sanitize(body, mediaType);
                checks.AddRange(clean.Checks);
                return new PageDoc(uri.ToString(), uri.Host, clean.Title, clean.PublishedAt, clean.Text, clock.GetUtcNow(), clean.Info, checks);
            }
        }
    }

    public static Uri Validate(string url, FetchOptions o)
    {
        if (string.IsNullOrWhiteSpace(url) || url.Length > o.MaxUrlLength) throw new FetchRefusedException("Invalid URL.", "url.invalid");
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)) throw new FetchRefusedException("Invalid URL.", "url.invalid");
        if (uri.Scheme is not ("http" or "https")) throw new FetchRefusedException("Only http/https allowed.", "url.scheme");
        if (!string.IsNullOrEmpty(uri.UserInfo)) throw new FetchRefusedException("Credentials in URL not allowed.", "url.credentials");
        if (o.AllowedDomains.Length > 0 && !o.AllowedDomains.Any(d =>
                uri.Host.Equals(d, StringComparison.OrdinalIgnoreCase) || uri.Host.EndsWith("." + d, StringComparison.OrdinalIgnoreCase)))
            throw new FetchRefusedException("Domain not allowed.", "url.domain_allowlist");
        return uri;
    }

    private static async Task<(string Body, int Bytes)> ReadCappedAsync(HttpResponseMessage resp, int maxBytes, CancellationToken ct)
    {
        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var ms = new MemoryStream();
        var buf = new byte[16 * 1024];
        int n;
        while ((n = await stream.ReadAsync(buf, ct)) > 0)
        {
            if (ms.Length + n > maxBytes) throw new FetchRefusedException("Response too large.", "content.size");
            ms.Write(buf, 0, n);
        }
        Encoding enc;
        try { enc = Encoding.GetEncoding(resp.Content.Headers.ContentType?.CharSet ?? "utf-8"); }
        catch (ArgumentException) { enc = Encoding.UTF8; }
        return (enc.GetString(ms.GetBuffer(), 0, (int)ms.Length), (int)ms.Length);
    }
}
