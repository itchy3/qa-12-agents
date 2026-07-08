from __future__ import annotations

import re
from typing import Any
from .shared import record_agent
from llm_client import build_prompt, call_json, compact_card_payload, record_usage

AGENT_NAME = 'source-meaning-checker'


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def _has_key(value: Any, key: str) -> bool:
    return any(isinstance(node, dict) and key in node for node in _walk(value))


def _mode_for(facts: dict) -> str:
    if facts.get('mode'):
        return facts['mode']
    if facts.get('semantic_pattern') == 'UNRESOLVED':
        return 'unresolved_generic'
    if facts.get('rule_source') in {'generic_parser', 'generic_parser+seed_oracle', 'approved_qa_logs', 'dominant_prior_translations', 'dominant_observed'}:
        return 'parser_extracted'
    return 'seeded_legacy'


def _optionality(source_text: str) -> str:
    lower = source_text.lower()
    has_must = bool(re.search(r'\bmust\b|\bcannot\b', lower))
    has_may = bool(re.search(r'\bmay\b|\bcan\b', lower))
    if has_must and has_may:
        return 'mixed_must_may'
    if has_must:
        return 'contains_must'
    if has_may:
        return 'contains_may'
    return 'none_detected'


def _timing(source_text: str, slots: dict) -> str:
    lower = source_text.lower()
    if _has_key(slots, 'persistent_effect'):
        return 'has_persistent_timing'
    if re.search(r'after each time|each time|at the start|at the end|when ', lower):
        return 'has_timing_clause'
    return 'none_detected'


def _condition(source_text: str) -> str:
    lower = source_text.lower()
    if 'if possible' in lower:
        return 'has_if_possible'
    if 'if successful' in lower:
        return 'has_if_successful'
    if re.search(r'\bif\b', lower):
        return 'has_if_clause'
    return 'none_detected'


def _quality(slots: dict, semantic_pattern: str) -> dict:
    has_choices = bool(slots.get('choices'))
    has_actions = _has_key(slots, 'actions') or bool(slots.get('actions'))
    has_modals = any(isinstance(node, dict) and node.get('modal') for node in _walk(slots))
    has_tables = _has_key(slots, 'random_table')
    has_persistent = _has_key(slots, 'persistent_effect')
    warnings = []
    if semantic_pattern == 'UNRESOLVED':
        warnings.append('semantic_pattern_unresolved')
    if has_choices and not has_actions and not has_tables and not has_persistent:
        warnings.append('choice_description_actions_unparsed')
    if has_tables and not has_actions:
        # Table outcomes are usually actions but keep the signal explicit if parser shape changes later.
        warnings.append('table_actions_not_promoted_to_choice_actions')
    signal_count = sum([has_choices, has_actions, has_modals, has_tables, has_persistent])
    richness = 'high' if signal_count >= 3 or has_tables else 'medium' if signal_count >= 2 else 'low'
    if semantic_pattern == 'UNRESOLVED':
        richness = 'low'
    return {
        'slot_richness': richness,
        'has_choice_slots': has_choices,
        'has_action_slots': has_actions,
        'has_modal_slots': has_modals,
        'has_table_slots': has_tables,
        'has_persistent_slots': has_persistent,
        'warnings': warnings,
    }


def _confidence(mode: str, quality: dict) -> float:
    if mode == 'unresolved_generic':
        return 0.2
    richness = quality.get('slot_richness')
    if richness == 'high':
        return 0.9
    if richness == 'medium':
        return 0.82
    return 0.68 if mode == 'parser_extracted' else 0.74


def _llm_source_meaning(context: dict) -> dict | None:
    schema = {
        'source_analysis': {
            'mode': 'llm_json',
            'semantic_pattern': 'string or UNKNOWN',
            'source_slots': {'actor': '...', 'action': '...', 'target_scope': '...'},
            'confidence': '0..1',
            'unknowns': [],
            'needs_human_pattern_review': False,
        },
        'translation_slot_result': {
            'mode': 'llm_json',
            'ko_slots': {},
            'slot_issues': [],
        },
    }
    prompt = build_prompt(
        AGENT_NAME,
        'Extract compact semantic slots from source_text and current_ko. Focus on actor, action, target, target_scope, timing, condition, modal force, numbers, icons/markup, and UNKNOWNs.',
        compact_card_payload(context, include_upstream=False),
        schema,
    )
    result = call_json(AGENT_NAME, prompt, expected_keys=['source_analysis', 'translation_slot_result'])
    record_usage(context, AGENT_NAME, result['usage'])
    return result.get('data')


