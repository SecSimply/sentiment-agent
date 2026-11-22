using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using AngleSharp.Dom;
using AngleSharp.Html.Parser;
using FetchMcp.Models;
using Microsoft.Extensions.Options;

namespace FetchMcp.Sanitize;

public sealed record SanitizedContent(string? Title, string? PublishedAt, string Text, SanitizationInfo Info);

public sealed partial class Sanitizer(IOptions<FetchOptions> options)
{
    private static readonly string[] DroppedTags =
        ["script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "canvas", "form", "nav", "footer", "aside"];

    [GeneratedRegex(@"ignore (all |any )?(previous|prior|above) (instructions|prompts?)|disregard (the )?(system|previous)|you are now|new instructions|system prompt|<\|?(system|assistant|im_start)|\[/?INST\]|(?m)^\s*(system|assistant)\s*:", RegexOptions.IgnoreCase)]
    private static partial Regex InjectionPattern();

    [GeneratedRegex(@"[ \t\f\v]+")] private static partial Regex Spaces();
    [GeneratedRegex(@"\n{3,}")] private static partial Regex BlankLines();

    public SanitizedContent Sanitize(string body, string contentType)
    {
        var flags = new List<string>();
        string? title = null, published = null;
        string text;

        if (contentType.Contains("html", StringComparison.OrdinalIgnoreCase))
            (title, published, text) = ExtractHtml(body, flags);
        else
            text = body;

        text = Normalize(text, flags);

        if (InjectionPattern().IsMatch(text)) flags.Add("possible_injection");

        var max = options.Value.MaxChars;
        var truncated = text.Length > max;
        if (truncated) text = text[..max];

        return new SanitizedContent(
            title is null ? null : Normalize(title, flags)[..Math.Min(300, Normalize(title, flags).Length)],
            published,
            text,
            new SanitizationInfo(truncated, flags.Distinct().ToList()));
    }

    private static (string? Title, string? Published, string Text) ExtractHtml(string html, List<string> flags)
    {
        var doc = new HtmlParser().ParseDocument(html);

        var published = doc.QuerySelector("meta[property='article:published_time']")?.GetAttribute("content")
                        ?? doc.QuerySelector("time[datetime]")?.GetAttribute("datetime");
        var title = doc.Title;

        foreach (var el in doc.QuerySelectorAll(string.Join(",", DroppedTags)).ToList()) el.Remove();

        var hidden = 0;
        foreach (var el in doc.QuerySelectorAll("[hidden],[aria-hidden='true'],[style]").ToList())
        {
            var style = el.GetAttribute("style")?.Replace(" ", "").ToLowerInvariant() ?? "";
            if (el.HasAttribute("hidden") || el.GetAttribute("aria-hidden") == "true"
                || style.Contains("display:none") || style.Contains("visibility:hidden")
                || style.Contains("font-size:0") || style.Contains("opacity:0"))
            {
                el.Remove();
                hidden++;
            }
        }
        if (hidden > 0) flags.Add("hidden_content_removed");

        IElement? root = doc.QuerySelector("article") ?? doc.QuerySelector("main") ?? doc.Body;
        // Insert newlines around block elements so paragraphs survive TextContent.
        foreach (var el in root?.QuerySelectorAll("p,div,br,li,h1,h2,h3,h4,h5,h6,tr,blockquote").ToList() ?? [])
            el.AppendChild(doc.CreateTextNode("\n"));

        return (title, published, root?.TextContent ?? "");
    }

    public static string Normalize(string input, List<string> flags)
    {
        var s = input.Normalize(NormalizationForm.FormKC);
        var sb = new StringBuilder(s.Length);
        var stripped = false;
        foreach (var rune in s.EnumerateRunes())
        {
            var cat = Rune.GetUnicodeCategory(rune);
            // Cf = format (zero-width, bidi overrides); Co/Cs/Cn = private/surrogate/unassigned.
            if (cat is UnicodeCategory.Format or UnicodeCategory.PrivateUse or UnicodeCategory.OtherNotAssigned
                || (cat == UnicodeCategory.Control && rune.Value is not ('\n' or '\t')))
            {
                stripped = true;
                continue;
            }
            sb.Append(rune.ToString());
        }
        if (stripped) flags.Add("control_chars_removed");

        var t = sb.ToString().Replace("\r", "");
        t = Spaces().Replace(t, " ");
        t = Regex.Replace(t, @" ?\n ?", "\n");
        t = BlankLines().Replace(t, "\n\n");
        return t.Trim();
    }
}
