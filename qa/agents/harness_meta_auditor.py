from __future__ import annotations

from typing import Any

from .shared import record_agent
from .qa_reviewer import classify_learning_route
from llm_client import build_prompt, call_json, compact_card_payload, record_usage

AGENT_NAME = 'harness-meta-auditor'


AUDIT_AXES = [
    'unexamined_semantic_axes: source conditions, exceptions, timing, targets, counts, choices, persistent/unstable markers',
    'upstream_blind_spots: parser unresolved, missing ontology/term/rulebook evidence, weak spans, empty compact context',
    'deterministic_only_slips: code-only terminology/ontology/syntax/style/cross-card checks may pass when an exception or coexisting wrong variant is present',
    'reviewer_failures: blocking issue downgraded, suspected issue absent from issues, self-verification optimistic despite evidence gaps',
    'unknown_unknowns: problems not covered by deterministic pattern rules; propose regression/agent improvements rather than only card fixes',
]

CODE_ONLY_AUDIT_AGENTS = [
    'context-pack-builder',
    'terminology-manager',
    'syntax-pattern-controller',
    'inductive-style-learner',
    'cross-card-consistency-checker',
    'lore-ontology-checker',
    'patch-note-checker',
]


def _limit_list(value: Any, limit: int) -> Any:
    if isinstance(value, list):
        return value[:limit]
    return value


def _deterministic_audit_manifest(context: dict[str, Any]) -> dict[str, Any]:
    """Compact signals for a light LLM audit of code-only checker blind spots.

    This is deliberately not a second implementation of the deterministic checks.
    It exposes the code-only agents' own decisions/quality so the final LLM can
    ask: "what did the code assume was safe, and could a nearby exception still
    be visible in source_text/current_ko?"  The LLM may only create human-review
    slip candidates; it must not suppress deterministic blockers.
    """
    terminology = context.get('terminology_result') or {}
    ontology = context.get('ontology_result') or {}
    syntax = context.get('syntax_pattern_result') or {}
    style_quality = context.get('style_learning_quality') or {}
    cross_card = context.get('cross_card_consistency') or {}
    patch_result = context.get('patch_result') or {}
    context_pack = context.get('context_pack') or {}
    return {
        'purpose': 'light LLM safety net for code-only QA passes and not_available states',
        'code_only_agents': CODE_ONLY_AUDIT_AGENTS,
        'known_risk_patterns': [
            'approved_ko_present is not enough if another wrong Korean variant also appears in current_ko',
            'expected_ko_missing/pass decisions may miss near-miss spelling, vowel, particle, or entity-form variants',
            'not_available or empty hits mean the checker did not inspect that axis, not that the card is safe',
            'style/cross-card/syntax warnings should remain human-review proposals unless semantics are clearly changed',
        ],
        'terminology': {
            'status': terminology.get('status'),
            'quality': terminology.get('terminology_quality') or terminology.get('quality') or {},
            'source_terms_by_policy': terminology.get('source_terms_by_policy') or {},
            'term_classification': terminology.get('term_classification') or {},
            'checks': _limit_list(terminology.get('term_checks') or terminology.get('checks') or [], 12),
            'violations': _limit_list(terminology.get('violations') or [], 8),
        },
        'ontology': {
            'status': ontology.get('status'),
            'quality': ontology.get('quality') or {},
            'checks': _limit_list(ontology.get('checks') or [], 10),
            'violations': _limit_list(ontology.get('violations') or [], 8),
        },
        'syntax': {
            'status': syntax.get('status'),
            'quality': syntax.get('syntax_pattern_quality') or syntax.get('quality') or {},
            'checks': _limit_list(syntax.get('checks') or [], 8),
        },
        'style_learning_quality': style_quality,
        'cross_card_consistency': {
            'status': cross_card.get('status'),
            'quality': cross_card.get('quality') or {},
            'checks': _limit_list(cross_card.get('checks') or [], 8),
        },
        'patch_note': {
            'status': patch_result.get('status'),
            'quality': patch_result.get('quality') or {},
            'checks': _limit_list(patch_result.get('checks') or [], 8),
            'violations': _limit_list(patch_result.get('violations') or [], 8),
        },
        'context_pack_quality': context_pack.get('context_pack_quality') or context_pack.get('quality') or {},
    }


def _meta_payload(context: dict[str, Any]) -> dict[str, Any]:
    payload = compact_card_payload(context, include_upstream=True)
    payload.update({
        'audit_role': 'meta verifier of the QA harness, not a translator rewriting the card',
        'audit_axes': AUDIT_AXES,
        'deterministic_audit_manifest': _deterministic_audit_manifest(context),
        'final_verdict': context.get('verdict'),
        'score': context.get('score'),
        'requires_human_review': context.get('requires_human_review'),
        'qa_reviewer_result': context.get('qa_reviewer_result'),
        'learning_update_proposal': context.get('learning_update_proposal') or [],
        'step_quality': context.get('step_quality') or {},
        'source_quality_issues': context.get('source_quality_issues') or [],
        'context_summary': context.get('context_summary') or {},
    })
    return payload


