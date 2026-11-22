using System.Net;
using Microsoft.AspNetCore.Mvc.Testing;

namespace FetchMcp.Tests;

public class AuthTests(WebApplicationFactory<Program> factory) : IClassFixture<WebApplicationFactory<Program>>
{
    private HttpClient Client() => factory.WithWebHostBuilder(b =>
        b.UseSetting("MCP_SHARED_SECRET", "s3cret")).CreateClient();

    [Fact] public async Task Healthz_is_anonymous() =>
        Assert.Equal(HttpStatusCode.OK, (await Client().GetAsync("/healthz")).StatusCode);

    [Fact] public async Task Mcp_requires_bearer() =>
        Assert.Equal(HttpStatusCode.Unauthorized, (await Client().PostAsync("/mcp", new StringContent("{}"))).StatusCode);

    [Fact] public async Task Mcp_rejects_wrong_bearer()
    {
        var c = Client();
        c.DefaultRequestHeaders.Authorization = new("Bearer", "nope");
        Assert.Equal(HttpStatusCode.Unauthorized, (await c.PostAsync("/mcp", new StringContent("{}"))).StatusCode);
    }
}