def _build_semantic_ir(source_analysis: dict, translation_slot_result: dict, previous_pattern: str | None = None) -> dict:
    source_ir = source_analysis.get('source_slots') or {}
    ko_ir = translation_slot_result.get('ko_slots') or {}
    semantic_pattern = source_analysis.get('semantic_pattern') or 'UNKNOWN'
    mode = source_analysis.get('mode') or 'unknown'
    llm_resolved = (
        mode == 'llm_json'
        and previous_pattern == 'UNRESOLVED'
        and semantic_pattern not in {'UNKNOWN', 'UNRESOLVED'}
        and not source_analysis.get('unknowns')
        and float(source_analysis.get('confidence') or 0) >= 0.75
    )
    return {
        'status': 'llm_resolved' if llm_resolved else ('unresolved' if semantic_pattern in {'UNKNOWN', 'UNRESOLVED'} else 'available'),
        'mode': mode,
        'semantic_pattern': semantic_pattern,
        'source_ir': source_ir,
        'ko_ir': ko_ir,
        'confidence': source_analysis.get('confidence'),
        'unknowns': source_analysis.get('unknowns') or [],
        'previous_semantic_pattern': previous_pattern,
        'requires_human_review': bool(source_analysis.get('needs_human_pattern_review')) or llm_resolved,
    }


def _downgrade_unresolved_if_llm_resolved(context: dict, semantic_ir: dict) -> None:
    if semantic_ir.get('status') != 'llm_resolved':
        return
    for issue in context.get('facts', {}).get('issues', []):
        if 'UNRESOLVED' not in str(issue.get('issue_id', '')) and issue.get('issue_type') != 'Unresolved semantic pattern':
            continue
        issue['issue_status'] = 'candidate'
        issue['review_status'] = 'llm_resolved_unresolved_human_review'
        issue['blocks_approval'] = False
        issue['llm_resolved'] = True
        issue['resolution_evidence'] = {
            'semantic_ir_status': semantic_ir.get('status'),
            'semantic_pattern': semantic_ir.get('semantic_pattern'),
            'confidence': semantic_ir.get('confidence'),
            'requires_human_approval': True,
        }


def run(context):
    llm_data = _llm_source_meaning(context)
    if llm_data:
        source_analysis = dict(llm_data.get('source_analysis') or {})
        translation_slot_result = dict(llm_data.get('translation_slot_result') or {})
        source_analysis.setdefault('mode', 'llm_json')
        source_analysis.setdefault('semantic_pattern', 'UNKNOWN')
        source_analysis.setdefault('source_slots', {})
        source_analysis.setdefault('unknowns', [])
        source_analysis.setdefault('needs_human_pattern_review', bool(source_analysis.get('unknowns')))
        source_analysis.setdefault('source_analysis_quality', {'slot_richness': 'llm_structured', 'warnings': []})
        translation_slot_result.setdefault('mode', 'llm_json')
        translation_slot_result.setdefault('ko_slots', {})
        translation_slot_result.setdefault('slot_issues', [])
        context['source_analysis'] = source_analysis
        context['translation_slot_result'] = translation_slot_result
        context['semantic_ir'] = _build_semantic_ir(source_analysis, translation_slot_result, context['facts'].get('semantic_pattern'))
        _downgrade_unresolved_if_llm_resolved(context, context['semantic_ir'])
        context['expected_seed_slots'] = context['facts'].get('expected_seed_slots')
        context['slot_extraction_quality'] = context['facts'].get('slot_extraction_quality')
        context['source_quality_issues'] = context['facts'].get('source_quality_issues', [])
        return record_agent(context, AGENT_NAME, {'summary': f"llm_json semantic slots recorded; confidence={source_analysis.get('confidence')}"})

    facts = context['facts']
    mode = _mode_for(facts)
    source_slots = facts['source_slots']
    quality = _quality(source_slots, facts['semantic_pattern'])
    context['source_analysis'] = {
        'mode': mode,
        'semantic_pattern': facts['semantic_pattern'],
        'source_slots': source_slots,
        'optionality': _optionality(context.get('source_text', '')),
        'timing': _timing(context.get('source_text', ''), source_slots),
        'condition': _condition(context.get('source_text', '')),
        'confidence': _confidence(mode, quality),
        'possible_rule_split': [],
        'needs_human_pattern_review': facts['semantic_pattern'] == 'UNRESOLVED',
        'source_analysis_quality': quality,
    }
    context['translation_slot_result'] = {
        'mode': mode,
        'ko_slots': facts['ko_slots'],
        'slot_issues': [x for x in facts.get('issues', []) if x.get('severity') in ['Critical', 'Major']],
    }
    context['semantic_ir'] = _build_semantic_ir(context['source_analysis'], context['translation_slot_result'], facts.get('semantic_pattern'))
    context['expected_seed_slots'] = facts.get('expected_seed_slots')
    context['slot_extraction_quality'] = facts.get('slot_extraction_quality')
    context['source_quality_issues'] = facts.get('source_quality_issues', [])
    return record_agent(context, AGENT_NAME, {'summary': f"{mode} slots recorded; richness={quality['slot_richness']}"})
