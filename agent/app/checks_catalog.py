"""Plain-language descriptions of every check, so the UI can explain what each one defends against."""

CATALOG: dict[str, dict[str, str]] = {
    "request.auth": {"title": "API key accepted", "defends": "Unauthorised use of a public, paid endpoint."},
    "url.valid": {"title": "URL is well-formed", "defends": "Non-web schemes (file://, ftp://), credentials smuggled in URLs, oversized URLs."},
    "url.invalid": {"title": "URL is well-formed", "defends": "Malformed or oversized URLs."},
    "url.scheme": {"title": "Only http(s) allowed", "defends": "file://, ftp:// and other schemes that could read local resources."},
    "url.credentials": {"title": "No credentials in URL", "defends": "user:password@host tricks used to disguise destinations."},
    "url.domain_allowlist": {"title": "Domain allow-list", "defends": "Fetching from sites outside the approved list."},
    "ssrf.dns_ip": {"title": "Destination is a public address", "defends": "SSRF: reaching internal services, cloud metadata or the private network. The IP is vetted at connect time, which also defeats DNS rebinding."},
    "redirect.revalidated": {"title": "Redirect target re-validated", "defends": "Open redirects that bounce a safe URL to an internal one."},
    "redirect.limit": {"title": "Redirect limit", "defends": "Redirect loops and chains used to evade checks."},
    "http.status": {"title": "Upstream status OK", "defends": "Treating error pages as real content."},
    "content.type": {"title": "Content type allowed", "defends": "Binary or active content reaching the parser."},
    "content.size": {"title": "Response size cap", "defends": "Memory exhaustion and cost blow-ups from huge responses."},
    "sanitize.extract": {"title": "Reduced to plain text", "defends": "Scripts, styles, templates, iframes, comments, meta and alt text never reach the model."},
    "sanitize.hidden_content": {"title": "Hidden content removed", "defends": "Instructions hidden from humans (display:none, aria-hidden, zero font size) but readable by a model."},
    "sanitize.control_chars": {"title": "Invisible characters stripped", "defends": "Zero-width, bidi and private-use characters used to smuggle or obfuscate instructions; Unicode is normalised (NFKC) first."},
    "sanitize.injection_patterns": {"title": "Instruction-like text scan", "defends": "Text aimed at the model rather than the reader. Flagged so the analyst is warned; the content is kept, not silently rewritten."},
    "sanitize.truncate": {"title": "Length cap", "defends": "Context flooding and cost."},
    "search.snippet_only": {"title": "Fell back to search snippet", "defends": "A page that could not be fetched safely is replaced by its (sanitised) snippet."},
    "analyst.no_tools": {"title": "Analyst has no tools", "defends": "A poisoned document cannot trigger any action: the model that reads untrusted text has no tools bound."},
    "analyst.untrusted_wrapper": {"title": "Documents wrapped as inert data", "defends": "Content is passed as JSON inside <documents>, with '</' escaped so it cannot close the tag and impersonate the prompt."},
    "analyst.flags_forwarded": {"title": "Sanitizer flags forwarded", "defends": "The model sees which documents looked suspicious and is told to treat them sceptically."},
    "output.schema": {"title": "Output schema validated", "defends": "Free-form text cannot change the shape of the result; the label, score and confidence are type-checked."},
    "output.flag_override": {"title": "Sanitizer evidence overrides the model", "defends": "If the sanitizer saw an injection attempt, the response says so regardless of what the model claimed."},
}


def describe(check_id: str) -> dict[str, str]:
    return CATALOG.get(check_id, {"title": check_id, "defends": ""})
