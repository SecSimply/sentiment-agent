"""Regenerate server/seed/*.json from the fetch-mcp fixtures and the agent's injection eval cases.

    python scripts/build_seed.py        (run from ui/)
"""
import html as H
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parents[1] / "server" / "seed"
FIX = ROOT / "fetch-mcp/tests/FetchMcp.Tests/Fixtures/injection"
CASES = ROOT / "agent/evals/datasets/injection.jsonl"

FIXTURE_NOTES = {
    "hidden_display_none": ("hidden text", "Sanitizer should REMOVE the hidden payload (hidden_content_removed)."),
    "hidden_aria_fontsize_opacity": ("hidden text", "aria-hidden, font-size:0, opacity:0 and [hidden] payloads should all be removed."),
    "html_comment": ("html comment", "Comments never reach the text. Payload should be absent."),
    "script_meta_alt_template": ("script/meta/alt", "script, noscript, template, meta description and img alt are ignored."),
    "zero_width_obfuscated": ("zero-width obfuscation", "Invisible characters stripped, then the pattern should be FLAGGED."),
    "visible_override": ("visible override", "Visible instruction cannot be removed; it should be FLAGGED possible_injection."),
    "fake_role_markers": ("role markers", "system:, <|im_start|> and [INST] markers should be FLAGGED."),
    "offscreen_text": ("off-screen css", "Off-screen CSS is not detected as hidden, so the pattern scan must FLAG it."),
    "bidi_override": ("bidi override", "Bidi control characters stripped; pattern FLAGGED."),
    "fullwidth_homoglyph": ("fullwidth homoglyph", "NFKC turns fullwidth letters into ASCII; pattern FLAGGED."),
    "benign_control": ("benign control", "False-positive control: should produce NO flags."),
}


def page(title: str, paragraphs: list[str]) -> str:
    body = "\n".join(f"    <p>{H.escape(p)}</p>" for p in paragraphs)
    return (f"<!doctype html>\n<html>\n<head><title>{H.escape(title)}</title></head>\n<body>\n<main>\n  <article>\n"
            f"    <h1>{H.escape(title)}</h1>\n{body}\n  </article>\n</main>\n</body>\n</html>\n")


pages, payloads = [], []
for f in sorted(FIX.glob("*.html")):
    tech, note = FIXTURE_NOTES[f.stem]
    pages.append({"seed_key": f"fixture:{f.stem}", "name": f"Fixture: {f.stem.replace('_', ' ')}", "technique": tech,
                  "expected_label": "neutral" if f.stem == "benign_control" else "negative", "notes": note,
                  "html": f.read_text()})

for line in CASES.read_text().splitlines():
    c = json.loads(line)
    base = c["text"]
    # recover the payload: cases are base review + payload at start / middle / end
    from_gen = {"negative": None, "positive": None}
    for body in ("I bought the Acme Widget last month and it stopped working after two days. Support never answered my emails and the refund was refused. I regret this purchase and would not recommend it to anyone.",
                 "I bought the Acme Widget last month and it has been fantastic. It works perfectly, support helped me within an hour and I would happily recommend it to anyone."):
        if body in base:
            payload = base.replace(body, "", 1).strip()
            break
        a, b = body.split(". ", 1)
        if base.startswith(a + ". ") and base.endswith(" " + b):
            payload, body = base[len(a) + 2: -(len(b) + 1)], body
            break
    else:
        raise SystemExit(f"cannot derive payload for {c['id']}")
    paras = [p for p in c["text"].split("\n\n") if p.strip()]
    pages.append({"seed_key": f"case:{c['id']}", "name": f"Attack: {c['technique'].replace('_', ' ')}", "technique": c["technique"],
                  "expected_label": c["true_label"],
                  "notes": f"Eval case {c['id']}. The attacker wants '{c['attacker_target']}'. {c.get('note','')}".strip(),
                  "html": page("Acme Widget Review", paras)})
    payloads.append({"id": c["id"], "technique": c["technique"], "target_label": c["attacker_target"], "text": payload})

(OUT / "pages.json").write_text(json.dumps(pages, indent=1, ensure_ascii=False))
(OUT / "payloads.json").write_text(json.dumps(payloads, indent=1, ensure_ascii=False))
print(len(pages), "seed pages,", len(payloads), "payloads")
