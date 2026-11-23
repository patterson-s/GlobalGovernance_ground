#!/usr/bin/env python3
"""unground — grounding layer for global-governance text.

    python3 unground.py scan data/sample/charter.txt --out out/charter
    python3 unground.py scan FILE --triage

Emits: <out>.mentions.jsonl, <out>.mentions.csv, <out>.html
Deterministic only: no API calls. Ambiguity and bare definite NPs go to a
review queue rather than being guessed.
"""
import argparse, csv, html, json, os, re, sys, unicodedata
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
GAZ = os.path.join(HERE, "data", "gazetteer.json")

# nouns that behave as document-local referring expressions
HEAD_NOUNS = ["Council", "Commission", "Committee", "Assembly", "Office", "Fund",
              "Programme", "Program", "Agency", "Board", "Secretariat", "Department",
              "Court", "Tribunal", "Forum", "Panel", "Unit", "Division", "Centre",
              "Center", "Organization", "Organisation", "Bank", "Union", "Conference",
              "Group", "Mission", "Force", "Bureau", "Authority", "Institute"]

CAP_REQUIRED = {"georgia", "jordan", "chad", "turkey", "niger", "mali", "oman",
                "dominica", "guinea", "congo", "china", "india", "israel", "egypt"}

BLOCK_COLOR = {
    "Principal Organs": "#c0392b", "Subsidiary Organs": "#d35400",
    "Funds and Programmes": "#8e44ad", "Research and Training": "#16a085",
    "Secretariats of Conventions": "#2c7a7b", "Other Entities and Bodies": "#7f8c8d",
    "Specialized Agencies": "#2471a3", "Related Organizations": "#5d6d7e",
    "Departments and Offices": "#b7950b", "Economic and Social Council Bodies": "#a04000",
    "Regional Commissions": "#1e8449", "Member States": "#6c3483",
    "UN System Bodies": "#566573", "Instruments": "#922b21",
    "UN System": "#1f4e79",
}


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("\u2019", "'").replace("\u2018", "'").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load_gazetteer(path=GAZ):
    g = json.load(open(path))
    alias = g["alias_index"]                      # key -> [entity ids]
    alias_type = {}
    for eid, e in g["entities"].items():
        if e.get("acronym"):
            alias_type[(norm(e["acronym"]), eid)] = "acronym"
        alias_type[(norm(e["canonical"]), eid)] = "canonical"
        for a in e.get("aliases", []):
            alias_type.setdefault((norm(a), eid), "variant")
        for names in (e.get("multilingual") or {}).values():
            for n in (names if isinstance(names, list) else [names]):
                alias_type.setdefault((norm(n), eid), "translation")
    return g, alias, alias_type


def build_pattern(alias_keys):
    """One alternation, longest first, so the leftmost-longest alias wins."""
    parts = []
    for key in sorted(alias_keys, key=len, reverse=True):
        if len(key) <= 6 and " " not in key and key.isalpha():
            # acronym: tolerate U.N. / U N / UN
            p = r"\s*\.?\s*".join(re.escape(c) for c in key)
        else:
            p = r"\s+".join(re.escape(t) for t in key.split())
        parts.append(p)
    return re.compile(r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])", re.IGNORECASE)


def paragraphs(text):
    out, pos = [], 0
    for chunk in re.split(r"(\n\s*\n)", text):
        if chunk.strip():
            out.append((pos, chunk))
        pos += len(chunk)
    return out


def head_noun(canonical):
    """The referring-expression head of a canonical name: the LAST organizational
    noun in it. 'International Court of Justice' -> 'court'; 'World Food Programme'
    -> 'programme'. Suffix-only matching misses every 'X of Y' name."""
    best, pos = None, -1
    for h in HEAD_NOUNS:
        for m in re.finditer(r"\b" + h + r"s?\b", canonical, re.IGNORECASE):
            if m.start() > pos:
                best, pos = h.lower(), m.start()
    return best


