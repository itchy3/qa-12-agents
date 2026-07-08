from __future__ import annotations

import re
from .shared import record_agent

AGENT_NAME = 'korean-editor'


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').strip())


def _strip_markup_text(text: str) -> str:
    return re.sub(r'\[\[[^\]]+\]\]', '', text or '')


def _markup_tokens(text: str) -> set[str]:
    return set(re.findall(r'\[\[[^\]]+\]\]|\[[^\]]+\]', text or ''))


def _numbers(text: str) -> list[str]:
    return re.findall(r'\d+', text or '')


def _has_optional_ko(text: str) -> bool:
    return bool(re.search(r'수\s*있|가능|할\s*수', text or ''))


def _has_obligation_ko(text: str) -> bool:
    return bool(re.search(r'해야|하여야|반드시|버려야|얻어야|배치해야|잃어야|선택해야|굴려야', text or ''))


def _has_that_adventurer_ko(text: str) -> bool:
    return bool(re.search(r'그\s*모험가|해당\s*모험가', text or ''))


def _has_each_adventurer_ko(text: str) -> bool:
    return '각 모험가' in (text or '')


def _has_if_possible_ko(text: str) -> bool:
    return bool(re.search(r'가능하다면|가능하면|가능한 경우|할 수 있다면', text or ''))


def _translationese_markers(text: str) -> list[str]:
    plain = _strip_markup_text(text or '')
    markers = []
    patterns = {
        'nominalized_action': r'(회복|공격|획득|배치|처리|선택|굴림|해결)\s*을\s*수행',
        'stilted_progress': r'진행합니다|수행합니다|처리합니다',
        'unneeded_corresponding': r'해당\s+',
        'stilted_above': r'상기',
        'future_copula': r'것입니다',
        'object_particle_gap': r'\]\]\s+[을를이가은는으로로]',
    }
    for marker, pattern in patterns.items():
        if re.search(pattern, plain):
            markers.append(marker)
    return markers


def _rule_based_polish_candidate(text: str) -> str | None:
    if not text:
        return None
    candidate = text
    # Conservative boardgame-rule cleanup: nominalized action + 수행 -> direct predicate.
    replacements = [
        (r'체력\s*(\d+)\s*회복을\s*수행할 수 있습니다', r'체력 \1을 회복할 수 있습니다'),
        (r'체력\s*(\d+)\s*회복을\s*수행합니다', r'체력 \1을 회복합니다'),
        (r'아이템\s*(\d+)\s*획득을\s*수행할 수 있습니다', r'아이템 \1개를 획득할 수 있습니다'),
        (r'아이템\s*(\d+)\s*획득을\s*수행합니다', r'아이템 \1개를 획득합니다'),
    ]
    for pattern, repl in replacements:
        candidate = re.sub(pattern, repl, candidate)
    candidate = re.sub(r'\]\]\s+([을를이가은는으로로])', r']]\1', candidate)
    candidate = candidate.replace('진행합니다', '합니다').replace('수행합니다', '합니다')
    candidate = re.sub(r'\s+', ' ', candidate).strip()
    if candidate != text and len(_translationese_markers(candidate)) < len(_translationese_markers(text)):
        return candidate
    return None


def _style_score(text: str) -> int:
    plain = _strip_markup_text(text)
    score = 0
    if '잠시' in plain or '자연스럽' in plain:
        score += 2
    if re.search(r'습니다\s*\.\s*$', plain):
        score += 1
    score -= 2 * len(_translationese_markers(plain))
    # Reward common cleanup from nominalized translationese into a direct Korean verb.
    if re.search(r'체력\s*\d+을\s*회복할 수 있습니다|\d+\s*HP를\s*회복할 수 있습니다', plain):
        score += 2
    return score


def _check(status: str, check_id: str, evidence: str, suggested_fix: str | None = None) -> dict:
    return {
        'check_id': check_id,
        'status': status,
        'evidence': evidence,
        'suggested_fix': suggested_fix,
    }


