#!/usr/bin/env python3
"""Build the grounding gazetteer: orgchart spine + UN external reference + curated extras.

Output: data/gazetteer.json  {meta, entities, alias_index}
Alias keys are normalized (casefold, accent-fold, punctuation-stripped, whitespace-collapsed).
"""
import json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE = os.path.expanduser("~/obsidian-vault/projects/unterminology")
ORGCHART = f"{SPIKE}/orgchart/orgchart.json"
REFERENCE = f"{SPIKE}/reference/records.jsonl"
OUT = f"{HERE}/data/gazetteer.json"


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Country names that are also ordinary words/names: require a capital first letter.
CAP_REQUIRED = {"georgia", "jordan", "chad", "turkey", "niger", "mali", "oman",
                "dominica", "guinea", "congo", "china", "india", "israel", "egypt"}

BLOCK_TO_TYPE = {
    "Principal Organs": "ORGAN",
    "Subsidiary Organs": "SUBSIDIARY_BODY",
    "Funds and Programmes": "PROGRAMME_FUND",
    "Research and Training": "RESEARCH_TRAINING",
    "Secretariats of Conventions": "CONVENTION_SECRETARIAT",
    "Other Entities and Bodies": "ENTITY",
    "Specialized Agencies": "SPECIALIZED_AGENCY",
    "Related Organizations": "RELATED_ORG",
    "Departments and Offices": "SECRETARIAT_UNIT",
    "Economic and Social Council Bodies": "ECOSOC_BODY",
    "Regional Commissions": "REGIONAL_COMMISSION",
}

entities = {}
alias_index = {}   # norm(alias) -> set(entity ids)


def add_alias(alias, eid, atype):
    k = norm(alias)
    if not k:
        return
    if len(k) < 3 and k not in {"un"}:
        return
    alias_index.setdefault(k, {})
    alias_index[k][eid] = atype


def add_entity(eid, canonical, etype, block, parent, definition, sources,
               acronym=None, aliases=(), multilingual=None, extra=None):
    if eid in entities:
        return
    entities[eid] = {
        "id": eid,
        "canonical": canonical,
        "acronym": acronym or "",
        "type": etype,
        "block": block,
        "parent": parent or "",
        "hierarchy_path": [p for p in ("UN system", block, parent or None, canonical) if p],
        "definition": (definition or "").strip()[:600],
        "sources": sources,
        "aliases": [],
        "multilingual": multilingual or {},
        "unterm_record_ids": [],
        "ref_id": None,
        "status": "active",
    }
    if acronym:
        add_alias(acronym, eid, "acronym")
        entities[eid]["aliases"].append(acronym)
    add_alias(canonical, eid, "canonical")
    entities[eid]["aliases"].append(canonical)
    for a in aliases:
        if a and norm(a) != norm(canonical):
            add_alias(a, eid, "variant")
            entities[eid]["aliases"].append(a)
    for lang, names in (multilingual or {}).items():
        if isinstance(names, str):
            names = [names]
        for n in names:
            if n:
                add_alias(n, eid, "translation")
                entities[eid]["aliases"].append(n)
    if extra:
        entities[eid].update(extra)


# ---------- 1. orgchart spine (placement authority) ----------
oc = json.load(open(ORGCHART))
for block, payload in oc["blocks"].items():
    for m in payload.get("members", []):
        add_entity(
            eid=m["id"], canonical=m["name"], etype=BLOCK_TO_TYPE.get(block, "ENTITY"),
            block=block, parent=m.get("parent_organ", "United Nations"),
            definition=m.get("definition", ""), sources=m.get("sources", []),
            acronym=m.get("acronym") or None, aliases=m.get("aliases", []),
            multilingual=m.get("multilingual") or {},
            extra={"ref_id": m.get("reference_id"),
                   "unterm_record_ids": [m["unterm_record_id"]] if m.get("unterm_record_id") else [],
                   "hq": m.get("hq", "")},
        )

spine_count = len(entities)