def scan(text, g, alias, alias_type, doc_id="doc"):
    entities = g["entities"]
    pat = build_pattern(alias.keys())
    mentions = []

    for para_idx, (pstart, para) in enumerate(paragraphs(text)):
        for m in pat.finditer(para):
            surface = m.group(0)
            key = norm(surface)
            cands = alias.get(key)
            if not cands:
                continue
            if key in CAP_REQUIRED and not surface[:1].isupper():
                continue
            ids = cands
            atype = alias_type.get((key, ids[0]), "variant") if len(ids) == 1 else None
            # An acronym alias must be written as one. Otherwise "who" (the pronoun)
            # resolves to WHO (the agency) — the worst kind of error: confident and wrong.
            if atype == "acronym":
                if sum(1 for c in surface if c.isupper()) < 2 and "." not in surface:
                    continue
            if len(ids) > 1:
                conf, status, decided = 0.45, "ambiguous", "dict"
            else:
                conf = {"canonical": 0.95, "acronym": 0.90,
                        "variant": 0.85, "translation": 0.80}.get(atype, 0.85)
                status, decided = "auto", "dict"
            mentions.append({
                "doc_id": doc_id, "para": para_idx,
                "start": pstart + m.start(), "end": pstart + m.end(),
                "surface": surface, "entity_ids": list(ids),
                "confidence": conf, "status": status, "decided_by": decided,
            })

    # --- abbreviation definitions: "Full Name (ACR)" binds for the document ---
    doc_bind = {}
    for m in mentions:
        if len(m["entity_ids"]) != 1:
            continue
        tail = text[m["end"]:m["end"] + 14]
        mm = re.match(r"\s*\(([A-Za-z][A-Za-z0-9\-\. ]{1,12})\)", tail)
        if mm:
            doc_bind[norm(mm.group(1))] = m["entity_ids"][0]
            m["defines"] = mm.group(1)

    # --- document-local bindings by head noun -------------------------------
    para_bind = defaultdict(dict)
    for m in mentions:
        if len(m["entity_ids"]) != 1:
            continue
        e = entities[m["entity_ids"][0]]
        h = head_noun(e["canonical"])
        if h:
            para_bind[m["para"]][h] = m["entity_ids"][0]
            doc_bind.setdefault("__head__:" + h, m["entity_ids"][0])

    # --- bare definite NPs: referring expressions, not aliases --------------
    occupied = [(m["start"], m["end"]) for m in mentions]
    head_re = re.compile(r"\b(?:[Tt]he|[Tt]his|[Tt]hat)\s+(" +
                         "|".join(HEAD_NOUNS) + r")\b")
    review = []
    for para_idx, (pstart, para) in enumerate(paragraphs(text)):
        for m in head_re.finditer(para):
            s, e = pstart + m.start(), pstart + m.end()
            if any(s < oe and os_ < e for os_, oe in occupied):
                continue
            head = m.group(1).lower()
            bound = para_bind[para_idx].get(head) or doc_bind.get("__head__:" + head)
            if bound:
                mentions.append({
                    "doc_id": doc_id, "para": para_idx, "start": s, "end": e,
                    "surface": m.group(0), "entity_ids": [bound],
                    "confidence": 0.75, "status": "auto", "decided_by": "binding",
                })
            else:
                cands = [eid for eid, en in entities.items()
                         if head_noun(en["canonical"]) == head]
                mentions.append({
                    "doc_id": doc_id, "para": para_idx, "start": s, "end": e,
                    "surface": m.group(0), "entity_ids": cands[:6],
                    "confidence": 0.0,
                    "status": "review" if cands else "review_no_candidate",
                    "decided_by": "bare_np",
                })
    mentions.sort(key=lambda m: m["start"])
    return mentions, doc_bind


# ---------------------------------- output ----------------------------------
def enrich(mentions, entities):
    for m in mentions:
        ids = m["entity_ids"]
        m["n_candidates"] = len(ids)
        if len(ids) == 1:
            e = entities[ids[0]]
            m.update(entity_id=ids[0], canonical=e["canonical"], type=e["type"],
                     block=e["block"], hierarchy=" > ".join(e["hierarchy_path"]),
                     definition=e["definition"][:240])
        else:
            m.update(entity_id="", canonical="", type="", block="",
                     hierarchy="", definition="")
    return mentions


FIELDS = ["doc_id", "para", "start", "end", "surface", "entity_id", "canonical",
          "type", "block", "hierarchy", "confidence", "status", "decided_by",
          "n_candidates"]


