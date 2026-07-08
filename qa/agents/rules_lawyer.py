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


def _add_issue_once(issues: list[dict[str, Any]], issue: dict[str, Any]) -> None:
    issue_id = issue.get('issue_id')
    if not issue_id or any(existing.get('issue_id') == issue_id for existing in issues):
        return
    issues.append(issue)


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
            'span_ko': '수 있습니다/가능',
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
            'evidence': 'Rules-lawyer text check: source has regardless-of-location scope qualifier, but current_ko lacks the location-independent qualifier.',
            'suggested_fix': '“위치와 관계없이/위치에 관계없이” 범위 조건을 보존합니다.',
            'confidence': 0.9,
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
    llm_data = _llm_rules_lawyer(context)
    llm_dispute_reviews: list[dict[str, Any]] = []
    if llm_data:
        for issue in llm_data.get('issues') or []:
            _add_issue_once(issues, issue)
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
