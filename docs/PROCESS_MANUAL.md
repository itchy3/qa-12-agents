# TES/Boardgame Translation QA Process Manual

This package is a shareable, data-clean version of a 12-agent translation QA harness for boardgame/rules text.

## Goal

The harness is not a one-shot auto-approval bot. It is a repeatable review loop:

```text
preflight indexes → pass 1 QA → human edits/false-positive labels → pass 2 QA → iteration review → pass 3 if needed
```

## Process structure

1. **Preflight knowledge preparation**
   - glossary/term DB
   - syntax-pattern DB
   - optional lore/ontology lookup DB
   - optional rulebook preflight index
2. **Card-level QA run**
   - build compact context pack
   - run 12 QA agents in order
   - write JSON/Markdown/context-pack artifacts
3. **Human review**
   - edit translations
   - mark accepted findings and false positives
4. **Iteration review**
   - compare previous and current QA JSON
   - classify resolved, persistent, new, false-positive, and learning candidates
5. **Human-approved learning**
   - promote only approved lessons to glossary/syntax/regression rules

## 12-agent order

1. context-pack-builder
2. source-meaning-checker
3. terminology-manager
4. syntax-pattern-controller
5. inductive-style-learner
6. cross-card-consistency-checker
7. lore-ontology-checker
8. patch-note-checker
9. rules-lawyer
10. korean-editor
11. verifier
12. qa-reviewer

## Required input per card

```json
{
  "card_id": "CARD_001",
  "category": {
    "component_type": "card",
    "card_type": "encounter",
    "text_type": "effect_text"
  },
  "source_text": "After an adventurer defeats an enemy, that adventurer may recover 1 health.",
  "current_ko": "모험가가 적을 처치한 후, 그 모험가는 체력 1을 회복할 수 있습니다."
}
```

Optional fields:

- `polished_ko`
- `term_glossary`
- `syntax_dictionary`
- `prior_translations`
- `approved_qa_logs`
- `patch_notes`

## Preflight data to prepare

Place project-specific data under `data/` or adapt paths in `qa/agents/shared.py`.

```text
data/memory/term_db.jsonl
data/memory/syntax_rules.jsonl
data/ontology/tes_ontology_lookup.sqlite       optional
data/rulebook/rulebook.pdf                     optional, not included
```

The public package intentionally does **not** include copyrighted rulebooks, private run outputs, MYBOX logs, local virtualenvs, or project-specific card seeds.

## Rulebook preflight index

Build a searchable meaning-unit rule bank before QA runs:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install pymupdf
python3 qa/scripts/build_rulebook_index.py \
  --source data/rulebook/rulebook.pdf \
  --out-dir data/rulebook/index
```

Outputs:

```text
data/rulebook/index/rule_bank.jsonl
data/rulebook/index/rule_bank.sqlite
data/rulebook/index/rulebook_preflight_manifest.json
data/rulebook/index/rulebook_preflight_report.md
```

## Pass 1 QA

```bash
python3 qa/scripts/run_agent_pipeline.py \
  --run-id PASS1 \
  --input-json templates/sample_cards.json
```

Outputs:

```text
qa/runs/PASS1/output/context_packs/*.context.json
qa/runs/PASS1/output/qa_json/*.qa.json
qa/runs/PASS1/output/qa_md/*.qa.md
qa/runs/PASS1/output/run_summary.json
qa/runs/PASS1/review/*.jsonl
```

## Human feedback format

JSONL, one row per issue:

```jsonl
{"card_id":"CARD_001","issue_id":"CARD_001-REG_TARGET_SCOPE_BROADENED","human_feedback":"accepted","action":"changed_translation","before_span":"각 모험가","after_span":"그 모험가","note":"same actor scope should be preserved"}
{"card_id":"CARD_002","issue_id":"CARD_002-STYLE_WARN","human_feedback":"false_positive","action":"kept_translation","note":"project style allows this variant","policy_candidate":"allow_equivalent_word_order"}
```

## Pass 2 / iteration review

```bash
python3 qa/scripts/run_agent_pipeline.py \
  --run-id PASS2 \
  --input-json templates/sample_cards_after_edit.json

python3 qa/scripts/compare_qa_iterations.py \
  --prev-run qa/runs/PASS1 \
  --current-run qa/runs/PASS2 \
  --human-feedback templates/sample_human_feedback.jsonl \
  --out-dir qa/runs/PASS2/review
```

## Finalization criteria

Do not finalize just because one card says `Pass`. Finalization requires:

- no persistent blockers
- no new blockers
- previous blockers resolved or explicitly overruled as false positives
- rulebook/source/OCR uncertainties handled
- learning candidates remain proposal-only until human approval
