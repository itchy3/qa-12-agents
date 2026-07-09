from __future__ import annotations

from typing import Any

from .shared import record_agent
from llm_client import build_prompt, call_json, record_usage

AGENT_NAME = 'syntax-style-worker'


def _limit(value: Any, n: int) -> Any:
    if isinstance(value, list):
        return value[:n]
    if isinstance(value, dict):
        return dict(list(value.items())[:n])
    return value


def _payload(context: dict[str, Any]) -> dict[str, Any]:
    syntax = context.get('syntax_pattern_result') or {}
    style = context.get('style_pattern_checks') or []
    pack = context.get('context_pack') or {}
    indexes = context.get('batch_indexes') or {}
    cross_index = indexes.get('cross_card_consistency_index') or {}
    return {
        'card_id': context.get('code') or context.get('item_id'),
        'source_text': context.get('source_text', ''),
        'current_ko': context.get('current_ko', ''),
        'source_analysis': context.get('source_analysis') or {},
        'translation_slot_result': context.get('translation_slot_result') or {},
        'deterministic_hints': {
            'warning': 'Python syntax/style checks are evidence/hints, not final authority. The LLM must decide semantic mismatch vs style drift from card text and examples.',
            'syntax_pattern_result': {
                'decision': syntax.get('decision'),
                'source_pattern': syntax.get('source_pattern'),
                'actual_ko_template': syntax.get('actual_ko_template'),
                'expected_ko_template': syntax.get('expected_ko_template'),
                'template_match': syntax.get('template_match'),
                'meaning_equivalent': syntax.get('meaning_equivalent'),
                'rule_strength': syntax.get('rule_strength'),
                'quality': syntax.get('syntax_pattern_quality') or {},
                'checks': _limit(syntax.get('checks') or [], 20),
            },
            'style_pattern_checks': _limit(style, 20),
            'syntax_structures_index': _limit(cross_index.get('syntax_structures') or {}, 12),
            'source_syntax_pattern_index': _limit((indexes.get('source_syntax_pattern_index') or {}).get('patterns') or {}, 12),
        },
        'corpus_examples': {
            'syntax_hits': _limit(pack.get('syntax_hits') or [], 12),
            'similar_qa_logs': _limit(pack.get('similar_qa_logs') or [], 12),
        },
        'task_rules': [
            'Distinguish semantic/rules mismatch from style-only word-order drift.',
            'If meaning_equivalent=true, do not make it a blocking issue; route as human-review style/corpus consistency only.',
            'If evidence examples are insufficient, return unknown/not_enough_evidence rather than inventing a syntax rule.',
            'Suggestions are proposal-only and must not be auto-applied.',
        ],
    }


