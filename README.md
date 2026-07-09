# TES / Boardgame Translation QA Harness

Shareable QA harness for boardgame/rules translation review. The current version is a **17-agent hybrid pipeline**:

```text
deterministic collectors / indexes
→ compact evidence-bound LLM workers
→ deterministic validators / reviewer routes
→ human-approved learning only
```

It is designed for high-stakes localization QA where literal string matching is not enough: modal force, target scope, timing, conditions, exceptions, lore entities, rulebook terminology, and cross-card style conventions all matter.

## Current version highlights

- 17-agent card QA pipeline.
- Optional LLM semantic workers with strict JSON outputs.
- Default-friendly support for compact models such as `4o-mini`.
- Strong-model escalation pattern for failures, uncertainty, route conflicts, and final validation.
- Deterministic preflight indexes for terminology, syntax, corpus patterns, cross-card consistency, ontology/lore hints, and optional rulebook evidence.
- Proposal-only learning: glossary/syntax/regression updates are never auto-applied.
- Answer-key evaluation support for adversarial test sets with strict issue-only scoring.
- Human-review routing for weak evidence, source/PDF/OCR uncertainty, model conflicts, and ambiguous semantic judgments.

## What is included

```text
qa/agents/                         17 QA agent modules
qa/scripts/run_agent_pipeline.py   main card/batch QA runner
qa/scripts/compare_qa_iterations.py pass-to-pass reviewer
qa/scripts/build_rulebook_index.py optional rulebook preflight index builder
qa/scripts/evaluate_adversarial_answer_key.py adversarial answer-key evaluator
qa/tests/                          smoke/regression tests
templates/                         sample cards and feedback JSON/JSONL
```

## What is intentionally excluded

- copyrighted rulebook PDFs
- generated rule banks from copyrighted PDFs
- local virtualenvs and caches
- browser/MYBOX logs
- private run outputs
- project-specific card seed facts unless explicitly shared as examples

## Architecture at a glance

The harness separates “retrieval/evidence” from “judgment”. Python/deterministic agents should gather compact, provenance-rich evidence; LLM agents should make bounded judgments from that evidence rather than inventing facts.

```text
Input cards
  ↓
Batch preflight indexes
  - syntax pattern index
  - dominant style/template index
  - cross-card consistency index
  - optional rulebook index
  ↓
Per-card 17-agent QA pipeline
  ↓
QA JSON + QA Markdown + review proposals
  ↓
Human edits / false-positive labels
  ↓
Iteration comparison
  ↓
Human-approved learning only
```

## 17-agent order

1. `context-pack-builder` — builds compact per-card evidence pack.
2. `source-meaning-checker` — extracts source/KO semantic IR and major semantic axes.
3. `terminology-manager` — deterministic glossary/term collector and policy classifier.
4. `terminology-pattern-worker` — LLM locked-term/proper-noun/lore judgment from term hints.
5. `syntax-pattern-controller` — deterministic syntax/template candidate collector.
6. `syntax-style-worker` — LLM distinction between style drift, meaning equivalence, and semantic mismatch.
7. `inductive-style-learner` — observes repeated style/template conventions and learning candidates.
8. `cross-card-consistency-checker` — deterministic cross-card pattern/corpus evidence collector.
9. `cross-card-pattern-worker` — LLM cross-card convention judgment from compact examples.
10. `lore-ontology-checker` — deterministic ontology/lore candidate collector.
11. `lore-ontology-worker` — LLM lore/entity consistency judgment from ontology hits.
12. `patch-note-checker` — checks patch-note or known-change evidence when provided.
13. `rules-lawyer` — LLM/deterministic rule semantics check: modal, target, scope, timing, condition, number, exception.
14. `korean-editor` — conservative Korean wording/readability review; proposal-only.
15. `verifier` — evidence-quality gate and conflict checker; does not silently delete issues.
16. `qa-reviewer` — final synthesis and route adjudication; Python routes are hints, not authority.
17. `harness-meta-auditor` — meta-audit for likely harness blind spots and weak evidence.

## Core design rules

- **No auto-approval from one model call.** LLMs may add issues, dispute issues, or route issues to human review; they do not silently suppress deterministic findings.
- **UNKNOWN is valid.** If evidence is insufficient, output `UNKNOWN`, `needs_human_review=true`, or a source-check route.
- **Evidence-bound LLMs.** Do not rely on model memory for TES lore or rulebook facts. Use supplied spans, glossary rows, ontology hits, rulebook snippets, and corpus examples.
- **Weak evidence is not a blocker.** Semantic blockers need source span + KO span/missing marker + semantic difference. Metadata/frontmatter mismatches can be evidenced by file/frontmatter spans.
- **Source/PDF/OCR/icon uncertainty routes to source check.** Do not turn extraction uncertainty into automatic regression memory.
- **Learning remains proposal-only.** Humans approve glossary, syntax, ontology, or regression updates.

