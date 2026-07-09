from __future__ import annotations

from typing import Any

from .shared import record_agent
from llm_client import build_prompt, call_json, record_usage

AGENT_NAME = 'cross-card-pattern-worker'


def _limit(value: Any, n: int) -> Any:
    if isinstance(value, list):
        return value[:n]
    if isinstance(value, dict):
        return dict(list(value.items())[:n])
    return value


def _deterministic_hint_pack(context: dict) -> dict:
    indexes = context.get('batch_indexes') or {}
    cross_index = indexes.get('cross_card_consistency_index') or {}
    source_syntax = indexes.get('source_syntax_pattern_index') or {}
    cross = context.get('cross_card_consistency') or {}
    checks = cross.get('checks') or []
    relevant_checks = [
        c for c in checks
        if c.get('check_type') in {
            'quantity_term_pattern_consistency',
            'syntax_structure_consistency',
            'syntax_corpus_consistency',
            'term_consistency',
            'choice_icon_consistency',
        }
    ]
    return {
        'warning': 'These are deterministic hints/evidence summaries, not authoritative judgments. The LLM worker must judge family membership, dominant pattern, current pattern, and drift from examples and card text.',
        'current_deterministic_checks': _limit(relevant_checks, 12),
        'quantity_term_patterns': _limit(cross_index.get('quantity_term_patterns') or {}, 12),
        'syntax_structures': _limit(cross_index.get('syntax_structures') or {}, 12),
        'source_syntax_patterns': _limit(source_syntax.get('patterns') or {}, 12),
        'terms': _limit(cross_index.get('terms') or {}, 12),
    }


def _payload(context: dict) -> dict:
    pack = context.get('context_pack') or {}
    return {
        'card_id': context.get('code'),
        'item_id': context.get('item_id'),
        'source_text': context.get('source_text', ''),
        'current_ko': context.get('current_ko', ''),
        'source_analysis': context.get('source_analysis'),
        'translation_slot_result': context.get('translation_slot_result'),
        'deterministic_hints': _deterministic_hint_pack(context),
        'similar_qa_logs': _limit(pack.get('similar_qa_logs') or [], 10),
        'syntax_hits': _limit(pack.get('syntax_hits') or [], 10),
        'term_hits': _limit(pack.get('terminology_hits') or [], 10),
    }


