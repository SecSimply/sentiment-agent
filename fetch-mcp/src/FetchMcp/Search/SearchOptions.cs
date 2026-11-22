namespace FetchMcp.Search;

public sealed class SearchOptions
{
    /// <summary>Brave Search API key. Set via BRAVE_API_KEY (or Search__ApiKey). Empty = search disabled.</summary>
    public string ApiKey { get; set; } = "";
    public string Endpoint { get; set; } = "https://api.search.brave.com/res/v1/news/search";
    public int MaxQueryLength { get; set; } = 300;
    public int FetchConcurrency { get; set; } = 5;
    /// <summary>Slow pages fall back to their search snippet instead of holding up the whole answer.</summary>
    public int PerPageTimeoutSeconds { get; set; } = 6;
    /// <summary>Diversity: at most this many results from one host.</summary>
    public int MaxPerDomain { get; set; } = 2;
}
