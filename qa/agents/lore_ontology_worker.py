from __future__ import annotations

import re
from typing import Any

from .shared import record_agent
from llm_client import build_prompt, call_json, record_usage

AGENT_NAME = 'lore-ontology-worker'


def _limit(value: Any, n: int) -> Any:
    if isinstance(value, list):
        return value[:n]
    if isinstance(value, dict):
        return dict(list(value.items())[:n])
    return value


def _ontology_candidates(context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in (context.get('context_pack') or {}).get('ontology_hits') or []:
        if not isinstance(row, dict):
            continue
        rows.append({
            k: row.get(k)
            for k in [
                'id', 'entity_id', 'qid', 'slug', 'type', 'entity_type', 'canonical_en', 'term', 'en',
                'name_en', 'title_en', 'name', 'canonical_ko', 'ko', 'name_ko', 'title_ko',
                'official_ko', 'patch_ko', 'aliases_en', 'alias_en', 'aliases', 'english_aliases',
                'wiki_titles_en', 'aliases_ko', 'alias_ko', 'allowed_variants_ko', 'ko_aliases',
                'wiki_titles_ko', 'forbidden_ko', 'deprecated_ko', 'status', 'source', 'source_ref',
                'source_refs', 'notes', 'lock_policy',
            ]
            if k in row
        })
    return rows[:40]


def _existing_lore_issues(context: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for issue in context.get('facts', {}).get('issues', []) or []:
        text = f"{issue.get('issue_type', '')} {issue.get('issue_id', '')}".lower()
        if 'lore' not in text and 'ontology' not in text:
            continue
        out.append({
            'issue_id': issue.get('issue_id'),
            'issue_type': issue.get('issue_type'),
            'severity': issue.get('severity'),
            'span_source': issue.get('span_source'),
            'span_ko': issue.get('span_ko'),
            'evidence': issue.get('evidence'),
            'semantic_diff': issue.get('semantic_diff'),
            'blocks_approval': issue.get('blocks_approval'),
            'review_status': issue.get('review_status'),
        })
    return out[:30]


def _payload(context: dict[str, Any]) -> dict[str, Any]:
    ontology = context.get('ontology_result') or {}
    return {
        'card_id': context.get('code') or context.get('item_id'),
        'source_text': context.get('source_text', ''),
        'current_ko': context.get('current_ko', ''),
        'source_analysis': context.get('source_analysis') or {},
        'translation_slot_result': context.get('translation_slot_result') or {},
        'ontology_candidates': _ontology_candidates(context),
        'deterministic_hints': {
            'warning': 'Python ontology checks are retrieval/hints, not final authority. The LLM must judge only from source_text/current_ko and provided ontology candidates.',
            'ontology_result': {
                'status': ontology.get('status'),
                'checks': _limit(ontology.get('checks') or [], 20),
                'violations': _limit(ontology.get('violations') or [], 20),
                'quality': ontology.get('quality') or {},
            },
            'existing_lore_issues': _existing_lore_issues(context),
        },
        'task_rules': [
            'Do not use model memory of TES lore as evidence; use only provided ontology candidates and card text.',
            'Approved Korean form appearing once does not clear a coexisting wrong/near-miss variant.',
            'If ontology evidence is missing or ambiguous, return not_enough_evidence / requires_human_review rather than inventing lore facts.',
            'Do not auto-apply fixes. Create grounded review candidates only.',
        ],
    }


def _slug(value: str) -> str:
    slug = re.sub(r'[^A-Z0-9]+', '_', str(value or '').upper()).strip('_')
    return slug or 'ENTITY'


def _as_float(value: Any, default: float = 0.75) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_judgment(context: dict[str, Any], raw: dict[str, Any], idx: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source_entity = str(raw.get('source_entity') or raw.get('canonical_en') or raw.get('span_source') or '').strip()
    expected_ko = str(raw.get('expected_ko') or raw.get('canonical_ko') or '').strip()
    observed_ko = str(raw.get('observed_ko') or raw.get('span_ko') or '').strip()
    evidence = str(raw.get('evidence') or '').strip()
    if not source_entity or not evidence or not (expected_ko or observed_ko):
        return None
    status = str(raw.get('status') or '').lower()
    is_violation = bool(raw.get('is_violation')) or status in {'warn', 'fail', 'violation', 'mismatch'}
    confidence = _as_float(raw.get('confidence'))
    requires_review = bool(raw.get('requires_human_review', True)) or confidence < 0.95
    severity = str(raw.get('severity') or ('StyleWarning' if requires_review else 'Major')).strip()
    blocks = bool(raw.get('blocks_approval')) if 'blocks_approval' in raw else (is_violation and severity == 'Major' and confidence >= 0.95 and not requires_review)
    issue_id = str(raw.get('issue_id') or f"{context.get('code', 'CARD')}-LLM-LORE-{_slug(source_entity)}-{idx:03d}")
    return {
        'check_type': 'llm_lore_ontology_consistency',
        'source': AGENT_NAME,
        'issue_id': issue_id,
        'entity_id': raw.get('entity_id') or raw.get('id') or _slug(source_entity).lower(),
        'entity_type': raw.get('entity_type') or raw.get('type'),
        'canonical_en': source_entity,
        'span_source': source_entity,
        'expected_ko': expected_ko,
        'canonical_ko': expected_ko,
        'observed_ko': observed_ko,
        'span_ko': observed_ko,
        'same_card_approved_present': bool(raw.get('same_card_approved_present')),
        'is_violation': is_violation,
        'violation_type': raw.get('violation_type') or 'lore_ontology_consistency',
        'evidence': evidence,
        'suggested_action': raw.get('suggested_action') or raw.get('suggested_fix') or (f'Review TES lore rendering: use `{expected_ko}` for `{source_entity}`.' if expected_ko else 'Review TES lore rendering.'),
        'confidence': confidence,
        'severity': severity,
        'status': 'warn' if is_violation or requires_review else 'pass',
        'requires_human_review': requires_review,
        'blocks_approval': blocks,
        'candidate_type': 'llm_review_candidate' if requires_review else 'llm_confirmed_candidate',
        'deterministic_hint_used': raw.get('deterministic_hint_used'),
        'ontology_provenance': raw.get('ontology_provenance') or raw.get('source_refs') or [],
        'semantic_diff': {
            'field': 'lore_ontology.entity_rendering',
            'source_value': source_entity,
            'expected_ko': expected_ko or None,
            'observed_ko': observed_ko or None,
            'ko_value': observed_ko or 'missing',
        },
    }


def _issue_from_check(check: dict[str, Any]) -> dict[str, Any]:
    return {
        'issue_id': check['issue_id'],
        'issue_type': 'Lore ontology consistency',
        'severity': check.get('severity') or 'StyleWarning',
        'span_source': check.get('span_source') or check.get('canonical_en'),
        'span_ko': check.get('span_ko') or check.get('observed_ko') or '',
        'evidence': check.get('evidence'),
        'suggested_fix': check.get('suggested_action'),
        'confidence': check.get('confidence'),
        'blocks_approval': bool(check.get('blocks_approval')),
        'issue_status': 'candidate' if check.get('requires_human_review') else 'confirmed',
        'evidence_quality': 'llm_grounded',
        'review_status': 'llm_lore_ontology_human_review' if check.get('requires_human_review') else None,
        'semantic_diff': check.get('semantic_diff'),
        'llm_lore_ontology_check': check,
    }


def _llm_judgments(context: dict[str, Any]) -> dict[str, Any] | None:
    schema = {
        'lore_ontology_judgments': [
            {
                'source_entity': 'English source entity exactly supported by source_text and ontology candidates',
                'entity_id': 'ontology entity id if provided',
                'entity_type': 'person|place|artifact|faction|term|unknown',
                'expected_ko': 'approved/allowed Korean rendering from ontology candidate',
                'observed_ko': 'wrong/missing Korean rendering if any',
                'same_card_approved_present': True,
                'is_violation': True,
                'violation_type': 'proper_noun_near_miss|wrong_variant|missing_approved_lore_term|deprecated_form|not_enough_evidence|false_positive',
                'evidence': 'grounded rationale using source_text/current_ko/ontology candidates',
                'suggested_action': 'non-auto-applied review/fix suggestion',
                'confidence': 0.0,
                'severity': 'Major|StyleWarning|Info',
                'requires_human_review': True,
                'blocks_approval': False,
                'deterministic_hint_used': 'which Python hint was used or overridden',
                'ontology_provenance': ['source refs from candidate if available'],
            }
        ],
        'lore_ontology_worker_summary': {
            'overall_status': 'pass|warn|fail|not_enough_evidence',
            'coverage_gaps': [],
        },
    }
    prompt = build_prompt(
        AGENT_NAME,
        'Act as an evidence-bound TES lore ontology QA worker. Python has already retrieved candidate ontology rows; do not scan or invent the ontology from memory. Judge whether source entities are rendered consistently in Korean, including near-miss variants and approved+wrong coexistence. Return grounded JSON only; uncertainty must become human review, not a hallucinated fact.',
        _payload(context),
        schema,
    )
    result = call_json(AGENT_NAME, prompt, expected_keys=['lore_ontology_judgments'])
    record_usage(context, AGENT_NAME, result['usage'])
    return result.get('data')


def run(context: dict[str, Any]) -> dict[str, Any]:
    data = _llm_judgments(context)
    checks: list[dict[str, Any]] = []
    if data:
        for idx, raw in enumerate(data.get('lore_ontology_judgments') or [], start=1):
            check = _normalize_judgment(context, raw, idx)
            if check:
                checks.append(check)

    ontology = context.setdefault('ontology_result', {})
    review = {
        'source': AGENT_NAME,
        'checks': checks,
        'summary': (data or {}).get('lore_ontology_worker_summary') if data else None,
    }
    ontology['llm_lore_ontology_review'] = review
    ontology.setdefault('checks', [])
    ontology['checks'].extend(checks)
    ontology.setdefault('violations', [])
    ontology['violations'].extend([c for c in checks if c.get('status') == 'warn' or c.get('requires_human_review')])
    quality = ontology.setdefault('quality', {})
    usage = (context.get('llm_usage') or {}).get(AGENT_NAME) or {}
    quality.update({
        'llm_lore_ontology_worker_used': bool(usage.get('used')),
        'llm_lore_ontology_worker_error': usage.get('error'),
        'llm_lore_ontology_check_count': len(checks),
        'llm_lore_ontology_warn_count': sum(1 for c in checks if c.get('status') == 'warn'),
    })

    if any(c.get('status') == 'warn' or c.get('requires_human_review') for c in checks):
        ontology['status'] = 'warn'
    for check in checks:
        if check.get('is_violation'):
            issue = _issue_from_check(check)
            issues = context['facts'].setdefault('issues', [])
            if not any(x.get('issue_id') == issue['issue_id'] for x in issues):
                issues.append(issue)
    return record_agent(context, AGENT_NAME, {'summary': f"llm_lore_checks={len(checks)}"})