## Required input per card

Minimum:

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

Useful optional fields:

```text
source_title
source_file
translation_file
polished_ko
term_glossary
syntax_dictionary
prior_translations
approved_qa_logs
patch_notes
rulebook_hits
ontology_hits
```

## Quick start: deterministic smoke run

```bash
cd TES-QA-Harness-Share
python3 qa/scripts/run_agent_pipeline.py \
  --run-id SAMPLE_PASS1 \
  --input-json templates/sample_cards.json
```

Expected outputs:

```text
qa/runs/SAMPLE_PASS1/output/context_packs/*.context.json
qa/runs/SAMPLE_PASS1/output/qa_json/*.qa.json
qa/runs/SAMPLE_PASS1/output/qa_md/*.qa.md
qa/runs/SAMPLE_PASS1/output/run_summary.json
qa/runs/SAMPLE_PASS1/review/*.jsonl
```

## Quick start: LLM semantic run

LLM usage is optional. If disabled, deterministic collectors/checkers still run.

### OpenAI-compatible API

```bash
export QA_LLM_ENABLED=1
export QA_LLM_PROVIDER=openai
export QA_LLM_API_KEY=...
export QA_LLM_MODEL=4o-mini
# optional; defaults to https://api.openai.com/v1
export QA_LLM_BASE_URL=https://api.openai.com/v1

python3 qa/scripts/run_agent_pipeline.py \
  --run-id LLM_PASS1 \
  --input-json templates/sample_cards.json
```

### OpenRouter-compatible API

```bash
export QA_LLM_ENABLED=1
export QA_LLM_PROVIDER=openai
export QA_LLM_API_KEY=$OPENROUTER_API_KEY
export QA_LLM_BASE_URL=https://openrouter.ai/api/v1
export QA_LLM_MODEL=openai/gpt-4o-mini
```

### Hermes CLI routing

If the environment has Hermes Agent configured, the harness can route LLM calls through Hermes:

```bash
export QA_LLM_ENABLED=1
export QA_LLM_PROVIDER=hermes-cli
export QA_LLM_MODEL=4o-mini
export QA_LLM_HERMES_PROVIDER=openai-codex
export QA_LLM_TIMEOUT_SECONDS=240

python3 qa/scripts/run_agent_pipeline.py \
  --run-id HERMES_4O_MINI_PASS1 \
  --input-json templates/sample_cards.json
```

For a strong-model validation run:

```bash
export QA_LLM_ENABLED=1
export QA_LLM_PROVIDER=hermes-cli
export QA_LLM_MODEL=gpt-5.5
export QA_LLM_HERMES_PROVIDER=openai-codex
```

## Recommended model policy

The current empirical default is:

```text
Use 4o-mini for normal full-process QA.
Escalate to gpt-5.5 only for failures, ambiguity, route conflicts, or final high-confidence validation.
```

Why: on the BM01-08 Conflict Adversarial v2 full process, `4o-mini` reached:

```text
strict loose issue-only: 57/57 = 100%
decoy false positives: 0/0
```

Caveat: one `source-meaning-checker` JSON parse error occurred in that run, so production usage should include retry/repair or strong-model fallback.

Suggested escalation triggers:

```text
JSON parse error
schema invalid
low confidence
agent conflict
Major/Critical issue with weak evidence
source/PDF/OCR/icon uncertainty
route conflict before regression-memory proposal
complex modal/scope/timing/condition/exception judgment
final release/adversarial validation
```

Suggested policy by agent:

```text
source-meaning-checker      4o-mini default; gpt-5.5 fallback on JSON/schema/low-confidence/complex IR
rules-lawyer                4o-mini default; gpt-5.5 fallback on complex rule semantics
syntax-style-worker         4o-mini default; gpt-5.5 fallback for blocker/semantic mismatch disputes
qa-reviewer                 4o-mini default; gpt-5.5 fallback for route conflicts/regression-memory proposals
harness-meta-auditor        off or 4o-mini for normal runs; batch-level gpt-5.5 for final validation
terminology-pattern-worker  4o-mini
lore-ontology-worker        4o-mini, escalate if lore identity is ambiguous
cross-card-pattern-worker   4o-mini, escalate for project-wide convention changes
korean-editor               4o-mini
verifier                    4o-mini or deterministic
```

## Fake LLM mode for CI

Use deterministic fake responses for regression tests:

