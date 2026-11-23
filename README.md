# unground — grounding layer for global-governance text

Give it text; it tells you where the UN-system entities are, what they are, and
where they sit. Built to run before text analysis, not during it.

```
python3 unground.py data/sample/charter.txt --triage   # annotate + print review queue
python3 console.py                                     # review console, 127.0.0.1:8760
```

Outputs (default `out/<docname>.*`):

```
<doc>.mentions.jsonl   one JSON object per mention (offsets, id, type, placement)
<doc>.mentions.csv     same, flat — load into R / pandas / Stata
<doc>.html             annotated document + placement sidebar + review queue
```

Zero dependencies beyond the standard library.

## What it does (v0, deterministic)

1. **Dictionary pass** — longest-match over a merged alias index
   (orgchart spine + UN external reference + curated instruments = 369 entities,
   ~1,900 aliases, multilingual included). Every hit gets a canonical id, type,
   block and hierarchy path.
2. **Discourse pass** — document-scoped abbreviation bindings (`Full Name (ACR)`),
   paragraph-scoped head-noun bindings (`the Council` after `the Security Council`),
   and a detector for bare definite NPs that have no antecedent.
3. **Triage** — anything unresolved or ambiguous goes to a review queue instead of
   being guessed.

No model calls. ~0.8 s for a 58k-character document.

## Review console

```
python3 console.py          # http://127.0.0.1:8760
```

Load a document, click any span, and give it a verdict: `confirm`, `wrong_id`,
`reject` (not UN), `missed` (select text the system never found), or `boundary`.
Every verdict appends to `logs/corrections.jsonl` with character offsets, context
and the sentence — simultaneously an error log, a gold set and solved-cases input.
Keyboard: `j`/`k` move, `1`–`4` pick a candidate, `x` reject, `c` confirm.
Design notes and log schema: `docs/GUI.md`.

## Known failure classes (found on the UN Charter, all real)

- `who` (pronoun) matched WHO (agency) — acronym aliases now require an
  actual acronym to be written (≥2 capitals, or periods).
- Head-noun binding by name *suffix* missed `International Court of Justice`
  ("the Court") — now takes the last organizational noun anywhere in the canonical.
- `the Organization` (30× in the Charter, meaning the UN) has no binding: the UN's
  canonical name contains no head noun. One review decision resolves all 30.
- The UN itself is absent from the orgchart spine (the chart lists it only as a
  parent organ), so it is added as a curated entity — otherwise `United Nations`
  resolves to a stray reference row and the mention count splits across two ids.

## Not built yet

- Model adjudication of the review queue (candidates-only, never free-text names).
- Replaying `logs/corrections.jsonl` on later runs, so decisions stick.
- PDF/DOCX ingestion; multi-document runs; corpus mode.
- Measured precision/recall — the console's verdicts are the raw material for it.

## Gazetteer sources

Built from `~/obsidian-vault/projects/unterminology` (orgchart spine, UN external
reference) plus curated instruments. To rebuild:

```
python3 build_gazetteer.py
```

The UNTERM full dump (253,117 records) sits behind that reference as the multilingual
surface-form source; it is a terminology database, not an org registry, and needs
filtering before use as an entity source.
