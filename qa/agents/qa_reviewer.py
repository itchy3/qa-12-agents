from __future__ import annotations
from .shared import record_agent
AGENT_NAME = 'qa-reviewer'


def classify_learning_route(issue: dict) -> dict:
    issue_id = issue.get('issue_id', '')
    issue_type = issue.get('issue_type', '')
    text = (issue_id + ' ' + issue_type + ' ' + issue.get('evidence', '')).lower()
    if 'unstable' in text and 'icon' in text:
        return {'route': 'parser_rule', 'test_id': 'REG_UNSTABLE_ICON_MISMATCH', 'proposal_type': 'parser_rule', 'proposal': 'Detect source ICON_Unstable_* vs KO non-unstable choiceType/icon mismatch.'}
    if 'each hex' in text or 'every hex' in text or 'star' in text or 'board-location' in text:
        return {'route': 'parser_rule', 'test_id': 'REG_EACH_HEX_SCOPE_NOT_STAR_HEX', 'proposal_type': 'parser_rule', 'proposal': 'Detect source each/every hex translated as star/special hex scope narrowing.'}
    if 'must' in text and ('modal' in text or 'may' in text or 'optional' in text):
        return {'route': 'parser_rule', 'test_id': 'REG_MODAL_MUST_TO_MAY', 'proposal_type': 'parser_rule', 'proposal': 'Detect source must obligation weakened into KO optional may/can wording.'}
    if 'regardless of location' in text or 'scope qualifier omission' in text:
        return {'route': 'parser_rule', 'test_id': 'REG_SCOPE_REGARDLESS_OMITTED', 'proposal_type': 'parser_rule', 'proposal': 'Detect omission of regardless-of-location scope qualifiers in KO.'}
    if 'target_scope' in text or 'target scope' in text or 'same_actor_to_each_actor' in text or 'same actor' in text:
        return {'route': 'parser_rule', 'test_id': 'REG_TARGET_SCOPE_BROADENED', 'proposal_type': 'parser_rule', 'proposal': 'Detect source same-actor/triggering-target references broadened into each/all targets in KO.'}
    if 'metadata' in text or 'frontmatter' in text or 'code mismatch' in text:
        return {'route': 'regression_candidate', 'test_id': 'REG_FRONTMATTER_CODE_TITLE_MISMATCH', 'proposal_type': 'source_pdf_check', 'proposal': 'Detect filename/frontmatter Code/title mismatch before card matching.'}
    if 'objective not in source' in text:
        return {'route': 'source_pdf_check', 'test_id': 'REG_ADDED_OBJECTIVE_REQUIRES_SOURCE_CHECK', 'proposal_type': 'source_pdf_check', 'proposal': 'Require PDF/source confirmation when KO adds battleObjective missing from extracted source.'}
    if 'terminology' in text or 'term' in text:
        return {'route': 'glossary_candidate', 'test_id': None, 'proposal_type': 'glossary_candidate', 'proposal': issue.get('suggested_fix') or 'Review terminology candidate.'}
    if 'syntax' in text or 'format' in text or 'particle' in text:
        return {'route': 'syntax_rule', 'test_id': None, 'proposal_type': 'syntax_rule', 'proposal': issue.get('suggested_fix') or 'Review syntax/style candidate.'}
    if 'unresolved' in text:
        return {'route': 'parser_rule', 'test_id': 'REG_UNRESOLVED_PATTERN_REQUIRES_SLOT_RULE', 'proposal_type': 'parser_rule', 'proposal': 'Promote unresolved source pattern to parser slot rule after human review.'}
    return {'route': 'card_fix', 'test_id': None, 'proposal_type': 'card_fix', 'proposal': issue.get('suggested_fix') or 'Review card-specific fix.'}


