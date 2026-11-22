using FetchMcp.Net;
using FetchMcp.Sanitize;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace FetchMcp.Tests;

public class FetcherTests
{
    private static PageFetcher Make(FetchOptions? o = null)
    {
        o ??= new FetchOptions();
        var services = new ServiceCollection();
        services.AddHttpClient(PageFetcher.ClientName).ConfigurePrimaryHttpMessageHandler(() => SafeHttp.CreateHandler());
        var sp = services.BuildServiceProvider();
        var opts = Options.Create(o);
        return new PageFetcher(sp.GetRequiredService<IHttpClientFactory>(), new Sanitizer(opts), opts, TimeProvider.System);
    }

    [Theory]
    [InlineData("file:///etc/passwd")] [InlineData("ftp://example.com/")] [InlineData("not a url")]
    [InlineData("http://user:pw@example.com/")]
    public async Task Rejects_bad_urls(string url) =>
        await Assert.ThrowsAsync<FetchRefusedException>(() => Make().FetchAsync(url, default));

    [Theory]
    [InlineData("http://127.0.0.1:9/")] [InlineData("http://169.254.169.254/latest/meta-data/")]
    [InlineData("http://[::1]:9/")] [InlineData("http://localhost:9/")]
    public async Task Blocks_internal_destinations(string url)
    {
        var ex = await Assert.ThrowsAsync<FetchRefusedException>(() => Make().FetchAsync(url, default));
        Assert.Equal("ssrf.dns_ip", ex.Code);
    }

    [Fact]  // does not resolve outside Railway: fails at DNS, which is also a refusal to connect
    public async Task Unresolvable_internal_hostname_fails() =>
        await Assert.ThrowsAsync<HttpRequestException>(() => Make().FetchAsync("http://fetch-mcp.railway.internal/", default));

    [Fact]
    public async Task Enforces_domain_allowlist() =>
        await Assert.ThrowsAsync<FetchRefusedException>(() =>
            Make(new FetchOptions { AllowedDomains = ["example.com"] }).FetchAsync("https://evil.test/", default));
}
