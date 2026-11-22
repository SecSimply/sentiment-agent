using System.Text.Json.Serialization;

namespace FetchMcp.Models;

public static class CheckStatus
{
    public const string Pass = "pass", Warn = "warn", Block = "block";
}

/// <summary>One defensive check that ran. Server-generated, ID + counts only: never echoes page content.</summary>
public sealed record Check(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("detail")] string Detail);

public sealed record SanitizationInfo(
    [property: JsonPropertyName("truncated")] bool Truncated,
    [property: JsonPropertyName("flags")] IReadOnlyList<string> Flags);

/// <summary>The only shape the LLM ever sees as content. Plain text + metadata, never raw HTML.</summary>
public sealed record PageDoc(
    [property: JsonPropertyName("source_url")] string SourceUrl,
    [property: JsonPropertyName("domain")] string Domain,
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("published_at")] string? PublishedAt,
    [property: JsonPropertyName("text")] string Text,
    [property: JsonPropertyName("fetched_at")] DateTimeOffset FetchedAt,
    [property: JsonPropertyName("sanitization")] SanitizationInfo Sanitization,
    [property: JsonPropertyName("checks")] IReadOnlyList<Check>? Checks = null);
