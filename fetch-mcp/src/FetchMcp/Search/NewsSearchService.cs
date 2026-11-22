using FetchMcp.Models;
using FetchMcp.Net;
using FetchMcp.Sanitize;
using Microsoft.Extensions.Options;

namespace FetchMcp.Search;

/// <summary>
/// Search → fetch each hit through the SSRF-guarded fetcher → sanitize. Hit URLs and snippets are
/// untrusted: URLs get the same checks as any fetch, snippets get the same sanitizer as page text.
/// </summary>
public sealed class NewsSearchService(
    ISearchProvider provider, IPageFetcher fetcher, Sanitizer sanitizer,
    IOptions<FetchOptions> fetchOptions, IOptions<SearchOptions> searchOptions, TimeProvider clock)
{
    public bool IsConfigured => provider.IsConfigured;

    public async Task<IReadOnlyList<PageDoc>> SearchAsync(string query, int maxResults, CancellationToken ct)
    {
        var so = searchOptions.Value;
        var flags = new List<string>();
        query = Sanitizer.Normalize(query ?? "", flags).Replace('\n', ' ');
        if (query.Length == 0 || query.Length > so.MaxQueryLength) throw new FetchRefusedException("Invalid query.", "search.invalid_query");

        var count = Math.Clamp(maxResults, 1, fetchOptions.Value.MaxSearchResults);
        var hits = (await provider.SearchAsync(query, count, ct))
            .Where(h => Uri.TryCreate(h.Url, UriKind.Absolute, out _))
            .DistinctBy(h => h.Url)
            .GroupBy(h => new Uri(h.Url).Host, StringComparer.OrdinalIgnoreCase)
            .SelectMany(g => g.Take(Math.Max(1, so.MaxPerDomain)))   // keeps one outlet from dominating the sources
            .Take(count)
            .ToList();

        using var gate = new SemaphoreSlim(Math.Max(1, so.FetchConcurrency));
        var tasks = hits.Select(async h =>
        {
            await gate.WaitAsync(ct);
            try { return await FetchOrSnippetAsync(h, ct); }
            finally { gate.Release(); }
        });
        return await Task.WhenAll(tasks);
    }

    private async Task<PageDoc> FetchOrSnippetAsync(SearchHit hit, CancellationToken ct)
    {
        using var pageCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        pageCts.CancelAfter(TimeSpan.FromSeconds(Math.Max(1, searchOptions.Value.PerPageTimeoutSeconds)));
        try
        {
            var doc = await fetcher.FetchAsync(hit.Url, pageCts.Token);
            return doc.PublishedAt is null && hit.PublishedAt is not null ? doc with { PublishedAt = hit.PublishedAt } : doc;
        }
        catch (Exception ex) when (ex is FetchRefusedException or HttpRequestException
                                   || (ex is OperationCanceledException && !ct.IsCancellationRequested))
        {
            var code = ex is FetchRefusedException fr ? fr.Code : ex is OperationCanceledException ? "fetch.timeout" : "fetch.failed";
            return Snippet(hit, code);
        }
    }

    private PageDoc Snippet(SearchHit hit, string reason)
    {
        var uri = new Uri(hit.Url);
        var clean = sanitizer.Sanitize($"{hit.Title}\n{hit.Description}".Trim(), "text/plain");
        var flags = clean.Info.Flags.Append("snippet_only").Distinct().ToList();
        var title = hit.Title is null ? null : Sanitizer.Normalize(hit.Title, []);
        var checks = new List<Check>
        {
            new("search.snippet_only", CheckStatus.Warn, $"page could not be fetched ({reason}); using the search snippet instead"),
        };
        checks.AddRange(clean.Checks);
        return new PageDoc(uri.ToString(), uri.Host, title, hit.PublishedAt, clean.Text, clock.GetUtcNow(),
            clean.Info with { Flags = flags }, checks);
    }
}
