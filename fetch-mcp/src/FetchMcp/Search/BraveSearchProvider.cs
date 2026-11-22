using System.Text.Json;
using Microsoft.Extensions.Options;

namespace FetchMcp.Search;

public sealed class BraveSearchProvider(HttpClient http, IOptions<SearchOptions> options) : ISearchProvider
{
    public bool IsConfigured => !string.IsNullOrWhiteSpace(options.Value.ApiKey);

    public async Task<IReadOnlyList<SearchHit>> SearchAsync(string query, int count, CancellationToken ct)
    {
        var o = options.Value;
        var url = $"{o.Endpoint}?q={Uri.EscapeDataString(query)}&count={count}";
        using var req = new HttpRequestMessage(HttpMethod.Get, url);
        req.Headers.Add("X-Subscription-Token", o.ApiKey);
        req.Headers.Accept.ParseAdd("application/json");

        using var resp = await http.SendAsync(req, ct);
        resp.EnsureSuccessStatusCode();

        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);

        var hits = new List<SearchHit>();
        if (doc.RootElement.TryGetProperty("results", out var results) && results.ValueKind == JsonValueKind.Array)
        {
            foreach (var r in results.EnumerateArray())
            {
                var link = Str(r, "url");
                if (link is null) continue;
                hits.Add(new SearchHit(link, Str(r, "title"), Str(r, "description"), Str(r, "page_age") ?? Str(r, "age")));
            }
        }
        return hits;
    }

    private static string? Str(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
}
