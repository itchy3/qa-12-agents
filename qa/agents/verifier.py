from __future__ import annotations

import re

from .shared import record_agent

AGENT_NAME = 'verifier'

BLOCKING_MEANING_TYPES = (
    'modal',
    'scope',
    'target',
    'choice type/icon',
    'added objective',
    'unresolved',
)


def _issue_text(issue: dict) -> str:
    return (issue.get('issue_type', '') + ' ' + issue.get('issue_id', '') + ' ' + issue.get('evidence', '')).lower()


def _is_blocking_issue(issue: dict) -> bool:
    if issue.get('severity') == 'Critical':
        return True
    if issue.get('severity') != 'Major':
        return False
    text = _issue_text(issue)
    return any(kind in text for kind in BLOCKING_MEANING_TYPES) or bool(issue.get('blocks_approval'))


def _is_meaning_issue(issue: dict) -> bool:
    if issue.get('severity') not in {'Critical', 'Major'}:
        return False
    text = _issue_text(issue)
    return any(kind in text for kind in BLOCKING_MEANING_TYPES)


def _markup_tokens(text: str) -> set[str]:
    return set(re.findall(r'\[\[[^\]]+\]\]|\[[^\]]+\]', text or ''))


def _markup_pass(context: dict, im_not_ai: dict) -> bool:
    for check in im_not_ai.get('checks', []):
        if check.get('check_id') == 'markup_tokens_preserved' and check.get('status') == 'fail':
            return False
    comparison = context.get('translation_comparison', {})
    if comparison.get('winner') != 'polished_ko':
        return True
    current = context.get('current_ko', '')
    final = context.get('final_translation', '')
    return _markup_tokens(current) == _markup_tokens(final)


def run(context):
    issues = context['facts'].get('issues', [])
    has_fix = bool(context.get('suggested_ko')) and context.get('suggested_ko') not in ['현행 유지 가능.', '사람 검토 필요']
    ontology_status = context.get('ontology_result', {}).get('status')
    patch_status = context.get('patch_result', {}).get('status')
    im_not_ai = context.get('im_not_ai_result', {})
    rules_risk = context.get('rules_lawyer_result', {}).get('risk', 'Low')

    blocking_issues = [issue for issue in issues if _is_blocking_issue(issue)]
    meaning_issues = [issue for issue in issues if _is_meaning_issue(issue)]
    im_not_ai_pass = im_not_ai.get('status') not in {'rejected_rule_or_meaning_risk'}
    rules_lawyer_pass = rules_risk not in {'Medium', 'High'} and not any(
        any(kind in _issue_text(issue) for kind in ('modal', 'scope', 'target'))
        and issue.get('severity') in {'Critical', 'Major'}
        for issue in issues
    )
    markup_pass = _markup_pass(context, im_not_ai)
    meaning_preserved = (
        not meaning_issues
        and rules_lawyer_pass
        and im_not_ai_pass
        and im_not_ai.get('meaning_structure_preserved', True)
        and markup_pass
    )

    context['self_verification'] = {
        'meaning_preserved': meaning_preserved,
        'rules_lawyer_pass': rules_lawyer_pass,
        'blocking_issue_pass': not blocking_issues,
        'blocking_issue_count': len(blocking_issues),
        'blocking_issue_ids': [issue.get('issue_id') for issue in blocking_issues],
        'meaning_issue_ids': [issue.get('issue_id') for issue in meaning_issues],
        'terminology_pass': not any(x.get('severity') == 'Major' and 'Terminology' in x.get('issue_type', '') for x in issues),
        'syntax_pattern_pass': has_fix or not any(x.get('severity') == 'Major' and 'Syntax' in x.get('issue_type', '') for x in issues),
        'cross_card_consistency_pass': context.get('cross_card_consistency', {}).get('status') != 'fail' or any(x.get('severity') == 'Major' for x in issues),
        'lore_ontology_pass': ontology_status not in ['warn', 'fail'] and not any('Lore' in x.get('issue_type', '') and x.get('severity') in ['Critical', 'Major'] for x in issues),
        'patch_check_pass': patch_status not in ['warn', 'fail'] and not context.get('patch_result', {}).get('violations'),
        'im_not_ai_pass': im_not_ai_pass,
        'im_not_ai_meaning_structure_preserved': im_not_ai.get('meaning_structure_preserved', True),
        'candidate_decision': im_not_ai.get('candidate_decision') or context.get('translation_comparison', {}).get('candidate_decision'),
        'markup_pass': markup_pass,
        'issue_count': len(issues),
        # Legacy compatibility: verifier itself does not create issues; issue_count/blocking_issue_ids carry the useful signal.
        'new_issue_created': False,
    }
    failed = [k for k, v in context['self_verification'].items() if k.endswith('_pass') and v is False]
    summary = 'self-verification passed' if not failed and meaning_preserved else f"self-verification failed: {', '.join(failed) or 'meaning_preserved'}"
    return record_agent(context, AGENT_NAME, {'summary': summary})