# ---------- 2. reference records: member states, agencies, related bodies ----------
ref_added = 0
for line in open(REFERENCE):
    r = json.loads(line)
    kind = r.get("kind")
    canonical = r.get("canonical_name") or ""
    if not canonical:
        continue
    langs = {}
    for lang, v in (r.get("languages") or {}).items():
        if isinstance(v, dict):
            vals = [v.get("common"), v.get("official")]
        elif isinstance(v, list):
            vals = v
        else:
            vals = [v]
        langs[lang] = [x for x in vals if x]
    etype = {"country": "MEMBER_STATE", "agency": "SPECIALIZED_AGENCY",
             "organ": "ORGAN", "related": "RELATED_ORG",
             "un_org": "ENTITY"}.get(kind, "ENTITY")
    block = {"country": "Member States", "agency": "Specialized Agencies",
             "organ": "Principal Organs", "related": "Related Organizations",
             "un_org": "UN System Bodies"}.get(kind, "UN System Bodies")
    eid = f"ref:{r.get('id') or norm(canonical)}"
    # merge into an orgchart entity if canonical/acronym already present
    target = None
    for normkey in (norm(canonical), norm(r.get("acronym") or "")):
        for cand in alias_index.get(normkey, {}):
            if cand in entities:
                target = cand
                break
        if target:
            break
    if target:
        entities[target]["ref_id"] = r.get("id")
        if kind == "country" and r.get("admission_date"):
            entities[target]["admission_date"] = r["admission_date"]
        for a in r.get("aliases") or []:
            add_alias(a, target, "variant")
        for lang, names in langs.items():
            for n in names:
                add_alias(n, target, "translation")
        continue
    add_entity(eid=eid, canonical=canonical, etype=etype, block=block,
               parent="United Nations" if kind != "country" else "",
               definition="", sources=r.get("source") or [],
               acronym=r.get("acronym") or None, aliases=r.get("aliases") or [],
               multilingual=langs,
               extra={"ref_id": r.get("id"),
                      "admission_date": r.get("admission_date", "")})
    ref_added += 1

# ---------- 3. curated extras the spine lacks ----------
CURATED = [
    # (canonical, acronym, type, block, parent, definition, aliases)
    ("United Nations Charter", "UN Charter", "INSTRUMENT", "Instruments",
     "United Nations", "The founding treaty of the United Nations, signed at San Francisco on 26 June 1945.",
     ["Charter of the United Nations", "the Charter"]),
    ("Universal Declaration of Human Rights", "UDHR", "INSTRUMENT", "Instruments",
     "General Assembly", "Adopted by the General Assembly on 10 December 1948 (resolution 217 A (III)).",
     ["Declaration of Human Rights"]),
    ("United Nations Convention on the Law of the Sea", "UNCLOS", "INSTRUMENT",
     "Instruments", "United Nations", "Adopted 1982; the legal framework for the oceans.", []),
    ("Treaty on the Non-Proliferation of Nuclear Weapons", "NPT", "INSTRUMENT",
     "Instruments", "United Nations", "Opened for signature 1968; entered into force 1970.", []),
    ("Convention on Certain Conventional Weapons", "CCW", "INSTRUMENT", "Instruments",
     "United Nations", "1980 convention on inhumane conventional weapons; a treaty regime with no single parent body.",
     ["Certain Conventional Weapons Convention"]),
    ("International Criminal Court", "ICC", "RELATED_ORG", "Related Organizations",
     "United Nations", "Permanent international tribunal established by the Rome Statute (1998); not a UN organ.",
     ["Rome Statute"]),
    ("World Trade Organization", "WTO", "RELATED_ORG", "Related Organizations",
     "United Nations", "Successor to GATT (1995); not a UN specialized agency.", []),
    ("International Committee of the Red Cross", "ICRC", "RELATED_ORG",
     "Related Organizations", "United Nations",
     "Impartial, neutral and independent humanitarian organization; not a UN body.", []),
    ("Security Council", "UNSC", "ORGAN", "Principal Organs", "United Nations",
     "Primary responsibility for the maintenance of international peace and security.",
     ["UN Security Council"]),
    ("General Assembly", "UNGA", "ORGAN", "Principal Organs", "United Nations",
     "The main deliberative, policymaking and representative organ of the UN.",
     ["UN General Assembly"]),
]
for canonical, acr, etype, block, parent, definition, aliases in CURATED:
    key = norm(canonical)
    if any(key == norm(e["canonical"]) for e in entities.values()):
        continue
    add_entity(f"cur:{norm(canonical).replace(' ', '_')}", canonical, etype, block,
               parent, definition, ["curated"], acronym=acr, aliases=aliases)

# ---------- 4. report ----------
multi = {k: sorted(v) for k, v in alias_index.items() if len(v) > 1}
out = {
    "meta": {
        "built_from": ["orgchart.json (111 entities, 11 blocks)",
                       "reference/records.jsonl (281 records)",
                       "curated extras"],
        "n_entities": len(entities),
        "n_aliases": len(alias_index),
        "n_ambiguous_aliases": len(multi),
        "spine_entities": spine_count,
        "reference_only_entities": ref_added,
        "captial_required_aliases": sorted(CAP_REQUIRED & set(alias_index)),
    },
    "alias_index": {k: sorted(v) for k, v in alias_index.items()},
    "entities": entities,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

print(json.dumps(out["meta"], indent=1))
print("\ndominant ambiguities (alias -> n entities):")
for k, v in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:15]:
    print(f"  {k!r}: {len(v)} {v[:4]}")