def _llm_pattern_worker(context: dict) -> dict | None:
    schema = {
        'cross_card_pattern_review': {
            'overall_status': 'pass|warn|not_applicable|unknown',
            'judgments': [
                {
                    'family_name': 'short source action/term family name',
                    'same_family': True,
                    'dominant_pattern': 'human-readable dominant Korean pattern or UNKNOWN',
                    'current_pattern': 'human-readable current Korean pattern or UNKNOWN',
                    'current_template': 'optional normalized template',
                    'dominant_template': 'optional normalized template',
                    'is_drift': False,
                    'drift_type': 'quantity_term_word_order|syntax_word_order|term_consistency|icon_scope|style_marker|other|none|unknown',
                    'meaning_equivalent': True,
                    'requires_human_review': False,
                    'confidence': '0.0-1.0',
                    'evidence_examples': ['card ids or concise examples from payload'],
                    'evidence': 'anchored rationale from source_text/current_ko/examples',
                    'suggested_fix': 'optional conservative alignment suggestion',
                    'deterministic_hint_used': 'which hint/index helped, or none',
                }
            ],
            'coverage_gaps': ['what examples/evidence were missing, if any'],
        }
    }
    prompt = build_prompt(
        AGENT_NAME,
        (
            'Act as a real cross-card consistency QA worker, not a regex classifier. '
            'Use deterministic_hints only as tools/evidence, never as final authority. '
            'Judge whether the current card belongs to a known source action/term/style family; infer the family dominant Korean pattern from examples/hints; compare the current KO pattern; and decide whether it is a consistency drift. '
            'This is about project/card-family consistency, not merely grammatical naturalness. '
            'If evidence is insufficient, return unknown/not_applicable with coverage_gaps instead of inventing a rule. '
            'Return grounded JSON only; suggestions are human-review/proposal-only.'
        ),
        _payload(context),
        schema,
    )
    result = call_json(AGENT_NAME, prompt, expected_keys=['cross_card_pattern_review'])
    record_usage(context, AGENT_NAME, result['usage'])
    return result.get('data')


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _check_from_judgment(context: dict, judgment: dict, idx: int) -> dict:
    confidence = _as_float(judgment.get('confidence'), 0.0)
    is_drift = judgment.get('is_drift') is True
    needs_review = bool(judgment.get('requires_human_review')) or is_drift
    status = 'warn' if needs_review else 'pass'
    return {
        'check_type': 'llm_cross_card_pattern_consistency',
        'source': 'cross-card-pattern-worker',
        'family_name': judgment.get('family_name') or 'UNKNOWN',
        'same_family': judgment.get('same_family'),
        'dominant_pattern': judgment.get('dominant_pattern'),
        'current_pattern': judgment.get('current_pattern'),
        'current_template': judgment.get('current_template'),
        'dominant_template': judgment.get('dominant_template'),
        'is_drift': is_drift,
        'drift_type': judgment.get('drift_type') or ('unknown' if needs_review else 'none'),
        'meaning_equivalent': judgment.get('meaning_equivalent'),
        'evidence_examples': judgment.get('evidence_examples') or [],
        'evidence': judgment.get('evidence') or '',
        'suggested_fix': judgment.get('suggested_fix') or '',
        'deterministic_hint_used': judgment.get('deterministic_hint_used'),
        'confidence': confidence,
        'status': status,
        'status_reason': 'llm_judged_pattern_drift' if is_drift else ('llm_requires_human_review' if needs_review else 'llm_judged_consistent'),
        'severity': 'StyleWarning' if needs_review else 'Pass',
        'requires_human_review': needs_review,
        'candidate_type': 'llm_review_candidate' if needs_review else 'llm_check_only',
        'blocks_approval': False,
        'issue_id': f"{context.get('code', 'CARD')}-LLM-CROSS-PATTERN-{idx:03d}",
    }


def _quality(cross: dict, checks: list[dict], review: dict, usage: dict) -> dict:
    prior_quality = dict(cross.get('cross_card_consistency_quality') or {})
    prior_quality.update({
        'llm_pattern_worker_available': bool(review),
        'llm_pattern_worker_used': bool(usage.get('used')),
        'llm_pattern_worker_error': usage.get('error'),
        'llm_pattern_check_count': len(checks),
        'llm_pattern_warn_count': sum(1 for c in checks if c.get('status') == 'warn'),
    })
    return prior_quality


def run(context: dict) -> dict:
    review_data = _llm_pattern_worker(context)
    review = dict((review_data or {}).get('cross_card_pattern_review') or {})
    judgments = [j for j in (review.get('judgments') or []) if isinstance(j, dict)]
    checks = [_check_from_judgment(context, j, i) for i, j in enumerate(judgments, start=1)]

    cross = dict(context.get('cross_card_consistency') or {})
    existing_checks = list(cross.get('checks') or [])
    existing_checks.extend(checks)
    has_warn = cross.get('status') == 'warn' or any(c.get('status') == 'warn' or c.get('requires_human_review') for c in existing_checks)
    usage = context.get('llm_usage', {}).get(AGENT_NAME) or {}
    cross.update({
        'status': 'warn' if has_warn else 'pass',
        'llm_pattern_worker_source': 'cross-card-pattern-worker',
        'llm_pattern_review': {
            'overall_status': review.get('overall_status') or ('not_run' if not review else 'unknown'),
            'coverage_gaps': review.get('coverage_gaps') or [],
            'judgment_count': len(judgments),
        },
        'checks': existing_checks,
        'cross_card_consistency_quality': _quality(cross, checks, review, usage),
    })
    context['cross_card_consistency'] = cross
    return record_agent(context, AGENT_NAME, {'summary': f"llm_pattern_checks={len(checks)} warn={sum(1 for c in checks if c.get('status') == 'warn')}"})
