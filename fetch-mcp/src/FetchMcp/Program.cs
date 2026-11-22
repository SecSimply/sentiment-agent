using System.Security.Cryptography;
using System.Text;
using FetchMcp;
using FetchMcp.Net;
using FetchMcp.Search;
using FetchMcp.Sanitize;

var builder = WebApplication.CreateBuilder(args);

// Railway: bind dual-stack so the IPv6 private network (*.railway.internal) can reach us.
if (Environment.GetEnvironmentVariable("PORT") is { Length: > 0 } port)
    builder.WebHost.UseUrls($"http://[::]:{port}");

builder.Services.Configure<FetchOptions>(builder.Configuration.GetSection("Fetch"));
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton<Sanitizer>();
builder.Services.AddTransient<IPageFetcher, PageFetcher>();

builder.Services.Configure<SearchOptions>(o =>
{
    builder.Configuration.GetSection("Search").Bind(o);
    if (builder.Configuration["BRAVE_API_KEY"] is { Length: > 0 } key) o.ApiKey = key;
});
// Fixed, trusted API host: normal handler (result URLs are fetched via the SSRF-guarded "safe" client instead).
builder.Services.AddHttpClient<ISearchProvider, BraveSearchProvider>(c =>
{
    c.Timeout = TimeSpan.FromSeconds(10);
    c.MaxResponseContentBufferSize = 1024 * 1024;
});
builder.Services.AddTransient<NewsSearchService>();
builder.Services.AddHttpClient(PageFetcher.ClientName)
    .ConfigurePrimaryHttpMessageHandler(() => SafeHttp.CreateHandler());

builder.Services.AddMcpServer()
    .WithHttpTransport(o => o.Stateless = true)
    .WithToolsFromAssembly();

var secret = builder.Configuration["MCP_SHARED_SECRET"];
if (string.IsNullOrEmpty(secret) && !builder.Environment.IsDevelopment())
    throw new InvalidOperationException("MCP_SHARED_SECRET must be set.");
var secretBytes = Encoding.UTF8.GetBytes(secret ?? "");

var app = builder.Build();

app.MapGet("/healthz", () => Results.Ok("ok"));

app.Use(async (ctx, next) =>
{
    if (ctx.Request.Path.StartsWithSegments("/healthz")) { await next(); return; }

    var header = ctx.Request.Headers.Authorization.ToString();
    var ok = secretBytes.Length > 0
        && header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase)
        && CryptographicOperations.FixedTimeEquals(
            SHA256.HashData(Encoding.UTF8.GetBytes(header[7..])),
            SHA256.HashData(secretBytes));
    if (!ok) { ctx.Response.StatusCode = StatusCodes.Status401Unauthorized; return; }
    await next();
});

// Authenticated plain-HTTP helper for the injection lab. Pure function (no network), and deliberately NOT an MCP
// tool so the model can never call it.
app.MapPost("/lab/sanitize", [Microsoft.AspNetCore.Mvc.RequestSizeLimit(300_000)] (LabSanitizeRequest req, Sanitizer sanitizer) =>
{
    if (string.IsNullOrEmpty(req.Html) || req.Html.Length > 200_000) return Results.BadRequest(new { error = "html must be 1..200000 characters" });
    var r = sanitizer.Sanitize(req.Html, "text/html");
    return Results.Ok(new LabSanitizeResponse(r.Title, r.PublishedAt, r.Text, r.Info, r.Checks));
});

app.MapMcp("/mcp");

app.Run();

public sealed record LabSanitizeRequest([property: System.Text.Json.Serialization.JsonPropertyName("html")] string Html);
public sealed record LabSanitizeResponse(
    [property: System.Text.Json.Serialization.JsonPropertyName("title")] string? Title,
    [property: System.Text.Json.Serialization.JsonPropertyName("published_at")] string? PublishedAt,
    [property: System.Text.Json.Serialization.JsonPropertyName("text")] string Text,
    [property: System.Text.Json.Serialization.JsonPropertyName("sanitization")] FetchMcp.Models.SanitizationInfo Sanitization,
    [property: System.Text.Json.Serialization.JsonPropertyName("checks")] IReadOnlyList<FetchMcp.Models.Check> Checks);

public partial class Program;
