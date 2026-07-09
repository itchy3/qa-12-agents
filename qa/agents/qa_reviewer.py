from __future__ import annotations
from .shared import record_agent
from llm_client import build_prompt, call_json, compact_card_payload, record_usage
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


def _source_quality_icon_crawl_gap(context: dict) -> bool:
    for row in context.get('source_quality_issues') or []:
        text = ' '.join(str(row.get(k) or '') for k in ['issue_id', 'issue_type', 'evidence']).lower()
        if 'source_icon_crawl_missing' in text or ('source crawl' in text and 'icon' in text):
            return True
    return False


def _classify_learning_route_for_context(context: dict, issue: dict) -> dict:
    route = classify_learning_route(issue)
    text = ' '.join(str(issue.get(k) or '') for k in ['issue_id', 'issue_type', 'span_source', 'span_ko', 'evidence']).lower()
    if route.get('test_id') == 'REG_EACH_HEX_SCOPE_NOT_STAR_HEX' and _source_quality_icon_crawl_gap(context):
        return {
            'route': 'source_pdf_check',
            'test_id': None,
            'proposal_type': 'source_pdf_check',
            'proposal': 'KO has a board/icon marker while source crawl only says each/every hex; verify source PDF/icon extraction before adding a scope-narrowing parser regression.',
        }
    if route.get('test_id') == 'REG_EACH_HEX_SCOPE_NOT_STAR_HEX' and 'terminology' in text:
        return {
            'route': 'glossary_candidate',
            'test_id': None,
            'proposal_type': 'glossary_candidate',
            'proposal': issue.get('suggested_fix') or 'Review terminology candidate before treating this as scope narrowing.',
        }
    return route


def _llm_qa_reviewer(context: dict) -> dict | None:
    schema = {
        'qa_reviewer_patch': {
            'llm_review_summary': 'short final synthesis',
            'final_decision_basis': 'optional basis string',
            'needs_human_review': 'optional boolean',
        },
        'proposal_route_overrides': [
            {
                'issue_id': 'proposal/issue id to reroute',
                'route': 'source_pdf_check|parser_rule|glossary_review|glossary_candidate|syntax_style_review|lore_ontology_review|card_fix|false_positive_review|issue_evidence_review|semantic_ir_review|cross_card_consistency_review|quantity_term_pattern_review|patch_note_review|meta_harness_review|regression_candidate',
                'test_id': 'regression id or null',
                'type': 'optional replacement proposal type',
                'proposal': 'grounded route rationale/proposal',
                'reason': 'why this route is better than the py route hint',
                'confidence': 0.0,
            }
        ],
    }
    payload = compact_card_payload(context)
    payload['learning_update_proposal'] = context.get('learning_update_proposal') or []
    payload['source_quality_issues'] = context.get('source_quality_issues') or []
    payload['route_task_rules'] = [
        'Treat existing proposal routes/test_ids as Python hints, not authority.',
        'If source_quality_issues indicate source crawl/OCR/icon extraction uncertainty, prefer source_pdf_check over parser regression.',
        'Do not create memory/regression routes from weak evidence; use human-review/source check routes instead.',
        'Only override routes when the provided issue/proposal evidence supports the change.',
    ]
    prompt = build_prompt(
        AGENT_NAME,
        'Synthesize final QA posture and audit learning proposal routes. Do not invent new facts. Existing Python-classified routes are hints only: reroute proposals when evidence shows a source/PDF/OCR gap, glossary/ontology issue, syntax/style review, false-positive review, or card-specific fix is more appropriate than a parser regression. Return grounded JSON only.',
        payload,
        schema,
    )
    result = call_json(AGENT_NAME, prompt, expected_keys=['qa_reviewer_patch'])
    record_usage(context, AGENT_NAME, result['usage'])
    return result.get('data')


