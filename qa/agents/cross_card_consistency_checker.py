from __future__ import annotations
import re
from .shared import record_agent
AGENT_NAME = 'cross-card-consistency-checker'


def _choice_alignment_quality(source_choices: list[dict], ko_choices: list[dict], idx: int) -> str:
    if len(source_choices) != len(ko_choices):
        return 'choice_count_mismatch'
    if idx >= len(ko_choices):
        return 'choice_count_mismatch'
    source_key = source_choices[idx].get('choice_key')
    ko_key = ko_choices[idx].get('choice_key')
    if source_key and ko_key and source_key != ko_key:
        return 'choice_key_mismatch'
    return 'ok'


def _evidence_strength(evidence_count: int, confidence: float) -> str:
    if evidence_count <= 1:
        return 'singleton'
    if confidence < 0.67:
        return 'no_clear_dominant'
    if evidence_count >= 3:
        return 'stable_observed'
    return 'weak_observed'


def _choice_icon_checks(context: dict, index: dict) -> list[dict]:
    checks = []
    source_choices = context.get('facts', {}).get('source_slots', {}).get('choices') or []
    ko_choices = context.get('facts', {}).get('ko_slots', {}).get('choices') or []
    icon_index = index.get('choice_icons') or {}
    for idx, choice in enumerate(source_choices):
        icon = choice.get('choice_type_icon')
        if not icon or icon not in icon_index:
            continue
        ko_icon = ko_choices[idx].get('choice_type_icon') if idx < len(ko_choices) else None
        entry = icon_index[icon]
        alignment_quality = _choice_alignment_quality(source_choices, ko_choices, idx)
        status = 'pass' if ko_icon == icon or not str(icon).startswith('ICON_Unstable') else 'warn'
        checks.append({
            'check_type': 'choice_icon_consistency',
            'choice_key': choice.get('choice_key'),
            'source_icon': icon,
            'ko_icon': ko_icon,
            'batch_source_cards': entry.get('source_cards', []),
            'source_cards_unique': entry.get('source_cards_unique') or sorted(set(entry.get('source_cards', []))),
            'source_occurrences': entry.get('source_occurrences', len(entry.get('source_cards', []))),
            'source_card_count': entry.get('source_card_count', len(set(entry.get('source_cards', [])))),
            'batch_ko_icons': entry.get('ko_icons', {}),
            'alignment_quality': alignment_quality,
            'source_choice_count': len(source_choices),
            'ko_choice_count': len(ko_choices),
            'status': status,
            'severity': 'StyleWarning',
            'requires_human_review': status != 'pass' or alignment_quality != 'ok',
        })
    return checks


def _term_checks(context: dict, index: dict) -> list[dict]:
    checks = []
    source = context.get('source_text', '')
    ko = context.get('current_ko', '')
    for term, entry in sorted((index.get('terms') or {}).items()):
        if term not in source:
            continue
        dominant = entry.get('dominant')
        confidence = float(entry.get('confidence') or 0.0)
        evidence_count = int(entry.get('total_count') or sum(len(v) for v in (entry.get('variants') or {}).values()))
        dominant_count = int(entry.get('dominant_count') or len((entry.get('variants') or {}).get(dominant, [])))
        status = 'pass' if dominant and dominant in ko else 'warn'
        checks.append({
            'check_type': 'term_consistency',
            'term': term,
            'dominant_ko': dominant,
            'variants': entry.get('variants', {}),
            'confidence': confidence,
            'evidence_count': evidence_count,
            'dominant_count': dominant_count,
            'evidence_strength': _evidence_strength(evidence_count, confidence),
            'status': status,
            'severity': 'StyleWarning',
            'requires_human_review': status != 'pass' and evidence_count >= 2,
        })
    return checks


def _norm_source_term(term: str) -> str:
    term = re.sub(r'\bavailable\b', ' ', (term or '').lower())
    term = re.sub(r'\b(skill) die\b', r'\1 dice', term)
    term = re.sub(r'\bdie\b', 'dice', term)
    term = re.sub(r'[^a-z0-9 ]+', ' ', term)
    return re.sub(r'\s+', ' ', term).strip()