def write_outputs(mentions, outbase, text, title):
    os.makedirs(os.path.dirname(outbase) or ".", exist_ok=True)
    with open(outbase + ".mentions.jsonl", "w") as f:
        for m in mentions:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    with open(outbase + ".mentions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for m in mentions:
            row = dict(m)
            row["confidence"] = f"{row['confidence']:.2f}"
            w.writerow(row)

    # ---- render ----
    auto = [m for m in mentions if m["status"] == "auto" and m["entity_id"]]
    amb = [m for m in mentions if m["status"] == "ambiguous"]
    rev = [m for m in mentions if m["status"].startswith("review")]
    by_block = Counter(m["block"] for m in auto)
    by_ent = Counter(m["canonical"] for m in auto)

    spans = []
    for m in sorted((x for x in mentions if x["entity_id"] or x["status"] == "ambiguous"),
                    key=lambda x: x["start"]):
        color = BLOCK_COLOR.get(m["block"], "#7f8c8d") if m["entity_id"] else "#e67e22"
        tip = (f'{m["canonical"]} · {m["type"]} · {m["hierarchy"]}' if m["entity_id"]
               else "UNRESOLVED — " + ", ".join(m["entity_ids"][:4]))
        spans.append((m["start"], f'<mark class="s" style="--c:{color}" title="{html.escape(tip)}">'))
        spans.append((m["end"], "</mark>"))

    body, cursor = [], 0
    for pos, tag in spans:
        if pos > cursor:
            body.append(html.escape(text[cursor:pos]))
        body.append(tag)
        cursor = pos
    body.append(html.escape(text[cursor:]))

    rows = "\n".join(
        f"<tr><td>{m['para']}</td><td><code>{html.escape(m['surface'])}</code></td>"
        f"<td>{html.escape(m['canonical'] or '—')}</td><td>{html.escape(m['type'])}</td>"
        f"<td>{html.escape(m['block'])}</td><td>{m['confidence']:.2f}</td>"
        f"<td>{m['decided_by']}</td></tr>" for m in mentions[:400])
    rev_rows = "\n".join(
        f"<tr><td>{m['para']}</td><td><code>{html.escape(m['surface'])}</code></td>"
        f"<td>{m['status']}</td><td>{html.escape(', '.join(m['entity_ids'][:4]) or 'none')}</td></tr>"
        for m in rev + amb)
    block_rows = "\n".join(
        f'<div class="bl"><span class="dot" style="background:{BLOCK_COLOR.get(b, "#7f8c8d")}"></span>'
        f'{html.escape(b)}<b>{n}</b></div>' for b, n in by_block.most_common())
    ent_rows = "\n".join(f"<div class='en'>{html.escape(c)}<b>{n}</b></div>"
                         for c, n in by_ent.most_common(15))

    doc = f"""<!doctype html><meta charset="utf-8"><title>unground — {html.escape(title)}</title>
<style>
 :root{{--ink:#1a1a1a;--paper:#fbfaf7}}
 body{{font:16px/1.65 Georgia,serif;background:var(--paper);color:var(--ink);margin:0}}
 header{{padding:22px 34px;border-bottom:1px solid #ddd;background:#fff}}
 h1{{font-size:19px;margin:0 0 4px}} .sub{{font:13px system-ui;color:#666}}
 main{{display:grid;grid-template-columns:minmax(0,1fr) 320px}}
 article{{padding:28px 34px;max-width:74ch;white-space:pre-wrap;overflow-wrap:break-word}}
 aside{{border-left:1px solid #e3e0da;padding:22px 18px;font:12px system-ui;background:#fff;overflow-wrap:break-word}}
 aside h2{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#888;margin:18px 0 8px}}
 mark.s{{background:color-mix(in srgb,var(--c) 16%,transparent);border-bottom:2px solid var(--c);
   padding:1px 0;cursor:help}}
 .bl{{display:flex;align-items:center;gap:7px;margin:3px 0;line-height:1.35}} .bl b{{margin-left:auto}}
 .en{{margin:3px 0;color:#444}} .en b{{float:right;color:#999;font-weight:400}}
 .dot{{width:9px;height:9px;border-radius:50%;display:inline-block}}
 table{{border-collapse:collapse;font:12px system-ui;width:100%;margin-top:8px}}
 td,th{{padding:4px 6px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}}
 .kpi{{display:flex;gap:14px;font:12px system-ui}} .kpi div{{background:#f3f1ec;padding:6px 10px;border-radius:4px}}
 details{{margin-top:26px}} summary{{font:13px system-ui;cursor:pointer;color:#555}}
</style>
<header><h1>unground — {html.escape(title)}</h1>
<div class="sub">deterministic pass · no model calls · {len(mentions)} mentions ·
{len(auto)} resolved · {len(amb)} ambiguous · {len(rev)} review</div></header>
<main><article>{''.join(body)}
<details><summary>mention table ({len(mentions)} rows, first 400)</summary>
<table><tr><th>¶</th><th>surface</th><th>canonical</th><th>type</th><th>block</th><th>conf</th><th>decided</th></tr>
{rows}</table></details>
<details open><summary>review queue ({len(rev)+len(amb)})</summary>
<table><tr><th>¶</th><th>surface</th><th>status</th><th>candidates</th></tr>{rev_rows}</table></details>
</article>
<aside>
<h2>coverage</h2>
<div class="kpi"><div>{len(auto)} auto</div><div>{len(amb)} ambig</div><div>{len(rev)} review</div></div>
<h2>by block</h2>{block_rows}
<h2>top entities</h2>{ent_rows}
</aside></main>"""
    with open(outbase + ".html", "w") as f:
        f.write(doc)
    return {"mentions": len(mentions), "auto": len(auto), "ambiguous": len(amb),
            "review": len(rev), "html": outbase + ".html"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--out", default=None)
    ap.add_argument("--triage", action="store_true")
    a = ap.parse_args()
    text = open(a.text, encoding="utf-8").read()
    g, alias, alias_type = load_gazetteer()
    doc_id = os.path.splitext(os.path.basename(a.text))[0]
    ms, _ = scan(text, g, alias, alias_type, doc_id)
    ms = enrich(ms, g["entities"])
    outbase = a.out or os.path.join(HERE, "out", doc_id)
    stats = write_outputs(ms, outbase, text, doc_id)
    print(json.dumps(stats, indent=1))
    if a.triage:
        print("\nreview queue:")
        for m in ms:
            if m["status"].startswith("review") or m["status"] == "ambiguous":
                print(f'  ¶{m["para"]:>3}  {m["surface"]!r:38} {m["status"]:18} '
                      f'{", ".join(m["entity_ids"][:3]) or "—"}')


if __name__ == "__main__":
    main()