def run(context):
    issues = context['facts'].get('issues', [])
    requires = any(x.get('severity') in ['Critical', 'Major'] for x in issues)
    context['issues'] = issues
    blocking_types = ('terminology', 'modal', 'scope', 'target', 'choice type/icon', 'added objective', 'unresolved')
    has_critical = any(x.get('severity') == 'Critical' for x in issues)
    has_blocking_major = any(
        x.get('severity') == 'Major'
        and any(t in (x.get('issue_type', '') + ' ' + x.get('issue_id', '')).lower() for t in blocking_types)
        for x in issues
    )
    if has_critical:
        context['score'] = min(context['facts'].get('score', 0), 70)
        context['verdict'] = 'Needs revision'
    elif has_blocking_major:
        context['score'] = min(context['facts'].get('score', 82), 82)
        context['verdict'] = 'Needs revision'
    else:
        context['score'] = context['facts']['score']
        context['verdict'] = context['facts']['verdict']
    context['requires_human_review'] = requires
    self_verification = context.get('self_verification') or {}
    blocking_issue_ids = self_verification.get('blocking_issue_ids') or [
        issue.get('issue_id') for issue in issues
        if issue.get('severity') in ['Critical', 'Major']
    ]
    self_verification_warnings = [
        key for key, value in self_verification.items()
        if key.endswith('_pass') and value is False
    ]
    candidate_decision = self_verification.get('candidate_decision') or context.get('translation_comparison', {}).get('candidate_decision')
    if has_critical:
        final_decision_basis = 'critical_issue'
    elif has_blocking_major or self_verification.get('blocking_issue_pass') is False:
        final_decision_basis = 'blocking_issue'
    elif candidate_decision == 'rejected_keep_current' and 'im_not_ai_pass' in self_verification_warnings and context['verdict'] == 'Pass':
        final_decision_basis = 'safe_current_with_rejected_candidate_warning'
    elif self_verification_warnings or self_verification.get('meaning_preserved') is False:
        final_decision_basis = 'self_verification_warning'
    elif requires:
        final_decision_basis = 'nonblocking_issue_requires_review'
    else:
        final_decision_basis = 'all_gates_passed'
    proposals = []
    for issue in issues:
        route = classify_learning_route(issue)
        proposals.append({
            'type': route['proposal_type'],
            'route': route['route'],
            'test_id': route['test_id'],
            'issue_id': issue['issue_id'],
            'proposal': route['proposal'],
            'card_fix': issue.get('suggested_fix'),
            'source_item_id': context['item_id'],
            'requires_human_approval': True,
        })
    for style_check in context.get('style_pattern_checks', []):
        if style_check.get('candidate_type') != 'review_candidate' or not style_check.get('status_requires_human_approval'):
            continue
        proposals.append({
            'type': 'Style Pattern Review',
            'route': 'style_pattern_review',
            'test_id': None,
            'issue_id': style_check.get('issue_id'),
            'proposal': style_check.get('review_reason') or 'Review observed style-pattern mismatch.',
            'card_fix': f"Use observed marker `{style_check.get('dominant_ko')}` for `{style_check.get('source_pattern')}` if appropriate.",
            'source_item_id': context['item_id'],
            'requires_human_approval': True,
            'source_pattern': style_check.get('source_pattern'),
            'dominant_ko': style_check.get('dominant_ko'),
            'current_ko_marker': style_check.get('current_ko_marker'),
            'risk_type': style_check.get('risk_type'),
            'evidence_strength': style_check.get('evidence_strength'),
            'evidence_count': style_check.get('evidence_count'),
        })
    for check in context.get('cross_card_consistency', {}).get('checks', []):
        if check.get('check_type') == 'syntax_corpus_consistency' and check.get('status') == 'warn' and check.get('requires_human_review'):
            proposals.append({
                'type': 'Syntax Corpus Pattern Review',
                'route': 'syntax_corpus_review',
                'test_id': None,
                'issue_id': check.get('issue_id'),
                'proposal': f"Current KO template `{check.get('current_template')}` differs from corpus dominant `{check.get('dominant_template')}` for `{check.get('source_pattern')}`.",
                'card_fix': 'Review whether to align this card with the corpus-dominant syntax template; do not auto-apply.',
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'source_pattern': check.get('source_pattern'),
                'current_template': check.get('current_template'),
                'dominant_template': check.get('dominant_template'),
                'evidence_count': check.get('evidence_count'),
                'confidence': check.get('confidence'),
                'meaning_equivalent': check.get('meaning_equivalent'),
            })
            continue
        if check.get('check_type') not in {'choice_icon_consistency', 'term_consistency', 'syntax_structure_consistency'}:
            continue
        if check.get('status') != 'warn' or not check.get('requires_human_review'):
            continue
        if check.get('evidence_strength') in {'singleton', 'no_clear_dominant'} and check.get('check_type') != 'choice_icon_consistency':
            continue
        proposals.append({
            'type': 'Cross-Card Consistency Review',
            'route': 'cross_card_consistency_review',
            'test_id': None,
            'issue_id': check.get('issue_id') or f"{context['item_id']}-{check.get('check_type')}",
            'proposal': f"Review cross-card consistency signal `{check.get('check_type')}` for {context['item_id']}.",
            'card_fix': 'Review whether this card should align with the observed batch/corpus variant; do not auto-apply.',
            'source_item_id': context['item_id'],
            'requires_human_approval': True,
            'check_type': check.get('check_type'),
            'term': check.get('term'),
            'source_icon': check.get('source_icon'),
            'ko_icon': check.get('ko_icon'),
            'dominant_ko': check.get('dominant_ko'),
            'current_template': check.get('current_template'),
            'dominant_template': check.get('dominant_template'),
            'alignment_quality': check.get('alignment_quality'),
            'evidence_count': check.get('evidence_count') or check.get('source_card_count'),
            'confidence': check.get('confidence'),
            'evidence_strength': check.get('evidence_strength'),
        })
    for check in context.get('ontology_result', {}).get('checks', []):
        if not check.get('requires_human_review'):
            continue
        proposals.append({
            'type': 'Lore Ontology Review',
            'route': 'lore_ontology_review',
            'test_id': None,
            'issue_id': f"{context['item_id']}-{check.get('entity_id')}",
            'proposal': check.get('suggested_action') or f"Review TES lore entity `{check.get('canonical_en')}` against ontology.",
            'card_fix': f"Use `{check.get('expected_ko')}` for `{check.get('canonical_en')}` if this ontology entry is approved for the project.",
            'source_item_id': context['item_id'],
            'requires_human_approval': True,
            'entity_id': check.get('entity_id'),
            'entity_type': check.get('entity_type'),
            'canonical_en': check.get('canonical_en'),
            'expected_ko': check.get('expected_ko'),
            'observed_ko': check.get('observed_ko'),
            'decision': check.get('decision'),
            'confidence': check.get('confidence'),
        })
    for check in context.get('patch_result', {}).get('patch_checks', []):
        if not check.get('requires_human_review'):
            continue
        proposals.append({
            'type': 'Patch Note Review',
            'route': 'patch_note_review',
            'test_id': None,
            'issue_id': f"{context['item_id']}-{check.get('patch_id')}",
            'proposal': check.get('note') or f"Review whether patch/improvement `{check.get('patch_id')}` is reflected in current KO.",
            'card_fix': f"Apply expected KO form(s): {', '.join(check.get('expected_ko') or [])}" if check.get('expected_ko') else 'Review patch note assertion; expected_ko missing.',
            'source_item_id': context['item_id'],
            'requires_human_approval': True,
            'patch_id': check.get('patch_id'),
            'decision': check.get('decision'),
            'expected_ko': check.get('expected_ko'),
            'forbidden_ko': check.get('forbidden_ko'),
            'evidence': check.get('evidence'),
        })
    for glossary_proposal in context.get('terminology_result', {}).get('glossary_review_proposals', []):
        proposals.append({
            'type': 'glossary_review',
            'route': 'glossary_review',
            'test_id': None,
            'issue_id': glossary_proposal.get('issue_id'),
            'proposal': glossary_proposal.get('suggested_action'),
            'card_fix': None,
            'source_item_id': context['item_id'],
            'requires_human_approval': True,
            'source_term': glossary_proposal.get('source_term'),
            'current_ko_excerpt': glossary_proposal.get('current_ko_excerpt'),
            'reason': glossary_proposal.get('reason'),
            'candidate_ko': glossary_proposal.get('candidate_ko'),
        })
    context['learning_update_proposal'] = proposals
    context['qa_reviewer_result'] = {
        'final_verdict': context['verdict'],
        'final_score': context['score'],
        'final_decision_basis': final_decision_basis,
        'requires_human_review': context['requires_human_review'],
        'blocking_issue_ids': blocking_issue_ids,
        'self_verification_warnings': self_verification_warnings,
        'candidate_decision': candidate_decision,
        'proposal_count': len(proposals),
        'proposal_routes': sorted({p.get('route') for p in proposals if p.get('route')}),
    }
    return record_agent(context, AGENT_NAME, {'summary': f"{context['verdict']} score={context['score']} proposals={len(proposals)}"})