def _llm_meta_audit(context: dict[str, Any]) -> dict[str, Any] | None:
    schema = {
        'harness_meta_audit': {
            'audit_verdict': 'ok | needs_harness_improvement | likely_slip_requires_review',
            'confidence': '0.0-1.0',
            'slip_candidates': [
                {
                    'issue_type': 'candidate missing issue class',
                    'severity': 'Critical|Major|Minor|Note',
                    'span_source': 'exact source span or empty',
                    'span_ko': 'exact KO span or empty',
                    'semantic_diff': 'why this may be a missed translation/rules problem',
                    'evidence': 'anchored evidence from payload only',
                    'suggested_fix': 'card-level fix or human review action',
                    'confidence': '0.0-1.0',
                    'requires_human_review': 'boolean',
                    'harness_gap': 'which agent/check failed to catch this',
                    'proposed_regression': 'generalized regression class, not this one phrase',
                }
            ],
            'harness_gaps': [
                {
                    'gap_type': 'parser|context_pack|terminology|ontology|rules_lawyer|verifier|reviewer|metrics|unknown',
                    'evidence': 'observed artifact/evidence gap',
                    'improvement': 'generalized harness improvement proposal',
                    'priority': 'high|medium|low',
                }
            ],
            'coverage_questions': ['remaining human-review questions, if any'],
        }
    }
    prompt = build_prompt(
        AGENT_NAME,
        (
            'You are a meta-cognition QA harness auditor. Audit the QA process itself after all translation QA agents ran. '
            'Do NOT merely translate or rewrite the card. Look for likely slips caused by missing parser slots, missing context evidence, '
            'optimistic reviewer/verifier gates, weak issue spans, or semantic axes not represented in issues_so_far. '
            'Pay special attention to deterministic_audit_manifest: these are code-only checks whose pass/not_available decisions can miss exceptions. '
            'For locked terminology/lore, approved_ko_present is not sufficient if current_ko also contains a plausible wrong variant, near-miss, or conflicting entity form. '
            'If a code-only checker did not inspect an axis because hits were empty/not_available, treat that as coverage uncertainty, not safety. '
            'If you identify a likely missed card problem, return it as a slip_candidate with exact source/KO evidence and a generalized harness_gap/proposed_regression. '
            'If evidence is insufficient, use coverage_questions/harness_gaps and needs_human_review rather than inventing facts. Return JSON only.'
        ),
        _meta_payload(context),
        schema,
    )
    result = call_json(AGENT_NAME, prompt, expected_keys=['harness_meta_audit'])
    record_usage(context, AGENT_NAME, result['usage'])
    return result.get('data')


def _issue_from_candidate(context: dict[str, Any], candidate: dict[str, Any], idx: int) -> dict[str, Any] | None:
    severity = str(candidate.get('severity') or 'Note')
    confidence = _as_float(candidate.get('confidence'), 0.0)
    requires_review = candidate.get('requires_human_review') is True
    # Only promote strongly evidenced candidates into the issue list. We still keep weaker ones in the audit/proposal artifact.
    if severity not in {'Critical', 'Major'} or confidence < 0.72:
        return None
    if not (candidate.get('span_source') or candidate.get('span_ko') or candidate.get('semantic_diff')):
        return None
    code = context.get('code') or 'CARD'
    return {
        'issue_id': f'{code}-META_AUDIT_SLIP-{idx:03d}',
        'issue_type': candidate.get('issue_type') or 'Harness meta-audit suspected missed issue',
        'severity': severity,
        'span_source': candidate.get('span_source', ''),
        'span_ko': candidate.get('span_ko', ''),
        'semantic_diff': candidate.get('semantic_diff', ''),
        'evidence': candidate.get('evidence') or candidate.get('harness_gap') or 'Harness meta-auditor flagged a likely missed issue.',
        'suggested_fix': candidate.get('suggested_fix') or 'Human review required before approval.',
        'confidence': confidence,
        'blocks_approval': True,
        'requires_human_review': True if requires_review else True,
        'review_status': 'meta_audit_candidate_human_review',
        'harness_gap': candidate.get('harness_gap'),
        'proposed_regression': candidate.get('proposed_regression'),
    }


def _proposal_from_candidate(context: dict[str, Any], candidate: dict[str, Any], idx: int, promoted_issue_id: str | None) -> dict[str, Any]:
    code = context.get('code') or 'CARD'
    issue_id = promoted_issue_id or f'{code}-META_AUDIT_CANDIDATE-{idx:03d}'
    route = classify_learning_route({
        'issue_id': issue_id,
        'issue_type': candidate.get('issue_type', ''),
        'evidence': ' '.join(str(candidate.get(k) or '') for k in ['semantic_diff', 'evidence', 'harness_gap', 'proposed_regression']),
        'suggested_fix': candidate.get('suggested_fix'),
    })
    if route.get('route') == 'card_fix':
        route = {
            'route': 'meta_harness_review',
            'test_id': None,
            'proposal_type': 'harness_meta_audit_review',
            'proposal': candidate.get('proposed_regression') or candidate.get('harness_gap') or 'Review meta-auditor slip candidate.',
        }
    return {
        'type': route['proposal_type'],
        'route': route['route'],
        'test_id': route.get('test_id'),
        'issue_id': issue_id,
        'proposal': route.get('proposal') or candidate.get('proposed_regression') or 'Review meta-auditor candidate.',
        'card_fix': candidate.get('suggested_fix'),
        'source_item_id': context.get('item_id'),
        'requires_human_approval': True,
        'meta_audit_candidate': True,
        'harness_gap': candidate.get('harness_gap'),
        'proposed_regression': candidate.get('proposed_regression'),
        'confidence': _as_float(candidate.get('confidence'), 0.0),
    }