def _meaning_structure_checks(source: str, current: str, polished: str) -> list[dict]:
    lower_source = source.lower()
    checks: list[dict] = []

    current_markups = _markup_tokens(current)
    polished_markups = _markup_tokens(polished)
    if current_markups == polished_markups:
        checks.append(_check('pass', 'markup_tokens_preserved', '마크업/아이콘 토큰이 current_ko와 polished_ko에서 동일함.'))
    else:
        checks.append(_check('fail', 'markup_tokens_preserved', f'마크업/아이콘 토큰 차이: missing={sorted(current_markups - polished_markups)}, added={sorted(polished_markups - current_markups)}', 'polished_ko 사용 전 current_ko의 마크업/아이콘 토큰을 정확히 보존해야 함.'))

    if _numbers(current) == _numbers(polished):
        checks.append(_check('pass', 'numbers_preserved', '숫자/수량 배열이 current_ko와 동일함.'))
    else:
        checks.append(_check('fail', 'numbers_preserved', f'수량/숫자 차이: current={_numbers(current)}, polished={_numbers(polished)}', '수량/숫자는 원문/current_ko 기준으로 보존해야 함.'))

    if re.search(r'\bmust\b', lower_source):
        if _has_obligation_ko(current) and _has_optional_ko(polished) and not _has_obligation_ko(polished):
            checks.append(_check('fail', 'must_force_preserved', '원문 must 의무 표현을 polished_ko가 선택/가능 표현으로 약화함.', 'must는 “해야 합니다/반드시 …합니다” 계열 의무 표현으로 보존해야 함.'))
        else:
            checks.append(_check('pass', 'must_force_preserved', 'must 의무/강제성이 약화되지 않음.'))

    if 'that adventurer' in lower_source:
        if _has_that_adventurer_ko(current) and _has_each_adventurer_ko(polished) and not _has_that_adventurer_ko(polished):
            check = _check('fail', 'target_scope_preserved', 'polished_ko가 동일 행위자/트리거 대상 범위를 전체 대상 범위로 확대함.', '동일 행위자 대상은 “그 모험가/해당 모험가”처럼 보존해야 함.')
            check['scope_check_type'] = 'target_scope_preservation'
            check['scope_risk_patterns'] = ['same_actor_to_each_actor']
            check['source_phrase'] = 'that adventurer'
            checks.append(check)
        else:
            check = _check('pass', 'target_scope_preserved', '동일 행위자/트리거 대상 범위가 확대되지 않음.')
            check['scope_check_type'] = 'target_scope_preservation'
            checks.append(check)

    if 'if possible' in lower_source:
        if _has_if_possible_ko(current) and not _has_if_possible_ko(polished):
            checks.append(_check('fail', 'if_possible_condition_preserved', 'polished_ko가 if possible 조건을 누락함.', '“가능하다면/가능하면” 조건을 보존해야 함.'))
        else:
            checks.append(_check('pass', 'if_possible_condition_preserved', 'if possible 조건이 보존됨.'))

    if 'regardless of location' in lower_source:
        current_has = '위치와 관계없이' in current or '위치에 관계없이' in current
        polished_has = '위치와 관계없이' in polished or '위치에 관계없이' in polished
        if current_has and not polished_has:
            checks.append(_check('fail', 'regardless_location_scope_preserved', 'polished_ko가 regardless of location 범위 조건을 누락함.', '위치와 관계없이/위치에 관계없이 범위 조건을 보존해야 함.'))
        else:
            checks.append(_check('pass', 'regardless_location_scope_preserved', 'regardless-of-location 범위 조건이 누락되지 않음.'))

    before_markers = set(_translationese_markers(current))
    after_markers = set(_translationese_markers(polished))
    removed = sorted(before_markers - after_markers)
    if removed:
        checks.append(_check('pass', 'translationese_marker_removed', f'번역투/AI투 marker 제거: {removed}'))
    elif before_markers:
        checks.append(_check('warn', 'translationese_marker_removed', f'current_ko의 번역투 marker가 polished_ko에도 남아 있음: {sorted(before_markers & after_markers)}'))
    else:
        checks.append(_check('not_applicable', 'translationese_marker_removed', 'current_ko에서 기계적으로 확인 가능한 번역투 marker가 없음.'))
    return checks


def _failed_meaning_checks(checks: list[dict]) -> list[dict]:
    return [c for c in checks if c.get('status') == 'fail']


def _build_im_not_ai_result(comparison: dict, checks: list[dict], current: str, polished: str | None, candidate_source: str) -> dict:
    candidate_decision = comparison.get('candidate_decision', 'no_candidate')
    if not polished or _norm(polished) == _norm(current):
        return {
            'status': 'skipped_no_polished_ko',
            'changed_spans': [],
            'change_ratio': 0.0,
            'meaning_preserved': True,
            'meaning_structure_preserved': True,
            'register_preserved': True,
            'requires_human_review': False,
            'candidate_source': candidate_source,
            'candidate_decision': candidate_decision,
            'checks': checks,
        }
    changed = [] if _norm(polished) == _norm(current) else [{'from': current, 'to': polished}]
    fail = _failed_meaning_checks(checks)
    if fail:
        status = 'rejected_rule_or_meaning_risk'
    elif comparison.get('winner') == 'polished_ko':
        status = 'accepted'
    else:
        status = 'not_selected'
    base_len = max(len(current), 1)
    return {
        'status': status,
        'changed_spans': changed,
        'change_ratio': round(abs(len(polished) - len(current)) / base_len, 3),
        'meaning_preserved': not fail,
        'meaning_structure_preserved': not fail,
        'register_preserved': not fail,
        'requires_human_review': bool(fail),
        'candidate_source': candidate_source,
        'candidate_decision': candidate_decision,
        'rejected_candidate_source': candidate_source if status == 'rejected_rule_or_meaning_risk' else None,
        'checks': checks,
    }


