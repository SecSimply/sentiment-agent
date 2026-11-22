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

    // ---- Injection scan -------------------------------------------------------------------------------------
    // Regexes are a tripwire, not a wall: they catch common phrasings so the analyst is warned and the trace can
    // say what matched. Each pattern is deliberately narrow (anchored to imperative or addressee phrasing) because
    // a pattern that fires on ordinary prose is worse than no pattern; PatternCoverageTests holds the line on both
    // sides (known attacks flagged, benign corpus untouched, held-out attacks reported).
    private const RegexOptions Rx = RegexOptions.IgnoreCase | RegexOptions.Multiline | RegexOptions.Compiled | RegexOptions.CultureInvariant;
    private static readonly TimeSpan PatternTimeout = TimeSpan.FromMilliseconds(250);   // fail closed on pathological input

    private const string Label = @"(?:positive|negative|neutral|mixed|favou?rable|unfavou?rable)";
    private const string Start = @"(?:^|[.!?:;]\s+|\b(?:please|just|now|then|always)\s+)";   // imperative position

    public sealed record InjectionPattern(string Id, Regex Pattern, int MinMatches = 1);
    private static InjectionPattern P(string id, string pattern, int min = 1) => new(id, new Regex(pattern, Rx, PatternTimeout), min);

    public static readonly InjectionPattern[] InjectionPatterns =
    [
        P("override_instruction",
            @"\b(?:ignore|disregard|forget|override|bypass|discard|skip|set\s+aside|stop\s+following|(?:do\s+not|don't)\s+follow)\s+(?:(?:all|any|every|your|the|these|those|my|of|whatever)\s+)*(?:(?:previous|prior|above|earlier|preceding|original|system|developer|existing|former)\s+)+(?:instructions?|prompts?|rules|guidelines|directions|commands|messages?|context)\b"
            + @"|\bdisregard\s+(?:the\s+)?(?:system|previous)\b"
            + @"|\b(?:ignore|disregard|forget)\s+(?:the\s+|everything\s+|all\s+(?:of\s+)?the\s+)?above\b"
            + @"|\bset\s+aside\b[^.\n]{0,40}\b(?:guidance|instructions|rules|guidelines)\b"
            + @"|\bforget\s+(?:everything|all)\s+(?:you\s+(?:were|have\s+been)\s+(?:told|given)|above|before)\b"),
        P("multilingual_override",
            @"忽略.{0,8}(?:指令|指示|提示|规则)|无视.{0,8}(?:指令|指示|提示)"
            + @"|\bignora(?:r)?\s+(?:todas\s+)?(?:las\s+)?(?:instrucciones|indicaciones)\s+(?:anteriores|previas)"
            + @"|\bignore[zr]?\s+(?:toutes\s+)?(?:les\s+)?instructions\s+(?:précédentes|precedentes|antérieures|anterieures)"
            + @"|\bignorier\w*\s+(?:sie\s+)?(?:alle\s+)?(?:vorherigen|bisherigen|obigen|vorigen|früheren?)\s+(?:anweisungen|instruktionen)"
            + @"|\bignore\s+(?:todas\s+)?as\s+instruções\s+anteriores"
            + @"|игнорируй\w*\s+(?:все\s+)?(?:предыдущие|прежние)\s+инструкции"
            + @"|\bignoreer\s+(?:alle\s+)?(?:vorige|voorafgaande)\s+(?:instruksies|opdragte)"),
        P("persona_switch",
            @"\byou are now\b|\b(?:developer|debug|god|jailbreak|dan|admin)\s+mode\b|\b(?:from now on|from this point forward|from here on),?\s+you\s+(?:will|must|shall|are)\s+(?:only\s+|always\s+|now\s+)?(?:behave|act|respond|answer|reply|write|output|classify|rate|label|an?\s)|\bpretend (?:to be|you are|that you)\b|\bact as if (?:the |this |that )?(?:review|document|text|article|above|you)\b"),
        P("new_instructions", @"\bnew instructions\b|\bnew instruction\s*[:\-]|\byour\s+(?:updated|new|revised|real|actual|only|true)\s+(?:instructions?|task)\b|\bnew\s+system\s+instructions?\b"),
        P("task_hijack", @"\bnew task\s*[:\-]|\bthe task has changed\b|\binstead of (?:the )?(?:sentiment|analy[sz]|summar|classif)"),
        P("system_prompt_reference", @"system prompt"),
        P("chat_template_token", @"<\|?(?:system|assistant|im_start|im_end|endoftext)|\[/?INST\]|<<\s*/?sys\s*>>"),
        P("fake_role_header", @"^\s*(?:#{2,}|\[|<<|\*\*)\s*(?:system|assistant|developer)\b"),
        P("fake_delimiter", @"</?\s*(?:documents?|system|assistant|instructions?|prompt)\s*>"),
        P("role_line", @"(?:^|[.!?]\s+)\s*(?:system|assistant)\s*:"),
        P("addresses_ai", @"\b(?:attention|hey|hello|dear)\s*,?\s+(?:ai|llm|chatbot|chatgpt|gpt|language model|ai assistant)\b|\bif you(?:'re| are) an? (?:ai|llm|language model|chatbot|ai assistant)\b"),
        P("forced_label",
            Start + @"(?:output|respond(?:\s+with)?|answer(?:\s+with)?|reply(?:\s+with)?|return|say|write|print)\s+(?:(?:only|exactly|just|with|the\s+(?:word|label|answer)|label|sentiment|rating)\s+|['""“«]\s*)*" + Label + @"\b"
            + "|" + Start + @"(?:set|mark|label|rate|classify|score|record|treat|report|tag)\s+(?:(?:it|this|that|these|them|everything|(?:the|this|that)\s+\w+(?:\s+\w+)?)\s+)?(?:as\s+|to\s+|=\s*)?" + Label + @"\b"
            + @"|\b(?:set|override|change|update)\s*:?\s+(?:the\s+)?(?:label|score|sentiment|confidence|rating)\s*(?:to|=>|->|=|:)\s*(?:-?\d|" + Label + ")"
            + @"|\bin one word\s*[:\-]?\s*" + Label
            + @"|\b(?:correct|right|expected|only valid)\s+(?:answer|label|output|response)\b[^.\n]{0,30}\b(?:is|=|:)\s*['""“]?" + Label),
        P("forged_output",
            @"\{\s*""(?:label|sentiment)""\s*:|""injection_suspected""\s*:"
            + @"|\b(?:rating|label|sentiment|verdict|classification)\s*[:=]\s*" + Label + @"\b[^\n]{0,60}?(?:reason|rationale|confidence|score|end of analysis|final)"),
        P("many_shot_labels", @"\b(?:label|sentiment|rating)\s*[:=]\s*" + Label + @"\b", min: 3),
        P("authority_claim",
            @"\b(?:developers?|administrators?|operators?|maintainers?|compliance team|owners?)\s+(?:of|for|behind)\s+this\s+(?:pipeline|model|analy\w+|classifier|sentiment\w*|assistant)\b"
            + @"|\bpre-?verified\b[^.\n]{0,40}\b(?:positive|negative|neutral|mixed|sentiment|rating|label)\b"),
        P("automation_directive",
            @"\b(?:automated|ai|llm|language models?|analy[sz]ers?|classifiers?|summari[sz]ers?|bots?|assistants?|models?)\b[^.\n]{0,40}\b(?:should|must|shall|are to|need to|have to)\b[^.\n]{0,40}\b(?:record|rate|label|classify|score|mark|output|report|treat|regard)\b[^.\n]{0,40}\b" + Label + @"\b"),
        P("tool_invocation", @"\b(?:fetch_page|search_news)\b"),
    ];

    private static readonly Dictionary<char, char> Fold = new()
    {
        ['0'] = 'o', ['1'] = 'i', ['3'] = 'e', ['4'] = 'a', ['5'] = 's', ['7'] = 't', ['@'] = 'a', ['$'] = 's', ['!'] = 'i',
        // Cyrillic / Greek letters that look like Latin ones
        ['а'] = 'a', ['е'] = 'e', ['о'] = 'o', ['р'] = 'p', ['с'] = 'c', ['у'] = 'y', ['х'] = 'x', ['і'] = 'i', ['ѕ'] = 's', ['ј'] = 'j',
        ['һ'] = 'h', ['ο'] = 'o', ['ι'] = 'i', ['α'] = 'a', ['ε'] = 'e', ['ν'] = 'v', ['ρ'] = 'p', ['τ'] = 't',
    };

    [GeneratedRegex(@"(?<=\b\p{L})[.\-_*](?=\p{L}\b)")] private static partial Regex DottedLetters();
    [GeneratedRegex(@"\b(?:\p{L} ){3,}\p{L}\b")] private static partial Regex SpacedLetters();
    [GeneratedRegex(@"(?<=\p{L})[._\-*]+(?=\p{L})")] private static partial Regex WordSeparators();

    /// <summary>A second view of the text with common obfuscation undone (leetspeak, homoglyphs, i.g.n.o.r.e, i g n o r e).</summary>
    public static string Deobfuscate(string s)
    {
        var sb = new StringBuilder(s.Length);
        foreach (var ch in s) sb.Append(Fold.TryGetValue(char.ToLowerInvariant(ch), out var f) ? f : ch);
        var t = DottedLetters().Replace(sb.ToString(), "");
        t = SpacedLetters().Replace(t, m => m.Value.Replace(" ", ""));
        return WordSeparators().Replace(t, " ");   // ignore.previous.instructions -> ignore previous instructions
    }

    /// <summary>IDs of every pattern that matched. Matches found only after de-obfuscation are prefixed "obfuscated:".</summary>
    public static IReadOnlyList<string> ScanForInjection(string text)
    {
        var found = new List<string>();
        var deob = Deobfuscate(text);
        var views = deob == text ? new[] { (text, "") } : new[] { (text, ""), (deob, "obfuscated:") };
        foreach (var p in InjectionPatterns)
        {
            foreach (var (view, prefix) in views)
            {
                try
                {
                    if (p.MinMatches <= 1 ? p.Pattern.IsMatch(view) : p.Pattern.Matches(view).Count >= p.MinMatches) { found.Add(prefix + p.Id); break; }
                }
                catch (RegexMatchTimeoutException) { found.Add("scan_timeout"); break; }   // fail closed: treat as suspicious
            }
        }
        return found.Distinct().ToList();
    }

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

        var matched = ScanForInjection(text);
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
