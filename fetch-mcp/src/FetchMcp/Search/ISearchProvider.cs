namespace FetchMcp.Search;

public sealed record SearchHit(string Url, string? Title, string? Description, string? PublishedAt);

public interface ISearchProvider
{
    bool IsConfigured { get; }
    Task<IReadOnlyList<SearchHit>> SearchAsync(string query, int count, CancellationToken ct);
}
