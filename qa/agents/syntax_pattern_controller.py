from __future__ import annotations

import json
import re
from pathlib import Path
from .shared import QA_ROOT, record_agent

AGENT_NAME = 'syntax-pattern-controller'
SYNTAX_RULES_PATH = QA_ROOT / 'memory' / 'syntax_rules.jsonl'
PLACEHOLDER_TEMPLATES = {
    '',
    'parser_extracted',
    'seed_or_parser_rules',
    'unresolved',
    'section-level templates recorded in context pack',
    'locked syntax hits + dominant observed examples',
}


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').strip())


def _load_global_syntax_rules() -> list[dict]:
    if not SYNTAX_RULES_PATH.exists():
        return []
    rows = []
    for line in SYNTAX_RULES_PATH.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get('status') or '').lower() not in {'approved', 'locked'}:
            continue
        if row.get('source_pattern') and row.get('ko_template'):
            rows.append(row)
    return rows


def _input_syntax_rules(context: dict) -> list[dict]:
    rows = []
    for row in context.get('input_card', {}).get('syntax_dictionary') or []:
        if isinstance(row, dict) and row.get('source_pattern') and row.get('ko_template'):
            rows.append(row)
    for row in context.get('context_pack', {}).get('syntax_hits') or []:
        if isinstance(row, dict) and row.get('source_pattern') and row.get('ko_template'):
            rows.append(row)
    for row in context.get('context_pack', {}).get('similar_qa_logs') or []:
        if isinstance(row, dict) and row.get('ko_template') and str(row.get('status') or '').lower() == 'approved':
            rows.append({
                'rule_id': row.get('rule_id') or row.get('semantic_pattern') or row.get('card_id') or 'APPROVED_QA_LOG_TEMPLATE',
                'source_pattern': row.get('source_pattern') or row.get('semantic_pattern') or _source_pattern(context.get('source_text', '')),
                'ko_template': row.get('ko_template'),
                'status': 'approved',
                'strength': 'locked',
                'source': 'approved_qa_logs',
            })
    facts = context.get('facts', {})
    expected = facts.get('expected_template')
    if expected and expected not in PLACEHOLDER_TEMPLATES:
        rows.append({
            'rule_id': facts.get('semantic_pattern') or 'LEGACY_EXPECTED_TEMPLATE',
            'source_pattern': facts.get('semantic_pattern') or _source_pattern(context.get('source_text', '')),
            'ko_template': expected,
            'status': 'approved',
            'strength': 'locked',
            'source': facts.get('rule_source') or 'legacy_fact_template',
        })
    return rows


def _syntax_rules(context: dict) -> list[dict]:
    seen = set()
    out = []
    for row in _input_syntax_rules(context) + _load_global_syntax_rules():
        key = (str(row.get('rule_id') or ''), str(row.get('source_pattern') or ''), str(row.get('ko_template') or ''))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _source_pattern(source: str) -> str:
    lower = _norm(source).lower()
    if re.search(r'deal\s+\d+\s+damage\.?$', lower):
        return 'deal X damage'
    if re.search(r'attack an enemy (?:\d+|once|twice|three|four|five) times?', lower):
        return 'Attack an enemy N times'
    if re.search(r'recover \d+ health \d+ times', lower):
        return 'Recover N health M times'
    if re.search(r'each adventurer\s+(?:must\s+|may\s+)?(?:gain|gains)\s+\d+\s+', lower):
        return 'Each adventurer gains X object'
    return ''


def _source_matches_rule(source: str, rule_pattern: str) -> bool:
    pattern = _norm(rule_pattern).lower()
    lower = _norm(source).lower()
    if pattern in {'repeat_action_count', 'attack an enemy n times'}:
        return bool(re.search(r'attack an enemy (?:\d+|once|twice|three|four|five) times?', lower))
    if pattern in {'deal_damage_amount', 'deal x damage'}:
        return bool(re.search(r'deal\s+\d+\s+damage\.?$', lower))
    if pattern == 'recover n health m times':
        return bool(re.search(r'recover \d+ health \d+ times', lower))
    regex = re.escape(pattern)
    regex = regex.replace('x', r'\d+').replace('n', r'\d+').replace('m', r'\d+')
    return bool(re.search(regex, lower, re.I))


def _ko_template_for_pattern(ko: str, source_pattern: str) -> str:
    text = _norm(ko)
    if source_pattern in {'deal X damage', 'Deal X damage'}:
        if re.search(r'\d+\s*피해를\s*줍니다', text):
            return 'X 피해를 줍니다.'
        if re.search(r'피해\s*\d+을\s*줍니다', text):
            return '피해 X을 줍니다.'
        if re.search(r'피해\s*\d+를\s*줍니다', text):
            return '피해 X를 줍니다.'
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
    return '<unclassified>'


