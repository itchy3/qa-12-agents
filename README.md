# TES / Boardgame Translation QA Harness Share Package

Clean shareable package for a repeatable boardgame/rules translation QA workflow.

## What is included

- `qa/agents/` — 12-agent QA modules
- `qa/scripts/run_agent_pipeline.py` — card QA runner
- `qa/scripts/compare_qa_iterations.py` — pass-to-pass reviewer
- `qa/scripts/build_rulebook_index.py` — rulebook preflight index builder
- `templates/` — sample input/feedback JSON files
- `qa/tests/` — public smoke/regression tests

## What is intentionally excluded

- actual rulebook PDF files
- generated rule banks from copyrighted PDFs
- local virtualenvs
- browser/MYBOX logs
- private run outputs
- project-specific card seed facts

## Quick start

```bash
cd TES-QA-Harness-Share
python3 qa/scripts/run_agent_pipeline.py \
  --run-id SAMPLE_PASS1 \
  --input-json templates/sample_cards.json
python3 qa/scripts/run_agent_pipeline.py \
  --run-id SAMPLE_PASS2 \
  --input-json templates/sample_cards_after_edit.json
python3 qa/scripts/compare_qa_iterations.py \
  --prev-run qa/runs/SAMPLE_PASS1 \
  --current-run qa/runs/SAMPLE_PASS2 \
  --human-feedback templates/sample_human_feedback.jsonl \
  --out-dir qa/runs/SAMPLE_PASS2/review
```

Run smoke tests:

```bash
python3 -m unittest discover -s qa/tests -p 'test_public_*.py'
python3 qa/tests/test_rulebook_preflight_index.py
python3 qa/tests/test_llm_agent_hybrid.py
```

## Optional compact LLM JSON agents

The harness is hybrid by design:

```text
deterministic preflight/retrieval + compact LLM semantic agents + deterministic validators
```

LLM usage is optional and off by default. Without LLM configuration, the pipeline keeps using deterministic parser/checker fallbacks.

Core LLM-capable agents:

- `source-meaning-checker` — source/KO semantic slot extraction
- `rules-lawyer` — modal, target/scope, timing, condition, number, exception checks
- `korean-editor` — conservative non-AI cleanup evaluation/proposal, never auto-apply
- `verifier` — advisory audit of upstream gate consistency
- `qa-reviewer` — advisory final synthesis/routing

Each LLM agent receives a compact card payload, not the full rulebook/glossary/run history. Outputs must be strict JSON and include confidence/UNKNOWN/human-review signals where applicable. Results are recorded in each QA JSON under `llm_usage`.

LLM dispute policy:

- The pipeline now emits a first-class `semantic_ir` artifact with `source_ir`, `ko_ir`, `status`, confidence, unknowns, and prior parser pattern.
- `source-meaning-checker` can downgrade an `UNRESOLVED` parser blocker only to `llm_resolved_unresolved_human_review`; it never auto-passes the card.
- Rules/LLM agents should emit candidate issues with `span_source`, `span_ko` or explicit missing marker, and `semantic_diff`.
- `terminology-manager` classifies source hits by policy: `proper_noun_terms`, `ordinary_words_ignored`, `locked_terms`, and `unknown_rule_terms`. Proper nouns/lore terms can be checked strictly; broad/common words are not blind blockers.
- `syntax-pattern-controller` separates semantic template mismatches from meaning-equivalent word-order/style variants. Style-only differences become `StyleWarning`/review signals, not Major blockers.
- `cross-card-consistency-checker` records sample strength (`singleton`, `weak_observed`, `stable_observed`) and keeps small-sample dominant patterns as review candidates rather than blockers.
- `rules-lawyer` compares semantic IR fields for modal, scope, target, timing, condition, exception, and number, then emits grounded candidate issues with `semantic_diff`.
- `verifier` checks whether a blocking candidate has enough evidence: source span + KO span/missing marker + semantic diff. Weak evidence becomes `issue_status=candidate`, `evidence_quality=weak`, `review_status=weak_evidence_human_review`.
- LLM agents may add new grounded issues.
- LLM agents may also return `issue_review[]` for existing deterministic issues.
- A high-confidence LLM `false_positive_candidate` does **not** delete the deterministic issue.
- Instead, the issue is annotated with `llm_disputed=true`, `review_status=llm_disputed_false_positive_candidate`, and an `llm_issue_review` evidence object.
- If every blocking issue is LLM-disputed, weak-evidence, or LLM-resolved-unresolved and no undisputed blocker remains, the final verdict becomes `Human review`, not `Pass`.
- Human approval is still required before suppressing or promoting the false-positive/semantic-IR lesson.

OpenAI-compatible setup:

```bash
export QA_LLM_ENABLED=1
export QA_LLM_API_KEY=...
export QA_LLM_MODEL=gpt-4o-mini
# optional; defaults to https://api.openai.com/v1
export QA_LLM_BASE_URL=https://api.openai.com/v1
python3 qa/scripts/run_agent_pipeline.py \
  --run-id LLM_PASS1 \
  --input-json templates/sample_cards.json
```

OpenRouter-style setup:

```bash
export QA_LLM_ENABLED=1
export QA_LLM_API_KEY=$OPENROUTER_API_KEY
export QA_LLM_BASE_URL=https://openrouter.ai/api/v1
export QA_LLM_MODEL=openai/gpt-4o-mini
```

Test/fake mode for CI:

```bash
export QA_LLM_ENABLED=1
export QA_LLM_FAKE_RESPONSES_JSON='{"source-meaning-checker":{"source_analysis":{"mode":"llm_json","semantic_pattern":"UNKNOWN","source_slots":{}},"translation_slot_result":{"mode":"llm_json","ko_slots":{},"slot_issues":[]}}}'
```

---

# Process Manual

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

