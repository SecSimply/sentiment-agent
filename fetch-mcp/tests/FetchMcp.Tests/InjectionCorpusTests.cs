using FetchMcp.Sanitize;
using Microsoft.Extensions.Options;

namespace FetchMcp.Tests;

/// <summary>Real-world-shaped injection pages run through the actual sanitizer.</summary>
public class InjectionCorpusTests
{
    private static Sanitizer Make() => new(Options.Create(new FetchOptions()));
    private static string Load(string name) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", "injection", name));

    /// <summary>Hidden / non-rendered payloads must be gone from the output entirely.</summary>
    [Theory]
    [InlineData("hidden_display_none.html", "ZZZ-PAYLOAD", "hidden_content_removed")]
    [InlineData("hidden_aria_fontsize_opacity.html", "ZZZ-PAYLOAD", "hidden_content_removed")]
    [InlineData("html_comment.html", "ZZZ-PAYLOAD", null)]
    [InlineData("script_meta_alt_template.html", "ZZZ-PAYLOAD", null)]
    public void Hidden_payload_is_removed(string file, string marker, string? expectedFlag)
    {
        var r = Make().Sanitize(Load(file), "text/html");
        Assert.DoesNotContain(marker, r.Text, StringComparison.Ordinal);
        Assert.DoesNotContain("possible_injection", r.Info.Flags);   // nothing visible left to flag
        Assert.Contains("broke after two days", r.Text);             // real content survives
        if (expectedFlag is not null) Assert.Contains(expectedFlag, r.Info.Flags);
    }

    /// <summary>Visible payloads can't be removed (they look like content) but must be flagged after normalization.</summary>
    [Theory]
    [InlineData("visible_override.html")]
    [InlineData("zero_width_obfuscated.html")]
    [InlineData("fake_role_markers.html")]
    [InlineData("offscreen_text.html")]       // off-screen CSS isn't detected as hidden, so it must be caught by the pattern
    [InlineData("bidi_override.html")]
    [InlineData("fullwidth_homoglyph.html")]
    public void Visible_payload_is_flagged(string file)
    {
        var r = Make().Sanitize(Load(file), "text/html");
        Assert.Contains("possible_injection", r.Info.Flags);
        Assert.Contains("broke after two days", r.Text);
    }

    [Theory]
    [InlineData("zero_width_obfuscated.html")]
    [InlineData("bidi_override.html")]
    public void Invisible_control_characters_are_stripped(string file)
    {
        var r = Make().Sanitize(Load(file), "text/html");
        Assert.Contains("control_chars_removed", r.Info.Flags);
        Assert.DoesNotContain(r.Text, c => c is '​' or '‌' or '‍' or '‮' or '‬');
    }

    /// <summary>False-positive control: ordinary prose with words like "system", "ignore", "instructions".</summary>
    [Fact]
    public void Benign_page_is_not_flagged()
    {
        var r = Make().Sanitize(Load("benign_control.html"), "text/html");
        Assert.Empty(r.Info.Flags);
        Assert.Contains("Harbour Cafe", r.Text);
    }
}