```bash
export QA_LLM_ENABLED=1
export QA_LLM_FAKE_RESPONSES_JSON='{"source-meaning-checker":{"source_analysis":{"mode":"llm_json","semantic_pattern":"UNKNOWN","source_slots":{}},"translation_slot_result":{"mode":"llm_json","ko_slots":{},"slot_issues":[]}}}'
python3 -m unittest discover -s qa/tests -p 'test_*.py'
```

## Rulebook preflight index

The package does not include copyrighted rulebooks. If you have a local rulebook PDF, build a searchable meaning-unit rule bank:

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

## Human feedback and iteration review

Human feedback is JSONL, one row per issue:

```jsonl
{"card_id":"CARD_001","issue_id":"CARD_001-REG_TARGET_SCOPE_BROADENED","human_feedback":"accepted","action":"changed_translation","before_span":"각 모험가","after_span":"그 모험가","note":"same actor scope should be preserved"}
{"card_id":"CARD_002","issue_id":"CARD_002-STYLE_WARN","human_feedback":"false_positive","action":"kept_translation","note":"project style allows this variant","policy_candidate":"allow_equivalent_word_order"}
```

Run a second pass and compare:

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

The iteration reviewer should classify:

```text
resolved
persistent
new
false_positive
learning_candidate
needs_human_review
```

## Adversarial answer-key evaluation

For adversarial test sets, run the full QA process first, then evaluate issue-level evidence only:

```bash
python3 qa/scripts/evaluate_adversarial_answer_key.py \
  --run-dir qa/runs/RUN_ID \
  --answer-key path/to/answer_key.json \
  --out-json qa/runs/RUN_ID/review/adversarial_answer_key_evaluation.json \
  --out-md qa/runs/RUN_ID/review/adversarial_answer_key_evaluation.md
```

Report both metrics:

```text
strict exact issue-only: exact mutated/source span appeared in issue evidence
strict loose issue-only: issue evidence semantically matched the answer-key row
decoy false positives: harmless/non-flag rows incorrectly flagged
```

Do not count copied full `source_text`, full `current_ko`, broad markdown context, or answer-key leakage as detections.

## Full-process verification checklist

Before calling a run “full process”, verify:

```text
expected card count processed
expected QA JSON count
expected QA Markdown count
all 17 agents present in trace/results per card
llm_usage provider/model/error inspected for every LLM-backed agent
answer-key evaluation report generated when applicable
strict exact and strict loose rates reported separately
decoy false positives reported
```

Example checks:

```bash
python3 - <<'PY'
import json
from pathlib import Path
run=Path('qa/runs/RUN_ID')
expected=['context-pack-builder','source-meaning-checker','terminology-manager','terminology-pattern-worker','syntax-pattern-controller','syntax-style-worker','inductive-style-learner','cross-card-consistency-checker','cross-card-pattern-worker','lore-ontology-checker','lore-ontology-worker','patch-note-checker','rules-lawyer','korean-editor','verifier','qa-reviewer','harness-meta-auditor']
for p in sorted((run/'output'/'qa_json').glob('*.qa.json')):
    qa=json.loads(p.read_text())
    seen=set([x.get('agent') or x.get('name') for x in qa.get('agent_trace',[])]) | set(qa.get('pipeline_steps',{})) | set(qa.get('agent_results',{}))
    missing=[a for a in expected if a not in seen]
    errors={a:u.get('error') for a,u in qa.get('llm_usage',{}).items() if isinstance(u,dict) and u.get('error')}
    print(p.name, 'missing=', missing, 'llm_errors=', errors)
PY
```

## Tests

Run public smoke/regression tests:

```bash
python3 -m unittest discover -s qa/tests -p 'test_public_*.py'
python3 qa/tests/test_rulebook_preflight_index.py
python3 qa/tests/test_llm_agent_hybrid.py
```

For development, also compile-check agents and scripts:

```bash
python3 -m py_compile qa/agents/*.py qa/scripts/*.py
```

## Finalization criteria

Do not finalize just because one card says `Pass`. Finalization requires:

- no persistent blockers
- no new blockers
- previous blockers resolved or explicitly overruled as false positives
- source/PDF/OCR/rulebook uncertainties handled
- weak-evidence candidates routed to human review
- learning candidates approved by a human before promotion
- no unintended auto-apply or memory mutation

## Practical interpretation

- `Needs revision` means at least one undisputed blocker remains.
- `Human review` means blockers were weak, disputed, unresolved, or require human approval.
- `Pass with fixes` means only non-blocking edits/warnings remain.
- `Pass` should be rare and requires clean evidence, not merely lack of parser findings.