def _quantity_source_observations(source: str) -> list[dict]:
    text = ' '.join((source or '').split())
    observations = []
    for m in re.finditer(r'\b(?:gain|gains|lose|loses)\s+(?P<num>\d+)\s+(?P<term>[A-Za-z][A-Za-z -]*?)(?=\.|,|;| and | or |$)', text, re.I):
        term = _norm_source_term(m.group('term'))
        if term:
            observations.append({'source_family': 'GAIN_LOSE_NUM_TERM', 'term_source': term, 'index_key': f'GAIN_LOSE_NUM_TERM::{term}'})
    for m in re.finditer(r'\bexhaust\s+(?:(?P<num>\d+)\s+)?(?P<term>(?:available\s+)?[A-Za-z][A-Za-z -]*?(?:die|dice))\b', text, re.I):
        term = _norm_source_term(m.group('term'))
        if term:
            observations.append({'source_family': 'EXHAUST_NUM_TERM', 'term_source': term, 'index_key': f'EXHAUST_NUM_TERM::{term}'})
    seen = set()
    out = []
    for obs in observations:
        if obs['index_key'] in seen:
            continue
        seen.add(obs['index_key'])
        out.append(obs)
    return out


def _clean_ko_term(term: str) -> str:
    term = re.sub(r'\[\[[^]|]+\|([^]]+)\]\]', r'\1', term or '')
    term = re.sub(r'\[\[([^]]+)\]\]', r'\1', term)
    term = re.sub(r'\b(?:사용 가능한|각|모든|아무|해당)\b', ' ', term)
    term = re.sub(r'\s+', ' ', term).strip(' .,:;')
    term = re.sub(r'(?:을|를|이|가|은|는)$', '', term)
    if '스킬 주사위' in term:
        m = re.search(r'((?:전투|궁술|중갑|그림자|곡예술|데이드라 소환술)\s+스킬 주사위)$', term)
        term = m.group(1).strip() if m else '스킬 주사위'
    elif '주사위' in term:
        m = re.search(r'((?:[가-힣A-Za-z_]+\s+){0,1}[가-힣A-Za-z_]*주사위)$', term)
        if m:
            term = m.group(1).strip()
    return term


def _quantity_ko_candidates(ko: str) -> list[dict]:
    text = ' '.join((ko or '').split())
    candidates = []
    for m in re.finditer(r'(?P<num>\d+)\s*(?P<term>(?:\[\[[^]|]+\|)?(?![을를이가은는]\s)[가-힣A-Za-z_]{2,}(?:\]\])?)(?:을|를)?\s*(?P<verb>얻|잃)', text):
        candidates.append({'template': 'NUM_TERM_VERB', 'term_ko': _clean_ko_term(m.group('term')), 'span_ko': m.group(0)})
    for m in re.finditer(r'(?P<term>(?:\[\[[^]|]+\|)?(?![을를이가은는]\s)[가-힣A-Za-z_]{2,}(?:\]\])?)\s*(?P<num>\d+)(?:을|를)?\s*(?P<verb>얻|잃)', text):
        candidates.append({'template': 'TERM_NUM_VERB', 'term_ko': _clean_ko_term(m.group('term')), 'span_ko': m.group(0)})
    for m in re.finditer(r'(?P<term>(?:사용 가능한\s+)?(?:[가-힣A-Za-z_]+\s+){0,2}주사위)\s*(?P<num>\d+)개(?:를)?\s*소진', text):
        candidates.append({'template': 'TERM_NUM_COUNTER_VERB', 'term_ko': _clean_ko_term(m.group('term')), 'span_ko': m.group(0)})
    for m in re.finditer(r'(?P<num>\d+)개\s*(?P<term>(?:사용 가능한\s+)?(?:[가-힣A-Za-z_]+\s+){0,2}주사위)(?:를)?\s*소진', text):
        candidates.append({'template': 'NUM_COUNTER_TERM_VERB', 'term_ko': _clean_ko_term(m.group('term')), 'span_ko': m.group(0)})
    return [c for c in candidates if c.get('term_ko')]