def _compare_current_and_polished(context: dict) -> dict:
    source = context.get('source_text', '')
    current = context.get('current_ko', '')
    polished = context.get('polished_ko') or context.get('input_card', {}).get('polished_ko')
    checks = _meaning_structure_checks(source, current, polished or current)
    if not polished or _norm(polished) == _norm(current):
        return {
            'status': 'skipped_no_polished_ko',
            'winner': 'current_ko',
            'candidate_decision': 'no_candidate',
            'meaning_delta': 'none',
            'rule_delta': 'none',
            'style_delta': 'none',
            'safe_to_apply': False,
            'reasons': ['polished_ko 후보가 없거나 current_ko와 동일해서 비교를 생략함.'],
            'required_fixes_before_use': [],
            'meaning_structure_checks': checks,
        }

    reasons: list[str] = []
    fixes: list[str] = []
    meaning_delta = 'preserved'
    rule_delta = 'preserved'

    failed_checks = _failed_meaning_checks(checks)
    if failed_checks:
        meaning_delta = 'degraded'
        rule_delta = 'degraded'
        for check in failed_checks:
            reasons.append(check.get('evidence', '의미구조 보존 실패'))
            if check.get('suggested_fix'):
                fixes.append(check['suggested_fix'])

    current_style = _style_score(current)
    polished_style = _style_score(polished)
    if meaning_delta == 'degraded' or rule_delta == 'degraded':
        style_delta = 'not_considered_due_to_rule_risk'
        winner = 'current_ko'
        candidate_decision = 'rejected_keep_current'
        if not reasons:
            reasons.append('polished_ko에 룰/의미 리스크가 있어 current_ko를 유지함.')
    else:
        if polished_style > current_style:
            style_delta = 'improved'
            winner = 'polished_ko'
            candidate_decision = 'accepted_use_polished'
            reasons.append('polished_ko가 의미/룰/마크업/수량 구조를 유지하면서 번역투를 줄임.')
        elif polished_style < current_style:
            style_delta = 'worse'
            winner = 'current_ko'
            candidate_decision = 'not_selected_keep_current'
            reasons.append('polished_ko가 의미는 유지하지만 문체상 current_ko보다 나아졌다는 근거가 부족함.')
        else:
            style_delta = 'tie'
            winner = 'tie_keep_current'
            candidate_decision = 'tie_keep_current'
            reasons.append('의미/룰은 유지하지만 문체 개선이 명확하지 않아 current_ko 유지 권장.')

    return {
        'status': 'compared',
        'winner': winner,
        'candidate_decision': candidate_decision,
        'meaning_delta': meaning_delta,
        'rule_delta': rule_delta,
        'style_delta': style_delta,
        'safe_to_apply': False,
        'reasons': reasons,
        'required_fixes_before_use': fixes,
        'meaning_structure_checks': checks,
        'style_scores': {'current_ko': current_style, 'polished_ko': polished_style},
    }


def run(context):
    external_polished = context.get('polished_ko') or context.get('input_card', {}).get('polished_ko')
    candidate_source = 'input_polished_ko' if external_polished else 'none'
    if external_polished:
        context['polished_ko'] = external_polished
    else:
        generated = _rule_based_polish_candidate(context.get('current_ko', ''))
        if generated:
            context['polished_ko'] = generated
            candidate_source = 'agent10_rule_based_cleanup'
        else:
            context['polished_ko'] = None
    comparison = _compare_current_and_polished(context)
    context['translation_comparison'] = comparison
    context['im_not_ai_result'] = _build_im_not_ai_result(comparison, comparison.get('meaning_structure_checks', []), context.get('current_ko', ''), context.get('polished_ko'), candidate_source)
    context['suggested_ko'] = context['facts'].get('suggested_ko', '수정 제안 없음')

    if comparison['status'] == 'skipped_no_polished_ko':
        context['final_translation'] = context['suggested_ko'] if context['facts'].get('verdict') != 'Pass' else '현행 유지 가능'
    elif comparison['winner'] == 'polished_ko':
        context['final_translation'] = context['polished_ko']
    elif comparison['winner'] in ['current_ko', 'tie_keep_current']:
        context['final_translation'] = context['current_ko']
    else:
        context['final_translation'] = context['suggested_ko'] if context['facts'].get('verdict') != 'Pass' else '현행 유지 가능'

    context['editor_result'] = {
        'auto_apply': False,
        'suggested_ko': context['suggested_ko'],
        'polished_ko': context.get('polished_ko'),
        'final_translation': context['final_translation'],
        'translation_comparison': comparison,
        'im_not_ai_result': context['im_not_ai_result'],
        'readability_exception': context.get('readability_exception'),
    }
    return record_agent(context, AGENT_NAME, {'summary': f"comparison {comparison['status']} winner={comparison['winner']} im_not_ai={context['im_not_ai_result']['status']}"})