def _proposal_from_gap(context: dict[str, Any], gap: dict[str, Any], idx: int) -> dict[str, Any]:
    code = context.get('code') or 'CARD'
    gap_type = gap.get('gap_type') or 'unknown'
    return {
        'type': 'harness_meta_gap',
        'route': 'meta_harness_review',
        'test_id': None,
        'issue_id': f'{code}-META_HARNESS_GAP-{idx:03d}',
        'proposal': gap.get('improvement') or 'Review QA harness gap.',
        'card_fix': None,
        'source_item_id': context.get('item_id'),
        'requires_human_approval': True,
        'gap_type': gap_type,
        'evidence': gap.get('evidence'),
        'priority': gap.get('priority') or 'medium',
    }


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fallback_audit(context: dict[str, Any]) -> dict[str, Any]:
    usage = context.get('llm_usage', {}).get(AGENT_NAME) or {}
    gaps: list[dict[str, Any]] = []
    if not usage.get('enabled'):
        gaps.append({
            'gap_type': 'llm_disabled',
            'evidence': 'QA_LLM_ENABLED is false; harness meta-auditor did not perform strong LLM unknown-unknown audit.',
            'improvement': 'Run with QA_LLM_ENABLED=1 and QA_LLM_PROVIDER=hermes-cli / QA_LLM_MODEL=gpt-5.5 for meta-audit coverage.',
            'priority': 'high',
        })
    elif usage.get('error'):
        gaps.append({
            'gap_type': 'llm_error',
            'evidence': usage.get('error'),
            'improvement': 'Fix LLM routing/timeout before trusting meta-audit coverage.',
            'priority': 'high',
        })
    return {
        'audit_verdict': 'meta_audit_not_run' if gaps else 'ok',
        'confidence': 0.0 if gaps else 0.4,
        'slip_candidates': [],
        'harness_gaps': gaps,
        'coverage_questions': [],
    }


def run(context: dict[str, Any]) -> dict[str, Any]:
    llm_data = _llm_meta_audit(context)
    audit = dict((llm_data or {}).get('harness_meta_audit') or _fallback_audit(context))
    candidates = audit.get('slip_candidates') or []
    gaps = audit.get('harness_gaps') or []

    issues = list(context.get('issues') or context.get('facts', {}).get('issues') or [])
    initial_proposal_count = len(context.get('learning_update_proposal') or [])
    proposals = list(context.get('learning_update_proposal') or [])
    promoted_count = 0
    for idx, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        issue = _issue_from_candidate(context, candidate, idx)
        promoted_id = None
        if issue:
            issues.append(issue)
            promoted_id = issue['issue_id']
            promoted_count += 1
        proposals.append(_proposal_from_candidate(context, candidate, idx, promoted_id))
    for idx, gap in enumerate(gaps, start=1):
        if isinstance(gap, dict) and (gap.get('priority') in {'high', 'medium'} or gap.get('gap_type') in {'llm_disabled', 'llm_error'}):
            proposals.append(_proposal_from_gap(context, gap, idx))

    context['issues'] = issues
    context['facts']['issues'] = issues
    context['learning_update_proposal'] = proposals
    if promoted_count or audit.get('audit_verdict') in {'likely_slip_requires_review', 'needs_harness_improvement', 'meta_audit_not_run'} or gaps:
        context['requires_human_review'] = True
    if promoted_count:
        context['score'] = min(int(context.get('score') or 82), 82)
        context['verdict'] = 'Needs revision'
    elif audit.get('audit_verdict') == 'likely_slip_requires_review':
        context['score'] = min(int(context.get('score') or 89), 89)
        context['verdict'] = 'Human review'
    context['harness_meta_audit'] = {
        'audit_verdict': audit.get('audit_verdict') or 'ok',
        'confidence': _as_float(audit.get('confidence'), 0.0),
        'slip_candidates': candidates,
        'harness_gaps': gaps,
        'coverage_questions': audit.get('coverage_questions') or [],
        'promoted_issue_count': promoted_count,
        'proposal_count_added': max(0, len(proposals) - initial_proposal_count),
        'role': 'meta verifier of QA harness blind spots; proposal-only until human approval',
    }
    return record_agent(
        context,
        AGENT_NAME,
        {'summary': f"{context['harness_meta_audit']['audit_verdict']} promoted={promoted_count} gaps={len(gaps)}"},
    )
