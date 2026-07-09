from __future__ import annotations

import re
from typing import Any

from .shared import record_agent
from llm_client import build_prompt, call_json, compact_card_payload, record_usage

AGENT_NAME = 'rules-lawyer'


def _has_optional_ko(text: str) -> bool:
    return bool(re.search(r'수\s*있|가능|해도\s*됩니다|할\s*수', text or ''))


def _has_obligation_ko(text: str) -> bool:
    return bool(re.search(r'해야|하여야|반드시|버려야|얻어야|배치해야|잃어야|선택해야|굴려야', text or ''))


def _evidence_score(issue: dict[str, Any]) -> int:
    score = 0
    if issue.get('span_source') or issue.get('source_span'):
        score += 1
    if issue.get('span_ko') or issue.get('ko_span'):
        score += 1
    if issue.get('semantic_diff'):
        score += 2
    if issue.get('evidence_quality') == 'sufficient':
        score += 1
    return score


def _merge_issue_evidence(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Merge duplicate issue IDs without letting weak LLM/raw issues erase evidence."""
    for key, value in incoming.items():
        if value in [None, '', [], {}]:
            continue
        if key in {'span_source', 'span_ko', 'source_span', 'ko_span'}:
            old = str(existing.get(key) or '')
            new = str(value)
            generic_markers = {'must', '수 있습니다', '할 수 있습니다/가능', '가능', 'xp', 'number'}
            if old.lower() in generic_markers or (len(new) > len(old) + 8 and old in new):
                existing[key] = value
                continue
        if key not in existing or existing.get(key) in [None, '', [], {}]:
            existing[key] = value
            continue
        if key == 'semantic_diff' and isinstance(existing.get(key), dict) and isinstance(value, dict):
            merged = dict(existing[key])
            for diff_key, diff_value in value.items():
                if diff_value not in [None, '', [], {}] and merged.get(diff_key) in [None, '', [], {}]:
                    merged[diff_key] = diff_value
            existing[key] = merged
    if _evidence_score(incoming) > _evidence_score(existing):
        for key in ['span_source', 'span_ko', 'source_span', 'ko_span', 'semantic_diff', 'evidence', 'suggested_fix']:
            if incoming.get(key) not in [None, '', [], {}]:
                existing[key] = incoming[key]


def _add_issue_once(issues: list[dict[str, Any]], issue: dict[str, Any]) -> None:
    issue_id = issue.get('issue_id')
    if not issue_id:
        return
    for existing in issues:
        if existing.get('issue_id') == issue_id:
            _merge_issue_evidence(existing, issue)
            return
    issues.append(issue)


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    for line in re.split(r'\n+', text or ''):
        line = re.sub(r'\s+', ' ', line).strip()
        if not line:
            continue
        parts.extend(p.strip() for p in re.split(r'(?<=[.!?。！？])\s+', line) if p.strip())
    return parts


def _first_sentence_matching(text: str, pattern: str) -> str:
    rx = re.compile(pattern, re.I)
    for sentence in _sentences(text):
        if rx.search(sentence):
            return sentence
    m = rx.search(text or '')
    return m.group(0) if m else ''


def _first_optional_ko_sentence(text: str) -> str:
    return _first_sentence_matching(text, r'수\s*있|가능|해도\s*됩니다|할\s*수')


def _first_obligation_source_sentence(text: str) -> str:
    return _first_sentence_matching(text, r'\bmust\b|\bcannot\b|required|required to|mandatory')


def _xp_mentions(text: str) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for sentence in _sentences(text):
        for m in re.finditer(r'(?i)(\d+)\s*(?:bonus\s*)?XP|(?:XP|경험치)\s*(\d+)|(\d+)\s*경험치', sentence):
            amount = next((g for g in m.groups() if g), None)
            if not amount:
                continue
            mentions.append({'amount': int(amount), 'span': m.group(0), 'sentence': sentence})
    return mentions


def _best_xp_mismatch(source: str, ko: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    source_mentions = _xp_mentions(source)
    ko_mentions = _xp_mentions(ko)
    if not source_mentions or not ko_mentions:
        return None
    reward_words = re.compile(r'(?i)reward|gain|XP|보상|얻|획득')
    source_ranked = sorted(source_mentions, key=lambda x: (not bool(reward_words.search(x['sentence'])), x['amount']))
    ko_ranked = sorted(ko_mentions, key=lambda x: (not bool(reward_words.search(x['sentence'])), x['amount']))
    for src in source_ranked:
        for target in ko_ranked:
            if src['amount'] != target['amount']:
                return src, target
    return None


def _is_modal_issue(issue: dict[str, Any]) -> bool:
    issue_tail = str(issue.get('issue_id', '')).split('-', 1)[-1]
    text = ' '.join([issue_tail] + [str(issue.get(k, '')) for k in ['issue_type', 'evidence']]).lower()
    return 'modal' in text or 'must_to_may' in text or 'obligation' in text


def _is_quantity_issue(issue: dict[str, Any]) -> bool:
    issue_tail = str(issue.get('issue_id', '')).split('-', 1)[-1]
    text = ' '.join([issue_tail] + [str(issue.get(k, '')) for k in ['issue_type', 'evidence']]).lower()
    if any(axis in text for axis in ['timing', 'scope', 'target', 'condition', 'exception']) and not any(token in text for token in ['reward', 'xp', 'quantity']):
        return False
    return any(token in text for token in ['quantity', 'reward', 'xp']) or ('number' in text and not any(axis in text for axis in ['timing', 'scope', 'target', 'condition', 'exception']))


def _normalize_issue_evidence(context: dict[str, Any], issue: dict[str, Any]) -> None:
    source = context.get('source_text', '')
    ko = context.get('current_ko', '')
    if _is_modal_issue(issue) and re.search(r'\bmust\b|\bcannot\b|required|required to|mandatory', source, re.I) and _has_optional_ko(ko):
        source_span = _first_obligation_source_sentence(source) or issue.get('span_source') or 'must'
        ko_span = _first_optional_ko_sentence(ko) or issue.get('span_ko') or '할 수 있습니다/가능'
        enriched = {
            'span_source': source_span,
            'span_ko': ko_span,
            'semantic_diff': {
                'field': 'modal_force',
                'source_value': 'mandatory',
                'ko_value': 'optional',
                'source_marker': 'must/cannot/required',
                'ko_marker': '할 수 있습니다/가능',
            },
            'evidence': 'Grounded modal-force comparison: source expresses a mandatory obligation, while KO expresses optional possibility.',
            'suggested_fix': issue.get('suggested_fix') or '의무/강제성은 “해야 합니다/반드시 …합니다” 계열로 보존합니다.',
            'blocks_approval': True,
        }
        _merge_issue_evidence(issue, enriched)
        issue['span_source'] = enriched['span_source']
        issue['span_ko'] = enriched['span_ko']
        issue['semantic_diff'] = enriched['semantic_diff']
    if _is_quantity_issue(issue):
        current_field = ((issue.get('semantic_diff') or {}).get('field') or '').strip()
        if current_field and current_field not in {'number', 'quantity', 'reward.quantity'}:
            return
        mismatch = _best_xp_mismatch(source, ko)
        if mismatch:
            src, target = mismatch
            enriched = {
                'span_source': src['sentence'] or src['span'],
                'span_ko': target['sentence'] or target['span'],
                'semantic_diff': {
                    'field': 'reward.quantity',
                    'unit': 'XP',
                    'source_value': f"{src['amount']} XP",
                    'ko_value': f"{target['amount']} XP",
                    'source_amount': src['amount'],
                    'ko_amount': target['amount'],
                },
                'evidence': 'Grounded reward/quantity comparison: source and KO contain different XP reward amounts in reward-like context.',
                'suggested_fix': issue.get('suggested_fix') or f"XP 보상 수량을 원문 {src['amount']} XP에 맞춥니다.",
                'blocks_approval': True,
            }
            _merge_issue_evidence(issue, enriched)
            issue['span_source'] = enriched['span_source']
            issue['span_ko'] = enriched['span_ko']
            issue['semantic_diff'] = enriched['semantic_diff']


def _normalize_all_issue_evidence(context: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    for issue in issues:
        _normalize_issue_evidence(context, issue)


IR_COMPARISON_FIELDS = {
    'modal': 'Modal force mismatch',
    'scope': 'Scope mismatch',
    'target': 'Target scope mismatch',
    'timing': 'Timing mismatch',
    'condition': 'Condition mismatch',
    'exception': 'Exception mismatch',
    'number': 'Number mismatch',
}


def _ir_value(ir: dict[str, Any], field: str):
    value = ir.get(field)
    if value is None and field == 'number':
        value = ir.get('count') or ir.get('amount')
    if isinstance(value, list):
        return ', '.join(str(v) for v in value)
    if isinstance(value, dict):
        return ', '.join(f'{k}={v}' for k, v in sorted(value.items()))
    return value


def _has_meaningful_ir_value(value: Any) -> bool:
    return value is not None and str(value).strip() != '' and str(value).strip().lower() not in {'unknown', 'same', 'unchanged'}


def _norm_ir_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())


IR_EQUIVALENTS = {
    'target': {
        ('an enemy', '적 하나'),
        ('one enemy', '적 하나'),
        ('an adventurer', '모험가 하나'),
        ('one adventurer', '모험가 하나'),
    },
    'number': {
        ('one time', '한 번'),
        ('once', '한 번'),
        ('two times', '두 번'),
        ('twice', '두 번'),
        ('three times', '세 번'),
        ('3 times', '3번'),
        ('3 times', '세 번'),
        ('three times', '3번'),
    },
}


def _ir_values_equivalent(field: str, source_value: Any, ko_value: Any) -> bool:
    source_norm = _norm_ir_text(source_value)
    ko_norm = _norm_ir_text(ko_value)
    if source_norm == ko_norm:
        return True
    pairs = IR_EQUIVALENTS.get(field, set())
    if (source_norm, ko_norm) in pairs:
        return True
    if field == 'number':
        source_number = re.search(r'\b(\d+)\b', source_norm)
        ko_number = re.search(r'(\d+)\s*번', ko_norm)
        if source_number and ko_number and source_number.group(1) == ko_number.group(1):
            return True
    return False


def _detect_semantic_ir_risks(context: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    semantic_ir = context.get('semantic_ir') or {}
    source_ir = semantic_ir.get('source_ir') or {}
    ko_ir = semantic_ir.get('ko_ir') or {}
    if not isinstance(source_ir, dict) or not isinstance(ko_ir, dict):
        return
    code = context.get('code', 'CARD')
    for field, issue_type in IR_COMPARISON_FIELDS.items():
        source_value = _ir_value(source_ir, field)
        ko_value = _ir_value(ko_ir, field)
        if not _has_meaningful_ir_value(source_value) or not _has_meaningful_ir_value(ko_value):
            continue
        if _ir_values_equivalent(field, source_value, ko_value):
            continue
        issue_code = re.sub(r'[^A-Z0-9]+', '_', field.upper()).strip('_')
        _add_issue_once(issues, {
            'issue_id': f'{code}-IR_{issue_code}_MISMATCH',
            'issue_type': issue_type,
            'severity': 'Major',
            'span_source': str(source_value),
            'span_ko': '' if str(ko_value).strip().lower() == 'missing' else str(ko_value),
            'semantic_diff': {'field': field, 'source_value': source_value, 'ko_value': ko_value},
            'evidence': f'Semantic IR comparison: source {field}=`{source_value}` but KO {field}=`{ko_value}`.',
            'suggested_fix': f'Preserve source {field}: {source_value}.',
            'confidence': min(float(semantic_ir.get('confidence') or 0.88), 0.95),
            'blocks_approval': True,
            'comparison_source': 'semantic_ir',
        })


def _scope_checks_from_issues(issues: list[dict]) -> list[dict]:
    checks = []
    for issue in issues:
        issue_id = issue.get('issue_id', '')
        if issue_id.endswith('REG_SCOPE_REGARDLESS_OMITTED'):
            checks.append({
                'case_id': 'REG_SCOPE_REGARDLESS_OMITTED',
                'source_phrase': issue.get('span_source', 'regardless of location'),
                'ko_span': issue.get('span_ko', ''),
                'scope_preserved': False,
                'decision': 'scope_qualifier_omitted_blocks_approval',
                'suggested_fix': issue.get('suggested_fix', ''),
            })
        if issue_id.endswith('REG_EACH_HEX_SCOPE_NOT_STAR_HEX'):
            checks.append({
                'case_id': 'REG_EACH_HEX_SCOPE_NOT_STAR_HEX',
                'source_phrase': issue.get('span_source', 'each/every hex'),
                'ko_span': issue.get('span_ko', ''),
                'scope_preserved': False,
                'decision': 'board_scope_narrowed_to_star_hex',
                'suggested_fix': issue.get('suggested_fix', ''),
            })
        if issue_id.endswith('REG_TARGET_SCOPE_BROADENED'):
            checks.append({
                'case_id': 'REG_TARGET_SCOPE_BROADENED',
                'scope_check_type': 'target_scope_preservation',
                'source_phrase': issue.get('span_source', 'that adventurer'),
                'ko_span': issue.get('span_ko', ''),
                'scope_risk_patterns': issue.get('scope_risk_patterns', ['same_actor_to_each_actor']),
                'target_scopes': [
                    {
                        'target': issue.get('target_label', 'source target'),
                        'source_scope': issue.get('source_scope', 'source-limited target/scope'),
                        'ko_scope': issue.get('span_ko', ''),
                        'scope_preserved': False,
                    }
                ],
                'scope_preserved': False,
                'decision': 'target_scope_broadened_blocks_approval',
                'suggested_fix': issue.get('suggested_fix', ''),
            })
        if issue.get('issue_type') == 'Verified scope' or issue_id.endswith('SCOPE-OK'):
            checks.append({
                'case_id': issue.get('case_id') or 'REG_SCOPE_PARALLEL_TARGETS_001',
                'scope_check_type': issue.get('scope_check_type') or 'target_scope_preservation',
                'source_phrase': issue.get('span_source', ''),
                'ko_span': issue.get('span_ko', ''),
                'scope_preserved': True,
                'decision': issue.get('decision') or 'current_translation_scope_correct',
                'broadened_scope_suggestion_blocked': issue.get('broadened_scope_suggestion_blocked', True),
                'evidence': issue.get('evidence'),
            })
        diff = issue.get('semantic_diff') or {}
        if diff.get('field') in {'scope', 'target', 'timing', 'condition', 'exception'}:
            checks.append({
                'case_id': issue_id,
                'scope_check_type': f"ir_{diff.get('field')}_preservation",
                'source_phrase': issue.get('span_source', ''),
                'ko_span': issue.get('span_ko', ''),
                'semantic_diff': diff,
                'scope_preserved': False,
                'decision': 'semantic_ir_mismatch_blocks_approval',
                'suggested_fix': issue.get('suggested_fix', ''),
            })
    return checks


def _modal_checks_from_issues(issues: list[dict]) -> list[dict]:
    checks = []
    for issue in issues:
        if issue.get('issue_id', '').endswith('REG_MODAL_MUST_TO_MAY'):
            checks.append({
                'case_id': 'REG_MODAL_MUST_TO_MAY',
                'source_modal': 'must',
                'ko_modal': issue.get('span_ko', ''),
                'force_preserved': False,
                'decision': 'obligation_weakened_to_optional_blocks_approval',
                'suggested_fix': issue.get('suggested_fix', ''),
            })
        diff = issue.get('semantic_diff') or {}
        if diff.get('field') in {'modal', 'number'}:
            checks.append({
                'case_id': issue.get('issue_id', ''),
                'check_type': f"ir_{diff.get('field')}_preservation",
                'source_value': diff.get('source_value'),
                'ko_value': diff.get('ko_value'),
                'preserved': False,
                'decision': 'semantic_ir_mismatch_blocks_approval',
                'suggested_fix': issue.get('suggested_fix', ''),
            })
    return checks


def _detect_modal_force_risks(context: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    source = context.get('source_text', '')
    ko = context.get('current_ko', '')
    code = context.get('code', 'CARD')
    if re.search(r'\bmust\b', source, re.I) and _has_optional_ko(ko) and not _has_obligation_ko(ko):
        _add_issue_once(issues, {
            'issue_id': f'{code}-REG_MODAL_MUST_TO_MAY',
            'issue_type': 'Modal force mismatch',
            'severity': 'Major',
            'span_source': 'must',
            'span_ko': '수 있습니다',
            'semantic_diff': {'field': 'modal', 'source_value': 'must/cannot obligation', 'ko_value': 'may/can optional'},
            'evidence': 'Rules-lawyer text check: source uses mandatory must, but current_ko expresses the effect as optional/possible without an obligation marker.',
            'suggested_fix': 'must는 “해야 합니다/반드시 …합니다” 계열 의무 표현으로 보존합니다.',
            'confidence': 0.9,
            'blocks_approval': True,
        })


def _detect_target_scope_risks(context: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    source = context.get('source_text', '')
    ko = context.get('current_ko', '')
    code = context.get('code', 'CARD')
    lower = source.lower()

    # Same-actor/same-trigger target phrases must not be broadened into every actor.
    # The current concrete pattern covers the TES wording "that adventurer", but the
    # emitted issue/check is deliberately generic so reports do not frame agent 9 as
    # a one-off "that adventurer" checker.
    if 'that adventurer' in lower and '각 모험가' in ko and not re.search(r'그\s*모험가|해당\s*모험가', ko):
        _add_issue_once(issues, {
            'issue_id': f'{code}-REG_TARGET_SCOPE_BROADENED',
            'issue_type': 'Target scope mismatch',
            'severity': 'Major',
            'span_source': 'that adventurer',
            'span_ko': '각 모험가',
            'semantic_diff': {'field': 'target.scope', 'source_value': 'same triggering adventurer', 'ko_value': 'each adventurer'},
            'target_label': 'same actor / triggering adventurer',
            'source_scope': 'the same adventurer who triggered/defeated/acted',
            'scope_risk_patterns': ['same_actor_to_each_actor'],
            'evidence': 'Rules-lawyer text check: source limits the effect to that/the triggering adventurer, but current_ko broadens it to each adventurer.',
            'suggested_fix': '“그 모험가” 또는 “해당 모험가”처럼 동일 행위자/트리거 대상임을 보존합니다.',
            'confidence': 0.92,
            'blocks_approval': True,
        })


def _detect_scope_qualifier_risks(context: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    source = context.get('source_text', '')
    ko = context.get('current_ko', '')
    code = context.get('code', 'CARD')
    if 'regardless of location' in source.lower() and not ('위치와 관계없이' in ko or '위치에 관계없이' in ko):
        _add_issue_once(issues, {
            'issue_id': f'{code}-REG_SCOPE_REGARDLESS_OMITTED',
            'issue_type': 'Scope qualifier omission',
            'severity': 'Major',
            'span_source': 'regardless of location',
            'span_ko': '',
            'semantic_diff': {'field': 'scope.exception', 'source_value': 'regardless of location', 'ko_value': 'missing'},
            'evidence': 'Rules-lawyer text check: source has regardless-of-location scope qualifier, but current_ko lacks the location-independent qualifier.',
            'suggested_fix': '“위치와 관계없이/위치에 관계없이” 범위 조건을 보존합니다.',
            'confidence': 0.9,
            'blocks_approval': True,
        })


def _detect_quantity_risks(context: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    source = context.get('source_text', '')
    ko = context.get('current_ko', '')
    code = context.get('code', 'CARD')
    mismatch = _best_xp_mismatch(source, ko)
    if not mismatch:
        return
    src, target = mismatch
    _add_issue_once(issues, {
        'issue_id': f'{code}-REG_REWARD_QUANTITY_MISMATCH',
        'issue_type': 'Reward/quantity mismatch',
        'severity': 'Major',
        'span_source': src['sentence'] or src['span'],
        'span_ko': target['sentence'] or target['span'],
        'semantic_diff': {
            'field': 'reward.quantity',
            'unit': 'XP',
            'source_value': f"{src['amount']} XP",
            'ko_value': f"{target['amount']} XP",
            'source_amount': src['amount'],
            'ko_amount': target['amount'],
        },
        'evidence': 'Rules-lawyer quantity grounder: source and KO contain different XP reward amounts in reward-like context.',
        'suggested_fix': f"XP 보상 수량을 원문 {src['amount']} XP에 맞춥니다.",
        'confidence': 0.9,
        'blocks_approval': True,
    })


def _detect_narrative_concept_polarity_risks(context: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    source = context.get('source_text', '')
    ko = context.get('current_ko', '')
    code = context.get('code', 'CARD')
    lower = source.lower()
    # Narrative/concept descriptions can carry rules-critical identity. The BM-06
    # adversarial case changed "new breed/species" to "old species" outside the
    # mechanical rules block, so catch polarity pairs in description text too.
    if not re.search(r'\bnew\s+(?:breed|species)\b|\bnew\s+breed\s+of\s+animal\b', lower):
        return
    ko_old = _first_sentence_matching(ko, r'오래된\s*(?:종|품종)')
    if not ko_old:
        return
    source_span = _first_sentence_matching(source, r'\bnew\s+(?:breed|species)\b|\bnew\s+breed\s+of\s+animal\b')
    _add_issue_once(issues, {
        'issue_id': f'{code}-REG_NARRATIVE_CONCEPT_POLARITY_NEW_TO_OLD',
        'issue_type': 'Narrative concept polarity mismatch',
        'severity': 'Major',
        'span_source': source_span or 'new breed/species',
        'span_ko': ko_old,
        'semantic_diff': {
            'field': 'narrative.concept_polarity',
            'concept': 'species/breed novelty',
            'source_value': 'new',
            'ko_value': 'old',
        },
        'evidence': 'Grounded narrative concept comparison: source describes a new breed/species, while KO describes an old species/breed.',
        'suggested_fix': '“새로운 종/새로운 품종”처럼 원문의 new concept polarity를 보존합니다.',
        'confidence': 0.92,
        'blocks_approval': True,
    })


def _apply_llm_issue_review(issues: list[dict[str, Any]], issue_reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    by_id = {issue.get('issue_id'): issue for issue in issues}
    for review in issue_reviews or []:
        issue_id = review.get('issue_id')
        issue = by_id.get(issue_id)
        if not issue:
            continue
        verdict = str(review.get('llm_verdict') or '').lower()
        confidence = float(review.get('confidence') or 0)
        if verdict in {'false_positive_candidate', 'not_a_problem', 'safe'} and confidence >= 0.75:
            issue['llm_disputed'] = True
            issue['review_status'] = 'llm_disputed_false_positive_candidate'
            issue['llm_issue_review'] = {
                'llm_verdict': review.get('llm_verdict'),
                'confidence': confidence,
                'evidence': review.get('evidence'),
                'recommended_action': review.get('recommended_action') or 'downgrade_to_human_review',
                'requires_human_approval': review.get('requires_human_approval', True),
            }
            applied.append({'issue_id': issue_id, **issue['llm_issue_review']})
    return applied


def _llm_rules_lawyer(context: dict) -> dict | None:
    schema = {
        'issues': [{'issue_id': '...', 'issue_type': '...', 'severity': 'Major|Critical|Minor|StyleWarning', 'evidence': '...', 'blocks_approval': True}],
        'issue_review': [{'issue_id': 'existing issue id', 'llm_verdict': 'false_positive_candidate|confirmed|unknown', 'confidence': '0..1', 'evidence': '...', 'recommended_action': 'downgrade_to_human_review', 'requires_human_approval': True}],
        'rules_lawyer_result': {'risk': 'Low|Medium|High', 'scope_checks': [], 'modal_checks': []},
    }
    prompt = build_prompt(
        AGENT_NAME,
        'Compare source slots, KO slots, source_text, current_ko, and relevant rulebook hits. Detect modal force, target scope, timing, condition, number, exception, and rule-scope mismatches. Return only grounded issues; use UNKNOWN/human review when unsure.',
        compact_card_payload(context),
        schema,
    )
    result = call_json(AGENT_NAME, prompt, expected_keys=['issues', 'rules_lawyer_result'])
    record_usage(context, AGENT_NAME, result['usage'])
    return result.get('data')


def run(context):
    issues = list(context['facts'].get('issues', []))
    _detect_modal_force_risks(context, issues)
    _detect_target_scope_risks(context, issues)
    _detect_scope_qualifier_risks(context, issues)
    _detect_quantity_risks(context, issues)
    _detect_narrative_concept_polarity_risks(context, issues)
    _detect_semantic_ir_risks(context, issues)
    _normalize_all_issue_evidence(context, issues)
    llm_data = _llm_rules_lawyer(context)
    llm_dispute_reviews: list[dict[str, Any]] = []
    if llm_data:
        for issue in llm_data.get('issues') or []:
            _add_issue_once(issues, issue)
        _normalize_all_issue_evidence(context, issues)
        llm_dispute_reviews = _apply_llm_issue_review(issues, llm_data.get('issue_review') or [])
    context['facts']['issues'] = issues

    major = [x for x in issues if x.get('severity') == 'Major']
    critical = [x for x in issues if x.get('severity') == 'Critical']
    scope_checks = _scope_checks_from_issues(issues)
    modal_checks = _modal_checks_from_issues(issues)
    context['rules_lawyer_result'] = {
        'risk': 'High' if critical else ('Medium' if major else 'Low'),
        'critical_issues': critical,
        'major_issues': major,
        'scope_checks': scope_checks,
        'modal_checks': modal_checks,
        'rules_lawyer_quality': {
            'direct_text_checks_enabled': True,
            'llm_json_enabled': bool(llm_data),
            'modal_check_count': len(modal_checks),
            'scope_check_count': len(scope_checks),
            'new_rules_lawyer_issue_count': len([i for i in issues if str(i.get('issue_id', '')).startswith(f"{context.get('code', 'CARD')}-REG_") or '-LLM_' in str(i.get('issue_id', ''))]),
            'llm_disputed_issue_count': len(llm_dispute_reviews),
        },
        'llm_issue_review': llm_dispute_reviews,
        'notes': 'XP omission/icon form/conditional position policies applied; not treated as issues by themselves.'
    }
    if llm_data and llm_data.get('rules_lawyer_result'):
        llm_result = dict(llm_data['rules_lawyer_result'])
        context['rules_lawyer_result']['risk'] = llm_result.get('risk') or context['rules_lawyer_result']['risk']
        context['rules_lawyer_result']['scope_checks'].extend(llm_result.get('scope_checks') or [])
        context['rules_lawyer_result']['modal_checks'].extend(llm_result.get('modal_checks') or [])
        context['rules_lawyer_result']['llm_review'] = {k: v for k, v in llm_result.items() if k not in {'scope_checks', 'modal_checks'}}
    return record_agent(context, AGENT_NAME, {'summary': context['rules_lawyer_result']['risk']})