def _template_key(template: str) -> str:
    text = _norm(template)
    text = text.replace('{수량}', 'X').replace('{횟수}', 'N').replace('{대상}', 'OBJ').replace('{행동}', 'VERB')
    text = text.replace('세 번', 'N번').replace('한 번', 'N번').replace('두 번', 'N번')
    return text


def _rule_strength(row: dict) -> str:
    status = str(row.get('status') or '').lower()
    strength = str(row.get('strength') or row.get('rule_strength') or '').lower()
    if status in {'approved', 'locked'} and strength in {'locked', 'strong', 'required'}:
        return 'locked'
    if status == 'approved':
        return 'approved'
    return strength or 'observed'


def _is_broad_rule(row: dict) -> bool:
    pattern = _norm(str(row.get('source_pattern') or '')).lower()
    return pattern in {'if', 'when', 'before x', 'after x', 'before', 'after'}


def _locked_rule_checks(context: dict, source_pattern: str, actual_template: str) -> list[dict]:
    checks = []
    for row in _syntax_rules(context):
        if not _source_matches_rule(context.get('source_text', ''), str(row.get('source_pattern') or '')):
            continue
        expected = str(row.get('ko_template') or '').strip()
        strength = _rule_strength(row)
        broad = _is_broad_rule(row)
        if broad and actual_template == '<unclassified>':
            checks.append({
                'check_type': 'broad_syntax_rule_ignored',
                'rule_id': row.get('rule_id') or row.get('id') or re.sub(r'[^A-Z0-9]+', '_', str(row.get('source_pattern', '')).upper()).strip('_'),
                'source_pattern': row.get('source_pattern'),
                'expected_ko_template': expected,
                'actual_ko_template': actual_template,
                'rule_strength': strength,
                'status': 'ignored',
                'severity': 'Info',
                'meaning_equivalent': None,
                'rule_source': row.get('source') or 'syntax_dictionary',
                'reason': 'Broad syntax trigger skipped because current KO template is not classified; avoid blind blocking on generic words such as if/when/before/after.',
            })
            continue
        status = 'pass' if _template_key(actual_template) == _template_key(expected) else 'fail'
        checks.append({
            'check_type': 'locked_syntax_rule' if strength in {'locked', 'approved'} else 'syntax_rule',
            'rule_id': row.get('rule_id') or row.get('id') or re.sub(r'[^A-Z0-9]+', '_', str(row.get('source_pattern', '')).upper()).strip('_'),
            'source_pattern': row.get('source_pattern'),
            'expected_ko_template': expected,
            'actual_ko_template': actual_template,
            'rule_strength': strength,
            'status': status,
            'severity': 'Major' if status == 'fail' and strength in {'locked', 'approved'} else 'StyleWarning',
            'meaning_equivalent': status == 'pass',
            'rule_source': row.get('source') or 'syntax_dictionary',
        })
    return checks


def _observed_structure_check(context: dict, source_pattern: str, actual_template: str) -> dict | None:
    structures = ((context.get('batch_indexes') or {}).get('cross_card_consistency_index') or {}).get('syntax_structures') or {}
    if not source_pattern or source_pattern not in structures:
        return None
    entry = structures[source_pattern]
    dominant = entry.get('dominant_template')
    confidence = entry.get('confidence') or 0
    if actual_template == '<unclassified>':
        status = 'needs_review'
        reason = 'unclassified_current_template'
    elif confidence < 0.67:
        status = 'warn'
        reason = 'no_clear_dominant_template'
    else:
        status = 'pass' if actual_template == dominant else 'warn'
        reason = 'matches_dominant_template' if status == 'pass' else 'differs_from_dominant_template'
    return {
        'check_type': 'observed_syntax_structure',
        'source_pattern': source_pattern,
        'actual_ko_template': actual_template,
        'dominant_template': dominant,
        'variants': entry.get('variants', {}),
        'confidence': confidence,
        'status': status,
        'status_reason': reason,
        'severity': 'StyleWarning' if status != 'pass' else 'Pass',
        'meaning_equivalent': True,
        'requires_human_review': status != 'pass',
        'rule_strength': 'observed_dominant' if confidence >= 0.67 else 'observed_no_clear_dominant',
    }


def _existing_syntax_issues(context: dict) -> list[dict]:
    return [x for x in context.get('facts', {}).get('issues', []) if 'Syntax' in str(x.get('issue_type', ''))]


def _issue_id(code: str, check: dict) -> str:
    rid = str(check.get('rule_id') or check.get('source_pattern') or 'SYNTAX').upper()
    rid = re.sub(r'[^A-Z0-9]+', '_', rid).strip('_') or 'SYNTAX'
    return f'{code}-SYNTAX-{rid}'


