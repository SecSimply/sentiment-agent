namespace FetchMcp;

public sealed class FetchOptions
{
    public int MaxBytes { get; set; } = 2 * 1024 * 1024;
    public int MaxChars { get; set; } = 20_000;
    public int MaxRedirects { get; set; } = 3;
    public int TimeoutSeconds { get; set; } = 10;
    public int MaxUrlLength { get; set; } = 2048;
    public int MaxSearchResults { get; set; } = 10;
    /// <summary>Empty = any public host. Otherwise host must equal or be a subdomain of an entry.</summary>
    public string[] AllowedDomains { get; set; } = [];
}
