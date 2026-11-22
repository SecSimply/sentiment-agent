namespace FetchMcp.Search;

public sealed class SearchOptions
{
    /// <summary>Brave Search API key. Set via BRAVE_API_KEY (or Search__ApiKey). Empty = search disabled.</summary>
    public string ApiKey { get; set; } = "";
    public string Endpoint { get; set; } = "https://api.search.brave.com/res/v1/news/search";
    public int MaxQueryLength { get; set; } = 300;
    public int FetchConcurrency { get; set; } = 3;
}
