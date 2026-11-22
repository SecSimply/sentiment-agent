using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using AngleSharp.Dom;
using AngleSharp.Html.Parser;
using FetchMcp.Models;
using Microsoft.Extensions.Options;

namespace FetchMcp.Sanitize;

public sealed record SanitizedContent(string? Title, string? PublishedAt, string Text, SanitizationInfo Info, IReadOnlyList<Check> Checks);

public sealed partial class Sanitizer(IOptions<FetchOptions> options)
{
    private static readonly string[] DroppedTags =
        ["script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "canvas", "form", "nav", "footer", "aside"];

    private const RegexOptions Rx = RegexOptions.IgnoreCase | RegexOptions.Compiled;

    /// <summary>Named so the trace can say WHICH pattern matched without echoing page content.</summary>
    public static readonly (string Id, Regex Pattern)[] InjectionPatterns =
    [
        ("override_instruction", new(@"ignore (all |any )?(previous|prior|above) (instructions|prompts?)|disregard (the )?(system|previous)", Rx)),
        ("persona_switch", new(@"you are now", Rx)),
        ("new_instructions", new(@"new instructions", Rx)),
        ("system_prompt_reference", new(@"system prompt", Rx)),
        ("chat_template_token", new(@"<\|?(system|assistant|im_start)|\[/?INST\]", Rx)),
        ("role_line", new(@"^\s*(system|assistant)\s*:", Rx | RegexOptions.Multiline)),
    ];

    [GeneratedRegex(@"[ \t\f\v]+")] private static partial Regex Spaces();
    [GeneratedRegex(@"\n{3,}")] private static partial Regex BlankLines();

    public SanitizedContent Sanitize(string body, string contentType)
    {
        var flags = new List<string>();
        var checks = new List<Check>();
        string? title = null, published = null;
        string text;

        if (contentType.Contains("html", StringComparison.OrdinalIgnoreCase))
            (title, published, text) = ExtractHtml(body, flags, checks);
        else
        {
            text = body;
            checks.Add(new Check("sanitize.extract", CheckStatus.Pass, "plain text; no markup to parse"));
        }

        text = NormalizeCounted(text, out var stripped);
        if (stripped > 0)
        {
            flags.Add("control_chars_removed");
            checks.Add(new Check("sanitize.control_chars", CheckStatus.Warn, $"stripped {stripped} invisible/control character(s) (zero-width, bidi, private-use)"));
        }
        else checks.Add(new Check("sanitize.control_chars", CheckStatus.Pass, "no invisible or control characters found"));

        var matched = InjectionPatterns.Where(p => p.Pattern.IsMatch(text)).Select(p => p.Id).ToList();
        if (matched.Count > 0)
        {
            flags.Add("possible_injection");
            checks.Add(new Check("sanitize.injection_patterns", CheckStatus.Warn, $"instruction-like text matched: {string.Join(", ", matched)}. Flagged for the analyst, content kept"));
        }
        else checks.Add(new Check("sanitize.injection_patterns", CheckStatus.Pass, "no instruction-like patterns matched"));

        var max = options.Value.MaxChars;
        var truncated = text.Length > max;
        if (truncated)
        {
            checks.Add(new Check("sanitize.truncate", CheckStatus.Warn, $"text cut from {text.Length} to {max} characters"));
            text = text[..max];
        }
        else checks.Add(new Check("sanitize.truncate", CheckStatus.Pass, $"{text.Length} characters (limit {max})"));

        var cleanTitle = title is null ? null : Normalize(title, []);
        if (cleanTitle is { Length: > 300 }) cleanTitle = cleanTitle[..300];

        return new SanitizedContent(cleanTitle, published, text,
            new SanitizationInfo(truncated, flags.Distinct().ToList()), checks);
    }

    private static (string? Title, string? Published, string Text) ExtractHtml(string html, List<string> flags, List<Check> checks)
    {
        var doc = new HtmlParser().ParseDocument(html);

        var published = doc.QuerySelector("meta[property='article:published_time']")?.GetAttribute("content")
                        ?? doc.QuerySelector("time[datetime]")?.GetAttribute("datetime");
        var title = doc.Title;

        var dropped = doc.QuerySelectorAll(string.Join(",", DroppedTags)).ToList();
        foreach (var el in dropped) el.Remove();
        checks.Add(new Check("sanitize.extract", CheckStatus.Pass,
            $"HTML reduced to text; dropped {dropped.Count} non-content element(s) (script/style/template/iframe/…); comments, meta and alt text ignored"));

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
        if (hidden > 0)
        {
            flags.Add("hidden_content_removed");
            checks.Add(new Check("sanitize.hidden_content", CheckStatus.Warn, $"removed {hidden} element(s) hidden from human readers"));
        }
        else checks.Add(new Check("sanitize.hidden_content", CheckStatus.Pass, "no hidden elements found"));

        IElement? root = doc.QuerySelector("article") ?? doc.QuerySelector("main") ?? doc.Body;
        // Insert newlines around block elements so paragraphs survive TextContent.
        foreach (var el in root?.QuerySelectorAll("p,div,br,li,h1,h2,h3,h4,h5,h6,tr,blockquote").ToList() ?? [])
            el.AppendChild(doc.CreateTextNode("\n"));

        return (title, published, root?.TextContent ?? "");
    }

    public static string Normalize(string input, List<string> flags)
    {
        var s = NormalizeCounted(input, out var stripped);
        if (stripped > 0) flags.Add("control_chars_removed");
        return s;
    }

    private static string NormalizeCounted(string input, out int stripped)
    {
        var s = input.Normalize(NormalizationForm.FormKC);
        var sb = new StringBuilder(s.Length);
        stripped = 0;
        foreach (var rune in s.EnumerateRunes())
        {
            var cat = Rune.GetUnicodeCategory(rune);
            // Cf = format (zero-width, bidi overrides); Co/Cs/Cn = private/surrogate/unassigned.
            if (cat is UnicodeCategory.Format or UnicodeCategory.PrivateUse or UnicodeCategory.OtherNotAssigned
                || (cat == UnicodeCategory.Control && rune.Value is not ('\n' or '\t')))
            {
                stripped++;
                continue;
            }
            sb.Append(rune.ToString());
        }

        var t = sb.ToString().Replace("\r", "");
        t = Spaces().Replace(t, " ");
        t = Regex.Replace(t, @" ?\n ?", "\n");
        t = BlankLines().Replace(t, "\n\n");
        return t.Trim();
    }
}
