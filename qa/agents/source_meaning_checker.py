from __future__ import annotations

import re
from typing import Any
from .shared import record_agent

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


def run(context):
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
    context['expected_seed_slots'] = facts.get('expected_seed_slots')
    context['slot_extraction_quality'] = facts.get('slot_extraction_quality')
    context['source_quality_issues'] = facts.get('source_quality_issues', [])
    return record_agent(context, AGENT_NAME, {'summary': f"{mode} slots recorded; richness={quality['slot_richness']}"})