def _append_locked_issues(context: dict, checks: list[dict]) -> None:
    issues = context['facts'].setdefault('issues', [])
    seen = {x.get('issue_id') for x in issues}
    has_existing_syntax_issue = any('Syntax' in str(x.get('issue_type', '')) for x in issues)
    for check in checks:
        if check.get('status') != 'fail' or check.get('severity') != 'Major':
            continue
        if has_existing_syntax_issue and check.get('rule_source') not in {'syntax_dictionary', 'approved_qa_logs'}:
            continue
        issue_id = _issue_id(context['code'], check)
        if issue_id in seen:
            continue
        issues.append({
            'issue_id': issue_id,
            'issue_type': 'Syntax Pattern Consistency',
            'severity': 'Major',
            'span_source': check.get('source_pattern', ''),
            'span_ko': check.get('actual_ko_template', ''),
            'evidence': f"Locked syntax rule `{check.get('rule_id')}` expects `{check.get('expected_ko_template')}`, but current KO matches `{check.get('actual_ko_template')}`.",
            'suggested_fix': f"Use locked syntax template: {check.get('expected_ko_template')}",
            'confidence': 0.92,
            'blocks_approval': True,
        })
        seen.add(issue_id)


def _quality(source_pattern: str, actual: str, expected: str, checks: list[dict], rules_loaded: int) -> dict:
    placeholder = actual in PLACEHOLDER_TEMPLATES or expected in PLACEHOLDER_TEMPLATES
    warnings = []
    if placeholder:
        warnings.append('placeholder_templates_not_compared')
    if not source_pattern:
        warnings.append('source_pattern_unclassified')
    if actual == '<unclassified>':
        warnings.append('ko_template_unclassified')
    return {
        'rules_loaded': rules_loaded,
        'checks_count': len(checks),
        'has_locked_rule': any(c.get('check_type') == 'locked_syntax_rule' for c in checks),
        'has_observed_structure': any(c.get('check_type') == 'observed_syntax_structure' for c in checks),
        'has_actual_template': actual not in PLACEHOLDER_TEMPLATES and actual != '<unclassified>',
        'has_expected_template': expected not in PLACEHOLDER_TEMPLATES and bool(expected),
        'used_placeholder_template': placeholder,
        'warnings': warnings,
    }


def run(context):
    facts = context['facts']
    source_pattern = _source_pattern(context.get('source_text', '')) or str(facts.get('semantic_pattern') or '')
    actual = _ko_template_for_pattern(context.get('current_ko', ''), source_pattern)
    if actual == '<unclassified>':
        actual = str(facts.get('actual_template') or '<unclassified>')
        if actual in PLACEHOLDER_TEMPLATES:
            actual = '<unclassified>'

    rules = _syntax_rules(context)
    checks = _locked_rule_checks(context, source_pattern, actual)
    observed = _observed_structure_check(context, source_pattern, actual)
    if observed:
        checks.append(observed)

    failing_locked = [c for c in checks if c.get('check_type') == 'locked_syntax_rule' and c.get('status') == 'fail']
    _append_locked_issues(context, checks)

    expected = ''
    if checks:
        expected = str(checks[0].get('expected_ko_template') or checks[0].get('dominant_template') or '')
    if not expected and facts.get('expected_template') not in PLACEHOLDER_TEMPLATES:
        expected = str(facts.get('expected_template') or '')

    if failing_locked:
        decision = 'Violation'
    elif any(c.get('check_type') == 'observed_syntax_structure' and c.get('status') == 'warn' for c in checks):
        decision = 'StyleWarning'
    elif any(c.get('status') in {'needs_review'} for c in checks):
        decision = 'NeedsReview'
    elif not checks and (not source_pattern or actual == '<unclassified>'):
        decision = 'InsufficientData'
    else:
        decision = 'Pass'

    template_match = bool(expected) and actual == expected
    quality = _quality(source_pattern, actual, expected, checks, len(rules))
    context['syntax_pattern_result'] = {
        'decision': decision,
        'source_pattern': source_pattern,
        'actual_ko_template': actual,
        'expected_ko_template': expected,
        'template_match': template_match,
        'meaning_equivalent': not failing_locked,
        'checks': checks,
        'rule_source': 'syntax_dictionary+batch_index' if checks else 'generic_parser',
        'rule_strength': 'locked' if failing_locked or any(c.get('check_type') == 'locked_syntax_rule' for c in checks) else ('observed' if checks else 'insufficient_data'),
        'syntax_pattern_quality': quality,
        'legacy_fact_templates': {
            'actual_template': facts.get('actual_template'),
            'expected_template': facts.get('expected_template'),
            'rule_source': facts.get('rule_source'),
        },
    }
    context['readability_exception'] = {
        'decision': 'NotNeeded' if decision in {'Pass', 'InsufficientData'} else 'ReviewRequired',
        'reason': 'No locked syntax conflict detected.' if not failing_locked else 'Locked syntax rule mismatch cannot be waived without human approval.',
    }
    return record_agent(context, AGENT_NAME, {'summary': f'{decision}; checks={len(checks)}'})