def _select_quantity_ko_candidate(source_obs: dict, ko: str) -> dict | None:
    candidates = _quantity_ko_candidates(ko)
    if not candidates:
        return None
    family = source_obs.get('source_family')
    term_source = source_obs.get('term_source', '')
    if family == 'EXHAUST_NUM_TERM' or 'dice' in term_source:
        dice_candidates = [c for c in candidates if '주사위' in c.get('term_ko', '')]
        return dice_candidates[0] if dice_candidates else None
    if family == 'GAIN_LOSE_NUM_TERM':
        non_dice = [c for c in candidates if '주사위' not in c.get('term_ko', '')]
        return non_dice[0] if non_dice else None
    return candidates[0]


def _quantity_term_pattern_checks(context: dict, index: dict) -> list[dict]:
    checks = []
    patterns = index.get('quantity_term_patterns') or {}
    if not patterns:
        return checks
    for source_obs in _quantity_source_observations(context.get('source_text', '')):
        entry = patterns.get(source_obs['index_key'])
        if not entry:
            continue
        ko_obs = _select_quantity_ko_candidate(source_obs, context.get('current_ko', ''))
        current_template = ko_obs.get('template') if ko_obs else '<unclassified>'
        current_term_ko = ko_obs.get('term_ko') if ko_obs else '<missing>'
        span_ko = ko_obs.get('span_ko') if ko_obs else ''
        dominant = entry.get('dominant_template')
        confidence = float(entry.get('confidence') or 0.0)
        evidence_count = int(entry.get('total_count') or sum(len(v) for v in (entry.get('variants') or {}).values()))
        evidence_strength = _evidence_strength(evidence_count, confidence)
        status = 'pass' if current_template == dominant else 'warn'
        checks.append({
            'check_type': 'quantity_term_pattern_consistency',
            'index_key': source_obs['index_key'],
            'source_family': source_obs['source_family'],
            'term_source': source_obs['term_source'],
            'term_ko_dominant': entry.get('term_ko_dominant'),
            'current_term_ko': current_term_ko,
            'span_ko': span_ko,
            'current_template': current_template,
            'dominant_template': dominant,
            'variants': entry.get('variants', {}),
            'term_ko_variants': entry.get('term_ko_variants', {}),
            'confidence': confidence,
            'evidence_count': evidence_count,
            'evidence_strength': evidence_strength,
            'status': status,
            'status_reason': 'matches_quantity_term_dominant_template' if status == 'pass' else 'differs_from_quantity_term_dominant_template',
            'severity': 'StyleWarning',
            'meaning_equivalent': True,
            'requires_human_review': status != 'pass',
            'issue_id': f"{context['code']}-QTY-TERM-PATTERN-{re.sub(r'[^A-Z0-9]+', '_', source_obs['index_key'].upper()).strip('_')}",
        })
    return checks


def _source_syntax_pattern(source: str) -> str:
    lower = ' '.join((source or '').lower().split())
    if re.search(r'attack an enemy (?:\d+|once|twice|three|four|five) times?', lower):
        return 'Attack an enemy N times'
    if re.search(r'recover \d+ health \d+ times', lower):
        return 'Recover N health M times'
    if re.search(r'\b(?:gain|gains|lose|loses)\s+\d+\s+[a-z][a-z -]*\b', lower):
        return 'Gain/Lose N TERM'
    if re.search(r'use\s+(?:the|this|that|an?|your)?\s*[a-z][a-z\s-]*?\s+\d+\s+times?', lower):
        return 'VERB OBJECT N times'
    return ''


