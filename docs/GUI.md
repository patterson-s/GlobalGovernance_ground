# Review console — design

    python3 console.py            # http://127.0.0.1:8760

The console exists for one job: turning your reading of a document into structured
error data. The annotation viewer is a means to that end, so the unit of interaction
is a **verdict**, not a click-through.

## Why verdicts instead of an editable span list

An error log needs to distinguish failure modes, because they have different fixes:

| verdict | what it means | what it fixes downstream |
|---|---|---|
| `confirm` | span and identity are right | positive evidence; promotes a case to solved |
| `wrong_id` | span right, identity wrong | candidate ranking, alias weights |
| `reject` | not a UN-system mention at all | false-positive class; tail-NP detector |
| `missed` | system saw nothing (text selection) | detection recall; the gazetteer gap |
| `boundary` | right entity, wrong span extent | tokenizer and longest-match rules |

A free-text "this is wrong" note collapses these, and then nothing is learnable.

## Log schema

`logs/corrections.jsonl`, one record per verdict, append-only:

```
ts, doc_id, start, end, surface, verdict,
system_entity_id, human_entity_id, system_status, system_confidence, decided_by,
note, context (±90 chars), sentence
```

Character offsets are the stable key — not mention indices — so a correction survives
re-running the scanner after a gazetteer change. `context` and `sentence` are stored so
a later gold-set pass can adjudicate without reopening the document.

This one file is three things at once: the error log, the gold set, and the input to
`solved_cases` (a confirmed resolution should be replayed on the next document).

## Interaction decisions

- **Keyboard first.** `j`/`k` move, `1`–`4` pick a candidate, `x` reject, `c` confirm.
  A review pass over a document is hundreds of items; mouse-only is not viable.
- **Auto-save, no save button.** Every verdict is written immediately. Losing a review
  pass to a closed tab is unacceptable.
- **Confirm is hidden on unresolved mentions.** You cannot confirm a decision the system
  never made — that would log a fake positive.
- **Candidates carry their placement path**, not just a name: the decision is usually
  "which Council", and the block tells you.
- **Missed mentions come from text selection.** Select the span, search an entity, click.
- **Queue is ordered by uncertainty**, not document order — review what is actually at risk.
- **Export CSV** for analysis; JSONL stays the system of record.

## Deliberately absent

- No model call in the console. Reviewing must work offline and must not be blocked by
  an API. The model belongs in the batch pass, before the human sees the queue.
- No multi-annotator mode yet. The log schema carries `doc_id` but not an annotator id;
  add `annotator` before any inter-coder reliability work.
- No merge into the gazetteer. A verdict proposes; promotion into the spine stays a
  deliberate, separate step.

## Next

1. Replay `corrections.jsonl` on the next run (settled cases short-circuit).
2. Report precision by block from accumulated verdicts — the first honest accuracy number.
3. Promote repeatedly-missed surfaces into the gazetteer as pending entities.