def _apply_llm_route_overrides(context: dict, llm_data: dict | None) -> list[dict]:
    proposals = context.get('learning_update_proposal') or []
    overrides = []
    if llm_data:
        overrides = [x for x in llm_data.get('proposal_route_overrides') or [] if isinstance(x, dict)]
    if not overrides:
        return []
    by_issue = {str(o.get('issue_id') or ''): o for o in overrides if o.get('issue_id')}
    applied = []
    for prop in proposals:
        override = by_issue.get(str(prop.get('issue_id') or ''))
        if not override:
            continue
        before = {k: prop.get(k) for k in ['type', 'route', 'test_id', 'proposal']}
        for key in ['type', 'route', 'test_id', 'proposal']:
            if key in override:
                prop[key] = override.get(key)
        prop['llm_route_override'] = {
            'source': AGENT_NAME,
            'before': before,
            'reason': override.get('reason'),
            'confidence': override.get('confidence'),
        }
        prop['requires_human_approval'] = True
        applied.append({
            'issue_id': prop.get('issue_id'),
            'before': before,
            'after': {k: prop.get(k) for k in ['type', 'route', 'test_id', 'proposal']},
            'reason': override.get('reason'),
            'confidence': override.get('confidence'),
        })
    return applied


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
    llm_disputed_blocking_issue_ids = self_verification.get('llm_disputed_blocking_issue_ids') or [
        issue.get('issue_id') for issue in issues
        if issue.get('severity') in ['Critical', 'Major'] and issue.get('llm_disputed')
    ]
    weak_evidence_blocking_issue_ids = self_verification.get('weak_evidence_blocking_issue_ids') or [
        issue.get('issue_id') for issue in issues
        if issue.get('severity') in ['Critical', 'Major'] and issue.get('review_status') == 'weak_evidence_human_review'
    ]
    llm_resolved_unresolved_issue_ids = self_verification.get('llm_resolved_unresolved_issue_ids') or [
        issue.get('issue_id') for issue in issues
        if issue.get('severity') in ['Critical', 'Major'] and issue.get('review_status') == 'llm_resolved_unresolved_human_review'
    ]
    undisputed_blocking_issue_ids = self_verification.get('undisputed_blocking_issue_ids') or [
        issue.get('issue_id') for issue in issues
        if issue.get('severity') in ['Critical', 'Major'] and not issue.get('llm_disputed') and issue.get('review_status') not in {'weak_evidence_human_review', 'llm_resolved_unresolved_human_review'}
    ]
    if has_blocking_major and not has_critical and not undisputed_blocking_issue_ids and (llm_disputed_blocking_issue_ids or weak_evidence_blocking_issue_ids or llm_resolved_unresolved_issue_ids):
        context['score'] = min(max(context['facts'].get('score', 85), 85), 89)
        context['verdict'] = 'Human review'
        context['requires_human_review'] = True
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
    elif has_blocking_major and llm_resolved_unresolved_issue_ids and not undisputed_blocking_issue_ids:
        final_decision_basis = 'llm_resolved_unresolved_human_review'
    elif has_blocking_major and weak_evidence_blocking_issue_ids and not undisputed_blocking_issue_ids:
        final_decision_basis = 'weak_evidence_blocker_human_review'
    elif has_blocking_major and llm_disputed_blocking_issue_ids and not undisputed_blocking_issue_ids:
        final_decision_basis = 'llm_disputed_blocker_human_review'
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
        if issue.get('review_status') == 'llm_resolved_unresolved_human_review':
            proposals.append({
                'type': 'Semantic IR Review',
                'route': 'semantic_ir_review',
                'test_id': None,
                'issue_id': issue['issue_id'],
                'proposal': 'LLM semantic IR resolved an unresolved parser pattern; human approval required before suppressing this unresolved class.',
                'card_fix': None,
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'semantic_ir_status': context.get('semantic_ir', {}).get('status'),
            })
            continue
        if issue.get('review_status') == 'weak_evidence_human_review':
            weak_text = ' '.join(str(issue.get(k) or '') for k in ['issue_id', 'issue_type', 'evidence']).lower()
            source_check = 'objective not in source' in weak_text or 'metadata' in weak_text or 'frontmatter' in weak_text or 'source crawl' in weak_text
            proposals.append({
                'type': 'source_pdf_check' if source_check else 'Weak Evidence Issue Review',
                'route': 'source_pdf_check' if source_check else 'issue_evidence_review',
                'test_id': None,
                'issue_id': issue['issue_id'],
                'proposal': issue.get('adjudication_note') or ('Verify source/PDF extraction before treating this weak-evidence issue as a translation or parser regression.' if source_check else 'Issue lacks sufficient source span, KO span, or semantic diff evidence.'),
                'card_fix': None,
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'evidence_quality': issue.get('evidence_quality'),
            })
            continue
        if issue.get('review_status') == 'llm_terminology_human_review':
            check = issue.get('llm_terminology_check') or {}
            proposals.append({
                'type': 'LLM Terminology Review',
                'route': 'terminology_consistency_review',
                'test_id': None,
                'issue_id': issue['issue_id'],
                'proposal': issue.get('evidence') or 'LLM terminology worker judged a locked-term consistency review candidate.',
                'card_fix': issue.get('suggested_fix'),
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'source_term': check.get('source_term') or issue.get('span_source'),
                'expected_ko': check.get('expected_ko'),
                'observed_ko': check.get('observed_ko') or issue.get('span_ko'),
                'same_card_approved_present': check.get('same_card_approved_present'),
                'violation_type': check.get('violation_type'),
                'deterministic_hint_used': check.get('deterministic_hint_used'),
                'confidence': check.get('confidence') or issue.get('confidence'),
            })
            continue
        if issue.get('review_status') == 'llm_lore_ontology_human_review':
            check = issue.get('llm_lore_ontology_check') or {}
            proposals.append({
                'type': 'LLM Lore Ontology Review',
                'route': 'lore_ontology_review',
                'test_id': None,
                'issue_id': issue['issue_id'],
                'proposal': issue.get('evidence') or 'LLM lore ontology worker judged an entity rendering review candidate.',
                'card_fix': issue.get('suggested_fix'),
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'entity_id': check.get('entity_id'),
                'entity_type': check.get('entity_type'),
                'canonical_en': check.get('canonical_en') or issue.get('span_source'),
                'expected_ko': check.get('expected_ko'),
                'observed_ko': check.get('observed_ko') or issue.get('span_ko'),
                'same_card_approved_present': check.get('same_card_approved_present'),
                'violation_type': check.get('violation_type'),
                'deterministic_hint_used': check.get('deterministic_hint_used'),
                'confidence': check.get('confidence') or issue.get('confidence'),
            })
            continue
        if issue.get('review_status') == 'llm_syntax_style_human_review':
            check = issue.get('llm_syntax_style_check') or {}
            proposals.append({
                'type': 'LLM Syntax/Style Review',
                'route': 'syntax_style_review',
                'test_id': None,
                'issue_id': issue['issue_id'],
                'proposal': issue.get('evidence') or 'LLM syntax/style worker judged a review candidate.',
                'card_fix': issue.get('suggested_fix'),
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'source_pattern': check.get('source_pattern') or issue.get('span_source'),
                'current_template': check.get('current_template'),
                'dominant_template': check.get('dominant_template'),
                'expected_template': check.get('expected_template'),
                'is_style_drift': check.get('is_style_drift'),
                'is_semantic_mismatch': check.get('is_semantic_mismatch'),
                'meaning_equivalent': check.get('meaning_equivalent'),
                'evidence_examples': check.get('evidence_examples'),
                'deterministic_hint_used': check.get('deterministic_hint_used'),
                'confidence': check.get('confidence') or issue.get('confidence'),
            })
            continue
        if issue.get('llm_disputed'):
            review = issue.get('llm_issue_review') or {}
            proposals.append({
                'type': 'false_positive_review',
                'route': 'false_positive_review',
                'test_id': None,
                'issue_id': issue['issue_id'],
                'proposal': review.get('evidence') or 'LLM disputes this deterministic blocker; human approval required before suppressing it.',
                'card_fix': None,
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'llm_verdict': review.get('llm_verdict'),
                'confidence': review.get('confidence'),
                'recommended_action': review.get('recommended_action') or 'downgrade_to_human_review',
            })
            continue
        route = _classify_learning_route_for_context(context, issue)
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
    for check in context.get('syntax_pattern_result', {}).get('checks', []):
        if check.get('check_type') != 'llm_syntax_style_judgment':
            continue
        if any(p.get('issue_id') == check.get('issue_id') for p in proposals):
            continue
        if check.get('status') != 'warn' or not check.get('requires_human_review'):
            continue
        proposals.append({
            'type': 'LLM Syntax/Style Review',
            'route': 'syntax_style_review',
            'test_id': None,
            'issue_id': check.get('issue_id'),
            'proposal': check.get('evidence') or f"LLM worker judged `{check.get('source_pattern')}` as a syntax/style review candidate.",
            'card_fix': check.get('suggested_fix') or 'Review whether this card should align with the syntax/style evidence; do not auto-apply.',
            'source_item_id': context['item_id'],
            'requires_human_approval': True,
            'check_type': check.get('check_type'),
            'source_pattern': check.get('source_pattern'),
            'current_template': check.get('current_template'),
            'dominant_template': check.get('dominant_template'),
            'expected_template': check.get('expected_template'),
            'is_style_drift': check.get('is_style_drift'),
            'is_semantic_mismatch': check.get('is_semantic_mismatch'),
            'meaning_equivalent': check.get('meaning_equivalent'),
            'evidence_examples': check.get('evidence_examples'),
            'deterministic_hint_used': check.get('deterministic_hint_used'),
            'confidence': check.get('confidence'),
        })
    for check in context.get('cross_card_consistency', {}).get('checks', []):
        if check.get('check_type') == 'quantity_term_pattern_consistency' and check.get('status') == 'warn' and check.get('requires_human_review'):
            proposals.append({
                'type': 'Quantity Term Pattern Review',
                'route': 'quantity_term_pattern_review',
                'test_id': None,
                'issue_id': check.get('issue_id'),
                'proposal': f"Current KO quantity template `{check.get('current_template')}` differs from term/action-family dominant `{check.get('dominant_template')}` for `{check.get('index_key')}`.",
                'card_fix': 'Review whether this card should align with the learned term/action-family quantity template; do not auto-apply.',
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'index_key': check.get('index_key'),
                'source_family': check.get('source_family'),
                'term_source': check.get('term_source'),
                'term_ko_dominant': check.get('term_ko_dominant'),
                'current_term_ko': check.get('current_term_ko'),
                'span_ko': check.get('span_ko'),
                'current_template': check.get('current_template'),
                'dominant_template': check.get('dominant_template'),
                'evidence_count': check.get('evidence_count'),
                'confidence': check.get('confidence'),
                'meaning_equivalent': check.get('meaning_equivalent'),
            })
            continue
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
        if check.get('check_type') == 'llm_cross_card_pattern_consistency' and check.get('status') == 'warn' and check.get('requires_human_review'):
            proposals.append({
                'type': 'LLM Cross-Card Pattern Review',
                'route': 'cross_card_consistency_review',
                'test_id': None,
                'issue_id': check.get('issue_id'),
                'proposal': check.get('evidence') or f"LLM worker judged `{check.get('family_name')}` as a cross-card pattern review candidate.",
                'card_fix': check.get('suggested_fix') or 'Review whether this card should align with the LLM-inferred cross-card dominant pattern; do not auto-apply.',
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'check_type': check.get('check_type'),
                'family_name': check.get('family_name'),
                'drift_type': check.get('drift_type'),
                'dominant_pattern': check.get('dominant_pattern'),
                'current_pattern': check.get('current_pattern'),
                'current_template': check.get('current_template'),
                'dominant_template': check.get('dominant_template'),
                'meaning_equivalent': check.get('meaning_equivalent'),
                'evidence_examples': check.get('evidence_examples'),
                'deterministic_hint_used': check.get('deterministic_hint_used'),
                'confidence': check.get('confidence'),
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
        if check.get('check_type') == 'llm_lore_ontology_consistency':
            if any(p.get('issue_id') == check.get('issue_id') for p in proposals):
                continue
            proposals.append({
                'type': 'LLM Lore Ontology Review',
                'route': 'lore_ontology_review',
                'test_id': None,
                'issue_id': check.get('issue_id'),
                'proposal': check.get('evidence') or f"LLM worker judged TES lore entity `{check.get('canonical_en')}` as a review candidate.",
                'card_fix': check.get('suggested_action') or f"Review whether `{check.get('expected_ko')}` should be used for `{check.get('canonical_en')}`; do not auto-apply.",
                'source_item_id': context['item_id'],
                'requires_human_approval': True,
                'check_type': check.get('check_type'),
                'entity_id': check.get('entity_id'),
                'entity_type': check.get('entity_type'),
                'canonical_en': check.get('canonical_en'),
                'expected_ko': check.get('expected_ko'),
                'observed_ko': check.get('observed_ko'),
                'same_card_approved_present': check.get('same_card_approved_present'),
                'violation_type': check.get('violation_type'),
                'deterministic_hint_used': check.get('deterministic_hint_used'),
                'confidence': check.get('confidence'),
            })
            continue
        if any(
            p.get('type') == 'LLM Lore Ontology Review'
            and (p.get('entity_id') == check.get('entity_id') or p.get('canonical_en') == check.get('canonical_en'))
            for p in proposals
        ):
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
    llm_data = _llm_qa_reviewer(context)
    route_overrides = _apply_llm_route_overrides(context, llm_data)
    context['qa_reviewer_result'] = {
        'final_verdict': context['verdict'],
        'final_score': context['score'],
        'final_decision_basis': final_decision_basis,
        'requires_human_review': context['requires_human_review'],
        'blocking_issue_ids': blocking_issue_ids,
        'llm_disputed_blocking_issue_ids': llm_disputed_blocking_issue_ids,
        'weak_evidence_blocking_issue_ids': weak_evidence_blocking_issue_ids,
        'llm_resolved_unresolved_issue_ids': llm_resolved_unresolved_issue_ids,
        'undisputed_blocking_issue_ids': undisputed_blocking_issue_ids,
        'self_verification_warnings': self_verification_warnings,
        'candidate_decision': candidate_decision,
        'proposal_count': len(proposals),
        'proposal_routes': sorted({p.get('route') for p in proposals if p.get('route')}),
        'llm_route_overrides': route_overrides,
    }
    if llm_data and llm_data.get('qa_reviewer_patch'):
        patch = dict(llm_data['qa_reviewer_patch'])
        if patch.get('final_decision_basis'):
            context['qa_reviewer_result']['final_decision_basis'] = patch['final_decision_basis']
        if patch.get('needs_human_review') is True:
            context['requires_human_review'] = True
            context['qa_reviewer_result']['requires_human_review'] = True
        context['qa_reviewer_result']['llm_advisory'] = patch
    return record_agent(context, AGENT_NAME, {'summary': f"{context['verdict']} score={context['score']} proposals={len(proposals)}"})