def _ko_syntax_template(ko: str, source_pattern: str) -> str:
    text = ' '.join((ko or '').split())
    if source_pattern == 'Attack an enemy N times':
        if re.search(r'적 하나를 \d+번 공격', text):
            return 'OBJ_COUNT_VERB'
        if re.search(r'\d+번 적 하나를 공격', text):
            return 'COUNT_OBJ_VERB'
    if source_pattern == 'Recover N health M times':
        if re.search(r'체력 \d+을 \d+번 회복', text):
            return 'OBJ_COUNT_VERB'
        if re.search(r'\d+번 체력 \d+을 회복', text):
            return 'COUNT_OBJ_VERB'
    if source_pattern == 'Gain/Lose N TERM':
        if re.search(r'\d+\s*(?:\[\[[^]|]+\|)?(?![을를이가은는]\s)[가-힣A-Za-z_]{2,}(?:\]\])?(?:을|를)?\s*(?:얻|잃)', text):
            return 'NUM_TERM_VERB'
        if re.search(r'(?:\[\[[^]|]+\|)?(?![을를이가은는]\s)[가-힣A-Za-z_]{2,}(?:\]\])?\s*\d+(?:을|를)?\s*(?:얻|잃)', text):
            return 'TERM_NUM_VERB'
    if source_pattern == 'VERB OBJECT N times':
        if re.search(r'[가-힣A-Za-z0-9_\s]+(?:을|를)\s*\d+번\s*(?:반복해\s*)?사용', text):
            return 'OBJ_COUNT_VERB'
        if re.search(r'(?:반복해서\s*)?\d+번\s*[가-힣A-Za-z0-9_\s]+(?:을|를)\s*사용', text):
            return 'COUNT_OBJ_VERB'
    return '<unclassified>'


def _syntax_structure_checks(context: dict, index: dict) -> list[dict]:
    checks = []
    structures = index.get('syntax_structures') or {}
    pattern = _source_syntax_pattern(context.get('source_text', ''))
    if not pattern or pattern not in structures:
        return checks
    entry = structures[pattern]
    current_template = _ko_syntax_template(context.get('current_ko', ''), pattern)
    dominant = entry.get('dominant_template')
    confidence = float(entry.get('confidence') or 0)
    evidence_count = int(entry.get('total_count') or sum(len(v) for v in (entry.get('variants') or {}).values()))
    evidence_strength = _evidence_strength(evidence_count, confidence)
    if confidence < 0.67:
        status = 'warn'
        status_reason = 'no_clear_dominant_template'
    else:
        status = 'pass' if current_template == dominant else 'warn'
        status_reason = 'matches_dominant_template' if status == 'pass' else 'differs_from_dominant_template'
    checks.append({
        'check_type': 'syntax_structure_consistency',
        'source_pattern': pattern,
        'current_template': current_template,
        'dominant_template': dominant,
        'variants': entry.get('variants', {}),
        'confidence': confidence,
        'evidence_count': evidence_count,
        'evidence_strength': evidence_strength,
        'candidate_type': 'review_candidate' if evidence_strength in {'singleton', 'weak_observed', 'no_clear_dominant'} else 'stable_observed_signal',
        'blocks_approval': False,
        'status': status,
        'status_reason': status_reason,
        'severity': 'StyleWarning',
        'meaning_equivalent': True,
        'requires_human_review': status != 'pass',
    })
    return checks


