# unground — grounding layer for global-governance text

Give it text; it tells you where the UN-system entities are, what they are, and
where they sit. Built to run before text analysis, not during it.

    python3 unground.py data/sample/charter.txt --triage

Outputs (default `out/<docname>.*`):

    <doc>.mentions.jsonl   one JSON object per mention (offsets, id, type, placement)
    <doc>.mentions.csv     same, flat — load into R / pandas / Stata
    <doc>.html             annotated document + placement sidebar + review queue

## What it does in this version (v0, deterministic)

1. **Dictionary pass** — longest-match over a merged alias index
   (orgchart spine 111 entities + UN external reference 281 + curated instruments
   = 369 entities, ~1,900 aliases, multilingual included). Every hit gets a
   canonical id, type, block and hierarchy path.
2. **Discourse pass** — document-scoped abbreviation bindings (`Full Name (ACR)`),
   paragraph-scoped head-noun bindings (`the Council` after `the Security Council`),
   and a detector for bare definite NPs that have no antecedent.
3. **Triage** — anything unresolved or ambiguous goes to a review queue instead of
   being guessed. `--triage` prints it.

No model calls. ~0.8 s for a 58k-character document.

## Known failure classes (found on the UN Charter, all real)

- `who` (pronoun) matched WHO (agency) — acronym aliases now require an
  actual acronym to be written (≥2 capitals or periods).
- Head-noun binding by name *suffix* missed `International Court of Justice`
  ("the Court") — now takes the last organizational noun anywhere in the canonical.
- `the Organization` (30× in the Charter, meaning the UN) has no binding: the UN's
  canonical name contains no head noun. This is the review loop's job — one decision
  resolves 30 mentions.

## Not built yet

- Model adjudication of the review queue (cloud or local, candidates-only, never free text).
- Persisted decisions (`solved_cases.jsonl`) applied on later runs.
- PDF/DOCX ingestion; multi-document runs; corpus mode.
- Measured precision/recall against a gold set.

## Rebuild the gazetteer

    python3 build_gazetteer.py     # from ~/obsidian-vault/projects/unterminology