def _as_float(value: Any, default: float = 0.75) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize(context: dict[str, Any], raw: dict[str, Any], idx: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source_pattern = str(raw.get('source_pattern') or raw.get('family_name') or raw.get('pattern') or '').strip()
    evidence = str(raw.get('evidence') or '').strip()
    if not evidence and not source_pattern:
        return None
    meaning_equivalent = raw.get('meaning_equivalent')
    is_semantic_mismatch = bool(raw.get('is_semantic_mismatch'))
    is_style_drift = bool(raw.get('is_style_drift')) or bool(raw.get('is_drift'))
    requires_review = bool(raw.get('requires_human_review')) or is_style_drift or is_semantic_mismatch
    confidence = _as_float(raw.get('confidence'))
    blocks = bool(raw.get('blocks_approval')) if 'blocks_approval' in raw else (is_semantic_mismatch and confidence >= 0.9 and not bool(raw.get('requires_human_review')))
    severity = str(raw.get('severity') or ('Major' if blocks else 'StyleWarning' if requires_review else 'Pass')).strip()
    issue_id = str(raw.get('issue_id') or f"{context.get('code', 'CARD')}-LLM-SYNTAX-STYLE-{idx:03d}")
    return {
        'check_type': 'llm_syntax_style_judgment',
        'source': AGENT_NAME,
        'issue_id': issue_id,
        'source_pattern': source_pattern or 'UNKNOWN',
        'current_template': raw.get('current_template') or raw.get('actual_ko_template'),
        'expected_template': raw.get('expected_template') or raw.get('dominant_template'),
        'dominant_template': raw.get('dominant_template') or raw.get('expected_template'),
        'is_style_drift': is_style_drift,
        'is_semantic_mismatch': is_semantic_mismatch,
        'meaning_equivalent': meaning_equivalent,
        'drift_type': raw.get('drift_type') or ('semantic_mismatch' if is_semantic_mismatch else 'syntax_style_drift' if is_style_drift else 'none'),
        'evidence_examples': raw.get('evidence_examples') or [],
        'evidence': evidence,
        'suggested_fix': raw.get('suggested_fix') or '',
        'deterministic_hint_used': raw.get('deterministic_hint_used'),
        'confidence': confidence,
        'status': 'warn' if requires_review or is_semantic_mismatch or is_style_drift else 'pass',
        'severity': severity,
        'requires_human_review': requires_review,
        'blocks_approval': blocks,
        'candidate_type': 'llm_review_candidate' if requires_review else 'llm_check_only',
        'semantic_diff': {
            'field': 'syntax_style.pattern',
            'source_pattern': source_pattern or None,
            'expected_template': raw.get('expected_template') or raw.get('dominant_template'),
            'current_template': raw.get('current_template') or raw.get('actual_ko_template'),
            'meaning_equivalent': meaning_equivalent,
            'is_semantic_mismatch': is_semantic_mismatch,
        },
    }


def _issue_from_check(check: dict[str, Any]) -> dict[str, Any]:
    return {
        'issue_id': check['issue_id'],
        'issue_type': 'Syntax/style semantic review' if check.get('is_semantic_mismatch') else 'Syntax/style consistency review',
        'severity': check.get('severity') or 'StyleWarning',
        'span_source': check.get('source_pattern') or '',
        'span_ko': check.get('current_template') or '',
        'evidence': check.get('evidence'),
        'suggested_fix': check.get('suggested_fix'),
        'confidence': check.get('confidence'),
        'blocks_approval': bool(check.get('blocks_approval')),
        'issue_status': 'candidate' if check.get('requires_human_review') else 'confirmed',
        'evidence_quality': 'llm_grounded',
        'review_status': 'llm_syntax_style_human_review' if check.get('requires_human_review') else None,
        'semantic_diff': check.get('semantic_diff'),
        'llm_syntax_style_check': check,
    }


def _llm_judgments(context: dict[str, Any]) -> dict[str, Any] | None:
    schema = {
        'syntax_style_judgments': [
            {
                'source_pattern': 'source action/rule/style pattern being judged',
                'current_template': 'current Korean syntax/template or UNKNOWN',
                'dominant_template': 'locked/corpus-dominant template or UNKNOWN',
                'expected_template': 'expected template if distinct',
                'is_style_drift': True,
                'is_semantic_mismatch': False,
                'meaning_equivalent': True,
                'drift_type': 'word_order_style|locked_template_mismatch|scope_semantic_mismatch|format_marker|other|none|unknown',
                'evidence_examples': ['card ids or concise examples from payload'],
                'evidence': 'grounded rationale using source/current_ko/examples',
                'suggested_fix': 'proposal-only conservative alignment suggestion',
                'confidence': 0.0,
                'severity': 'Major|StyleWarning|Info',
                'requires_human_review': True,
                'blocks_approval': False,
                'deterministic_hint_used': 'which hint/index helped, or none',
            }
        ],
        'syntax_style_worker_summary': {
            'overall_status': 'pass|warn|fail|not_enough_evidence',
            'coverage_gaps': [],
        },
    }
    prompt = build_prompt(
        AGENT_NAME,
        'Act as a syntax/style QA worker, not a regex classifier. Python has retrieved locked syntax rules, observed corpus patterns, and current template hints. Use them as evidence only. Decide whether differences are semantic/rules mismatches or style-only/corpus consistency drifts. Return grounded JSON; insufficient evidence must become review/unknown, not an invented rule.',
        _payload(context),
        schema,
    )
    result = call_json(AGENT_NAME, prompt, expected_keys=['syntax_style_judgments'])
    record_usage(context, AGENT_NAME, result['usage'])
    return result.get('data')


def run(context: dict[str, Any]) -> dict[str, Any]:
    data = _llm_judgments(context)
    checks: list[dict[str, Any]] = []
    if data:
        for idx, raw in enumerate(data.get('syntax_style_judgments') or [], start=1):
            check = _normalize(context, raw, idx)
            if check:
                checks.append(check)

    syntax = context.setdefault('syntax_pattern_result', {})
    review = {
        'source': AGENT_NAME,
        'checks': checks,
        'summary': (data or {}).get('syntax_style_worker_summary') if data else None,
    }
    syntax['llm_syntax_style_review'] = review
    syntax.setdefault('checks', [])
    syntax['checks'].extend(checks)
    quality = syntax.setdefault('syntax_pattern_quality', {})
    usage = (context.get('llm_usage') or {}).get(AGENT_NAME) or {}
    quality.update({
        'llm_syntax_style_worker_used': bool(usage.get('used')),
        'llm_syntax_style_worker_error': usage.get('error'),
        'llm_syntax_style_check_count': len(checks),
        'llm_syntax_style_warn_count': sum(1 for c in checks if c.get('status') == 'warn'),
    })
    if any(c.get('is_semantic_mismatch') for c in checks):
        syntax['decision'] = 'Violation'
        syntax['meaning_equivalent'] = False
    elif any(c.get('is_style_drift') or c.get('requires_human_review') for c in checks):
        syntax['decision'] = 'StyleWarning'

    for check in checks:
        if check.get('status') == 'warn' or check.get('is_semantic_mismatch'):
            issue = _issue_from_check(check)
            issues = context['facts'].setdefault('issues', [])
            if not any(x.get('issue_id') == issue['issue_id'] for x in issues):
                issues.append(issue)
    return record_agent(context, AGENT_NAME, {'summary': f"llm_syntax_style_checks={len(checks)}"})