def _syntax_corpus_checks(context: dict, source_syntax_index: dict) -> list[dict]:
    checks = []
    pattern = _source_syntax_pattern(context.get('source_text', ''))
    entry = (source_syntax_index.get('patterns') or {}).get(pattern) if pattern else None
    if not entry:
        return checks
    current_template = _ko_syntax_template(context.get('current_ko', ''), pattern)
    dominant = entry.get('dominant_template')
    confidence = float(entry.get('confidence') or 0.0)
    evidence_count = int(entry.get('total_count') or 0)
    status = 'pass' if current_template == dominant else 'warn'
    if confidence < 0.67:
        status_reason = 'no_clear_dominant_template'
    elif status == 'pass':
        status_reason = 'matches_preflight_dominant_template'
    else:
        status_reason = 'differs_from_preflight_dominant_template'
    checks.append({
        'check_type': 'syntax_corpus_consistency',
        'source': 'source_syntax_pattern_index',
        'source_pattern': pattern,
        'current_template': current_template,
        'dominant_template': dominant,
        'variants': entry.get('ko_template_variants', {}),
        'confidence': confidence,
        'evidence_count': evidence_count,
        'evidence_strength': 'stable_observed' if evidence_count >= 3 and confidence >= 0.67 else 'weak',
        'status': status,
        'status_reason': status_reason,
        'severity': 'StyleWarning',
        'meaning_equivalent': current_template != '<unclassified>',
        'requires_human_review': status != 'pass',
        'promotion_eligible': bool(entry.get('promotion_eligible')),
        'issue_id': f"{context['code']}-SYNTAX-CORPUS-{re.sub(r'[^A-Z0-9]+', '_', pattern.upper()).strip('_')}",
    })
    return checks


def _quality(index: dict, source_syntax_index: dict, checks: list[dict]) -> dict:
    index_quality = index.get('quality') or {}
    return {
        'index_available': bool(index),
        'source_syntax_index_available': bool(source_syntax_index),
        'checks_count': len(checks),
        'pass_count': sum(1 for x in checks if x.get('status') == 'pass'),
        'warn_count': sum(1 for x in checks if x.get('status') == 'warn'),
        'review_required_count': sum(1 for x in checks if x.get('requires_human_review')),
        'check_types': {kind: sum(1 for x in checks if x.get('check_type') == kind) for kind in sorted({str(x.get('check_type')) for x in checks if x.get('check_type')})},
        'index_evidence_summary': {
            'choice_icons': len((index.get('choice_icons') or {})),
            'choice_icon_occurrences': index_quality.get('choice_icon_occurrences', sum((entry.get('source_occurrences') or len(entry.get('source_cards', []))) for entry in (index.get('choice_icons') or {}).values())),
            'choice_icon_unique_cards': index_quality.get('choice_icon_unique_cards', len({card for entry in (index.get('choice_icons') or {}).values() for card in (entry.get('source_cards_unique') or entry.get('source_cards') or [])})),
            'terms': len((index.get('terms') or {})),
            'syntax_structures': len((index.get('syntax_structures') or {})),
            'quantity_term_patterns': len((index.get('quantity_term_patterns') or {})),
            'source_syntax_patterns': len((source_syntax_index.get('patterns') or {})),
        },
        'warnings': [] if source_syntax_index else ['source_syntax_pattern_index_not_available'],
    }


def run(context):
    indexes = context.get('batch_indexes') or {}
    index = indexes.get('cross_card_consistency_index') or {}
    source_syntax_index = indexes.get('source_syntax_pattern_index') or {}
    checks = []
    checks.extend(_choice_icon_checks(context, index))
    checks.extend(_term_checks(context, index))
    checks.extend(_syntax_structure_checks(context, index))
    checks.extend(_quantity_term_pattern_checks(context, index))
    checks.extend(_syntax_corpus_checks(context, source_syntax_index))
    has_major = any(x.get('severity') == 'Major' for x in context['facts'].get('issues', []))
    has_warn = has_major or any(c.get('status') == 'warn' or c.get('requires_human_review') for c in checks)
    context['cross_card_consistency'] = {
        'source': 'cross_card_consistency_index' if index else 'not_available',
        'syntax_corpus_source': 'source_syntax_pattern_index' if source_syntax_index else 'not_available',
        'status': 'warn' if has_warn else 'pass',
        'checks': checks,
        'index_scope': index.get('scope') or source_syntax_index.get('scope'),
        'cross_card_consistency_quality': _quality(index, source_syntax_index, checks),
    }
    return record_agent(context, AGENT_NAME, {'summary': context['cross_card_consistency']['status']})
