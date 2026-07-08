from __future__ import annotations

import re
from .shared import parse_sections, record_agent

AGENT_NAME = 'inductive-style-learner'

STYLE_PATTERN_META = {
    'Persistent:': {'marker_scope': 'description_label', 'risk_type': 'format_risk', 'allowed_markers': ['지속:']},
    'Unstable:': {'marker_scope': 'description_label', 'risk_type': 'format_risk', 'allowed_markers': ['불안정:']},
    'If successful': {'marker_scope': 'clause_marker', 'risk_type': 'style_only', 'allowed_markers': ['성공하면', '성공 시']},
    'If unsuccessful': {'marker_scope': 'clause_marker', 'risk_type': 'style_only', 'allowed_markers': ['실패하면', '실패 시']},
    'Each adventurer': {'marker_scope': 'rule_scope_phrase', 'risk_type': 'rule_scope_risk', 'allowed_markers': ['각 모험가']},
}


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').strip())


def _source_has_pattern(context: dict, source_pattern: str) -> bool:
    source = context.get('source_text', '')
    if source_pattern in {'Persistent:', 'Unstable:'}:
        sections = parse_sections(source)
        return any(_norm(x).startswith(source_pattern) for x in sections.get('description', []))
    return source_pattern in source


def _current_ko_marker(context: dict, source_pattern: str) -> str:
    ko = context.get('current_ko', '')
    sections = parse_sections(ko)
    descriptions = sections.get('description', [])
    if source_pattern == 'Persistent:':
        return '지속:' if any(_norm(x).startswith('지속:') for x in descriptions) else ''
    if source_pattern == 'Unstable:':
        return '불안정:' if any(_norm(x).startswith('불안정:') for x in descriptions) else ''
    if source_pattern == 'If successful':
        return '성공하면' if '성공하면' in ko else '성공 시' if '성공 시' in ko else ''
    if source_pattern == 'If unsuccessful':
        return '실패하면' if '실패하면' in ko else '실패 시' if '실패 시' in ko else ''
    if source_pattern == 'Each adventurer':
        return '각 모험가' if '각 모험가' in ko else ''
    return ''


def _evidence_strength(evidence_count: int, confidence: float) -> str:
    if evidence_count < 2:
        return 'singleton'
    if evidence_count < 3 or confidence < 0.67:
        return 'weak'
    if evidence_count >= 5 and confidence >= 0.9:
        return 'promotion_candidate'
    if confidence >= 0.8:
        return 'stable_observed'
    return 'weak'


def _candidate_type(status: str, strength: str) -> str:
    if status != 'pass':
        return 'review_candidate'
    if strength == 'promotion_candidate':
        return 'promotion_candidate'
    return 'check_only'


def _rule_strength_label(strength: str) -> str:
    if strength == 'promotion_candidate':
        return 'batch_observed_promotion_candidate'
    if strength == 'stable_observed':
        return 'batch_observed_stable'
    if strength == 'singleton':
        return 'batch_observed_singleton'
    return 'batch_observed_weak'


def _issue_id(code: str, source_pattern: str) -> str:
    key = re.sub(r'[^A-Z0-9]+', '_', source_pattern.upper()).strip('_') or 'STYLE'
    return f'{code}-STYLE-{key}'


def _build_check(context: dict, pattern: str, entry: dict) -> dict:
    current = _current_ko_marker(context, pattern)
    dominant = entry.get('dominant_ko') or ''
    confidence = float(entry.get('confidence') or 0.0)
    evidence_count = int(entry.get('count') or 0)
    strength = _evidence_strength(evidence_count, confidence)
    status = 'pass' if current and dominant and current == dominant else 'warn' if current else 'missing_marker'
    meta = STYLE_PATTERN_META.get(pattern, {'marker_scope': 'whole_text', 'risk_type': 'style_only', 'allowed_markers': []})
    ctype = _candidate_type(status, strength)
    eligible = ctype == 'promotion_candidate'
    requires_review = status != 'pass' and meta['risk_type'] in {'format_risk', 'rule_scope_risk'}
    return {
        'source': 'dominant_pattern_index',
        'source_pattern': pattern,
        'dominant_ko': dominant,
        'current_ko_marker': current,
        'status': status,
        'confidence': confidence,
        'evidence_count': evidence_count,
        'evidence_strength': strength,
        'examples': entry.get('examples', [])[:5],
        'rule_strength': _rule_strength_label(strength),
        'candidate_type': ctype,
        'eligible_for_promotion': eligible,
        'status_requires_human_approval': requires_review or eligible,
        'marker_scope': meta['marker_scope'],
        'risk_type': meta['risk_type'],
        'allowed_markers': meta.get('allowed_markers', []),
        'meaning_equivalent': status == 'pass' or meta['risk_type'] == 'style_only',
        'issue_id': _issue_id(context['code'], pattern) if ctype == 'review_candidate' else None,
        'review_reason': None if status == 'pass' else f"Observed dominant style marker `{dominant}` for `{pattern}` but current scoped KO marker is `{current or '<missing>'}`.",
    }


def _quality(index_available: bool, checks: list[dict]) -> dict:
    warnings = []
    singleton = sum(1 for x in checks if x.get('evidence_strength') == 'singleton')
    weak = sum(1 for x in checks if x.get('evidence_strength') == 'weak')
    if singleton:
        warnings.append('singleton_observed_patterns_not_promoted')
    if weak:
        warnings.append('weak_observed_patterns_not_promoted')
    return {
        'index_available': index_available,
        'checks_count': len(checks),
        'pass_count': sum(1 for x in checks if x.get('status') == 'pass'),
        'warn_count': sum(1 for x in checks if x.get('status') == 'warn'),
        'missing_marker_count': sum(1 for x in checks if x.get('status') == 'missing_marker'),
        'singleton_evidence_count': singleton,
        'weak_evidence_count': weak,
        'stable_observed_count': sum(1 for x in checks if x.get('evidence_strength') == 'stable_observed'),
        'promotion_candidates_count': sum(1 for x in checks if x.get('candidate_type') == 'promotion_candidate'),
        'review_candidates_count': sum(1 for x in checks if x.get('candidate_type') == 'review_candidate'),
        'warnings': warnings,
    }


def run(context):
    index = (context.get('batch_indexes') or {}).get('dominant_pattern_index') or {}
    index_available = bool(index.get('patterns'))
    checks = []
    for pattern, entry in sorted((index.get('patterns') or {}).items()):
        if not _source_has_pattern(context, pattern):
            continue
        checks.append(_build_check(context, pattern, entry))
    context['style_pattern_checks'] = checks
    # Backward-compatible field: retain all checks, now explicitly typed.
    context['learned_rule_candidates'] = checks
    context['style_learning_quality'] = _quality(index_available, checks)
    return record_agent(context, AGENT_NAME, {'summary': f"style checks={len(checks)} review={context['style_learning_quality']['review_candidates_count']} promotion={context['style_learning_quality']['promotion_candidates_count']}"})
