from __future__ import annotations

import re
from typing import Any

from .shared import record_agent
from llm_client import build_prompt, call_json, record_usage

AGENT_NAME = 'terminology-pattern-worker'


def _term_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in context.get('term_glossary') or []:
        if isinstance(row, dict):
            rows.append(row)
    input_card = context.get('input_card') or {}
    for row in input_card.get('term_glossary') or []:
        if isinstance(row, dict):
            rows.append(row)
    compact = context.get('context_pack') or {}
    for row in compact.get('term_hits') or []:
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for row in rows:
        key = (
            str(row.get('en') or row.get('source') or '').strip().lower(),
            str(row.get('ko') or '').strip(),
            str(row.get('category') or row.get('term_category') or '').strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append({k: row.get(k) for k in ['en', 'ko', 'category', 'term_category', 'lock_policy', 'term_policy', 'status', 'source', 'source_ref', 'source_refs', 'forbidden_ko', 'deprecated_ko', 'near_miss_ko'] if k in row})
    return out[:40]


def _existing_term_issues(context: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for issue in context.get('facts', {}).get('issues', []) or []:
        if 'terminology' in str(issue.get('issue_type', '')).lower() or '-TERM_' in str(issue.get('issue_id', '')):
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


def _build_payload(context: dict[str, Any]) -> dict[str, Any]:
    term_result = context.get('terminology_result') or {}
    return {
        'card_id': context.get('code') or context.get('item_id'),
        'source_text': context.get('source_text', ''),
        'current_ko': context.get('current_ko', ''),
        'source_analysis': context.get('source_analysis') or {},
        'translation_slot_result': context.get('translation_slot_result') or {},
        'terminology_evidence': _dedupe_rows(_term_rows(context)),
        'deterministic_hints': {
            'terminology_result': {
                'status': term_result.get('status'),
                'violations': term_result.get('violations') or [],
                'glossary_review_proposals': term_result.get('glossary_review_proposals') or [],
                'source_terms_checked': term_result.get('source_terms_checked') or [],
                'source_terms_by_policy': term_result.get('source_terms_by_policy') or {},
                'term_classification': term_result.get('term_classification') or {},
                'terminology_quality': term_result.get('terminology_quality') or {},
            },
            'existing_terminology_issues': _existing_term_issues(context),
        },
        'task_rules': [
            'Python deterministic terminology checks are evidence/hints, not final authority.',
            'Judge only source terms supported by the source text and provided terminology evidence.',
            'For locked/proper-noun/lore terms, approved KO appearing once does not clear a coexisting wrong or near-miss variant.',
            'If evidence is insufficient, return requires_human_review=true rather than inventing a term rule.',
            'Do not auto-apply fixes; produce issue/review candidates only.',
        ],
    }


def _slug(value: str) -> str:
    slug = re.sub(r'[^A-Z0-9]+', '_', str(value or '').upper()).strip('_')
    return slug or 'TERM'


def _normalize_judgment(context: dict[str, Any], judgment: dict[str, Any], idx: int) -> dict[str, Any] | None:
    if not isinstance(judgment, dict):
        return None
    source_term = str(judgment.get('source_term') or judgment.get('span_source') or '').strip()
    expected_ko = str(judgment.get('expected_ko') or '').strip()
    observed_ko = str(judgment.get('observed_ko') or judgment.get('span_ko') or '').strip()
    evidence = str(judgment.get('evidence') or '').strip()
    status = str(judgment.get('status') or '').strip().lower()
    is_violation = bool(judgment.get('is_violation')) or status in {'violation', 'warn', 'fail', 'mismatch'}
    if not source_term or not (expected_ko or observed_ko) or not evidence:
        return None
    same_card_approved_present = bool(judgment.get('same_card_approved_present'))
    confidence = judgment.get('confidence')
    if confidence is None:
        confidence = 0.75
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.75
    requires_human_review = bool(judgment.get('requires_human_review', True))
    severity = str(judgment.get('severity') or ('Major' if is_violation and confidence >= 0.85 and not requires_human_review else 'StyleWarning')).strip()
    blocks = bool(judgment.get('blocks_approval')) if 'blocks_approval' in judgment else (severity == 'Major' and confidence >= 0.9 and not requires_human_review)
    issue_id = str(judgment.get('issue_id') or f"{context.get('code', 'CARD')}-LLM-TERM-{_slug(source_term)}-{idx:03d}")
    check = {
        'check_type': 'llm_terminology_consistency',
        'source': AGENT_NAME,
        'issue_id': issue_id,
        'source_term': source_term,
        'span_source': source_term,
        'expected_ko': expected_ko,
        'approved_ko': judgment.get('approved_ko') or ([expected_ko] if expected_ko else []),
        'observed_ko': observed_ko,
        'span_ko': observed_ko,
        'same_card_approved_present': same_card_approved_present,
        'is_violation': is_violation,
        'violation_type': judgment.get('violation_type') or judgment.get('drift_type') or 'terminology_consistency',
        'evidence': evidence,
        'suggested_fix': judgment.get('suggested_fix') or (f'Use approved terminology `{expected_ko}` for `{source_term}`.' if expected_ko else None),
        'confidence': confidence,
        'severity': severity,
        'status': 'warn' if is_violation else 'pass',
        'requires_human_review': requires_human_review,
        'blocks_approval': blocks,
        'candidate_type': 'llm_review_candidate' if requires_human_review else 'llm_confirmed_candidate',
        'deterministic_hint_used': judgment.get('deterministic_hint_used'),
        'meaning_delta': judgment.get('meaning_delta'),
        'semantic_diff': {
            'field': 'terminology.locked_term',
            'source_value': source_term,
            'expected_ko': expected_ko,
            'approved_ko': judgment.get('approved_ko') or ([expected_ko] if expected_ko else []),
            'observed_ko': observed_ko or None,
            'ko_value': observed_ko or 'missing',
        },
    }
    return check


def _issue_from_check(context: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    return {
        'issue_id': check['issue_id'],
        'issue_type': 'Terminology consistency',
        'severity': check.get('severity') or 'Major',
        'span_source': check.get('span_source') or check.get('source_term'),
        'span_ko': check.get('span_ko') or check.get('observed_ko') or '',
        'evidence': check.get('evidence'),
        'suggested_fix': check.get('suggested_fix'),
        'confidence': check.get('confidence'),
        'blocks_approval': bool(check.get('blocks_approval')),
        'issue_status': 'candidate' if check.get('requires_human_review') else 'confirmed',
        'evidence_quality': 'llm_grounded',
        'review_status': 'llm_terminology_human_review' if check.get('requires_human_review') else None,
        'semantic_diff': check.get('semantic_diff'),
        'llm_terminology_check': check,
    }


def _llm_judgments(context: dict[str, Any]) -> dict[str, Any] | None:
    schema = {
        'terminology_judgments': [
            {
                'source_term': 'English source term exactly supported by source_text',
                'expected_ko': 'approved Korean term',
                'approved_ko': ['approved forms'],
                'observed_ko': 'wrong/missing Korean span if any',
                'same_card_approved_present': True,
                'is_violation': True,
                'violation_type': 'near_miss|wrong_variant|missing_locked_term|false_positive',
                'evidence': 'grounded explanation using source_text/current_ko/terminology evidence',
                'suggested_fix': 'non-auto-applied suggested fix',
                'confidence': 0.0,
                'severity': 'Major|StyleWarning|Info',
                'requires_human_review': True,
                'blocks_approval': False,
                'deterministic_hint_used': 'which hint/evidence was used or overridden',
            }
        ],
        'terminology_worker_summary': {
            'overall_status': 'pass|warn|fail|not_enough_evidence',
            'coverage_gaps': [],
        },
    }
    prompt = build_prompt(
        AGENT_NAME,
        'Act as a terminology QA worker, not a regex postprocessor. Use Python output only as evidence/hints. Judge locked/proper-noun/lore terminology consistency from the supplied source text, Korean text, and glossary evidence. Return only grounded terminology judgments; if unsure, require human review.',
        _build_payload(context),
        schema,
    )
    result = call_json(AGENT_NAME, prompt, expected_keys=['terminology_judgments'])
    record_usage(context, AGENT_NAME, result['usage'])
    return result.get('data')


def run(context):
    data = _llm_judgments(context)
    checks: list[dict[str, Any]] = []
    if data:
        for idx, raw in enumerate(data.get('terminology_judgments') or [], start=1):
            check = _normalize_judgment(context, raw, idx)
            if check:
                checks.append(check)
    term_result = context.setdefault('terminology_result', {})
    llm_review = {
        'overall_status': (data or {}).get('terminology_worker_summary', {}).get('overall_status') if data else 'not_run',
        'coverage_gaps': (data or {}).get('terminology_worker_summary', {}).get('coverage_gaps', []) if data else [],
        'judgment_count': len(checks),
        'warn_count': len([c for c in checks if c.get('status') == 'warn']),
    }
    term_result['llm_terminology_review'] = llm_review
    term_result.setdefault('llm_checks', []).extend(checks)

    seen_issue_ids = {i.get('issue_id') for i in context.get('facts', {}).setdefault('issues', [])}
    for check in checks:
        if check.get('status') != 'warn':
            continue
        issue = _issue_from_check(context, check)
        if issue['issue_id'] not in seen_issue_ids:
            context['facts']['issues'].append(issue)
            seen_issue_ids.add(issue['issue_id'])

    status = term_result.get('status') or 'pass'
    if checks and any(c.get('status') == 'warn' for c in checks):
        if status == 'pass':
            term_result['status'] = 'needs_review'
    return record_agent(context, AGENT_NAME, {'summary': f"llm terminology judgments={len(checks)}"})
