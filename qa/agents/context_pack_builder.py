from __future__ import annotations

import json
from pathlib import Path
from .shared import QA_ROOT, parse_sections, record_agent, similar_corpus, syntax_hits_for, term_hits_for
from .ontology_lookup import lookup_ontology_hits

AGENT_NAME = 'context-pack-builder'

DEFERRED_CONTEXT_OPTIONS = [
    {
        'profile': 'medium',
        'option': 'expand_similar_corpus_scope',
        'benefit': 'improves style/pattern recall across Conflict, Peaceful, and Delve components',
        'token_cost': 'medium',
        'default': False,
        'activation_hint': 'Enable only when current batch has unresolved semantic/style pattern or poor similar_prior_translations coverage.',
    },
    {
        'profile': 'medium',
        'option': 'include_choice_block_excerpts',
        'benefit': 'helps humans and downstream LLMs inspect choice-scoped source/translation alignment without opening raw files',
        'token_cost': 'medium',
        'default': False,
        'activation_hint': 'Keep mini default; add excerpts only for cards with choice_alignment warnings.',
    },
    {
        'profile': 'high',
        'option': 'full_prior_qa_log_context',
        'benefit': 'improves regression reasoning by exposing full prior approved QA rationales',
        'token_cost': 'high',
        'default': False,
        'activation_hint': 'Use for final review or ambiguous cards only; never default for batch runs.',
    },
    {
        'profile': 'high',
        'option': 'full_glossary_rows_with_provenance',
        'benefit': 'preserves sheet provenance and imported metadata for glossary audits',
        'token_cost': 'high',
        'default': False,
        'activation_hint': 'Use for glossary maintenance/debugging, not normal translation QA.',
    },
]


def _explicit_or_fallback(input_card: dict, key: str, fallback):
    return input_card[key] if key in input_card else fallback


def _compact_glossary_hit(hit: dict) -> dict:
    compact = {}
    if hit.get('sheet_row') is not None:
        compact['row'] = hit.get('sheet_row')
    elif hit.get('row') is not None:
        compact['row'] = hit.get('row')
    for source_key, out_key in [
        ('en', 'en'), ('ko', 'ko'), ('status', 'status'), ('notes', 'notes'),
        ('category', 'category'), ('term_category', 'term_category'),
        ('lock_policy', 'lock_policy'), ('term_policy', 'term_policy'),
        ('forbidden_ko', 'forbidden_ko'), ('deprecated_ko', 'deprecated_ko'),
        ('source', 'source'), ('source_refs', 'source_refs'),
    ]:
        value = hit.get(source_key)
        if value not in [None, '']:
            compact[out_key] = value
    return compact


def _glossary_summary(hits: list[dict]) -> dict:
    counts = {'approved': 0, 'candidate': 0, 'other': 0, 'broad_common': 0}
    broad_terms = {'a', 'an', 'an ~', 'each', 'every', 'all', 'cards', 'card'}
    for hit in hits:
        status = str(hit.get('status') or '').lower()
        if status == 'approved':
            counts['approved'] += 1
        elif status == 'candidate':
            counts['candidate'] += 1
        else:
            counts['other'] += 1
        en = str(hit.get('en') or '').strip().lower()
        if en in broad_terms or en.replace('~', '').strip() in broad_terms:
            counts['broad_common'] += 1
    counts['total'] = len(hits)
    return counts


def _is_lore_glossary_row(row: dict) -> bool:
    category = str(row.get('category') or row.get('term_category') or '').strip().lower()
    policy = str(row.get('lock_policy') or row.get('term_policy') or '').strip().lower()
    return category in {'lore_term', 'proper_noun', 'named_entity', 'entity'} or policy in {'lore', 'ontology'}


def _glossary_lore_ontology_hits(term_hits: list[dict]) -> list[dict]:
    hits = []
    for idx, row in enumerate(term_hits, start=1):
        if not isinstance(row, dict) or not _is_lore_glossary_row(row):
            continue
        en = row.get('en') or row.get('term') or row.get('source') or row.get('english')
        ko = row.get('ko') or row.get('target') or row.get('korean')
        if not en or not ko:
            continue
        hit = {
            'id': row.get('id') or f'context_glossary:{str(en).strip().casefold()}',
            'entity_id': row.get('entity_id') or row.get('id') or f'context_glossary:{str(en).strip().casefold()}',
            'type': row.get('entity_type') or row.get('type') or row.get('category') or 'lore_term',
            'canonical_en': str(en).strip(),
            'term': str(en).strip(),
            'aliases_en': row.get('aliases_en') or row.get('english_aliases') or [],
            'canonical_ko': str(ko).strip(),
            'ko': str(ko).strip(),
            'aliases_ko': row.get('aliases_ko') or row.get('allowed_variants_ko') or [str(ko).strip()],
            'allowed_variants_ko': row.get('allowed_variants_ko') or row.get('aliases_ko') or [str(ko).strip()],
            'forbidden_ko': row.get('forbidden_ko') or row.get('deprecated_ko') or [],
            'status': row.get('status') or 'approved',
            'ko_status': row.get('status') or 'approved',
            'source': row.get('source') or 'input_card.term_glossary',
            'source_refs': row.get('source_refs') or [row.get('source') or 'input_card.term_glossary'],
            'confidence': 0.95 if str(row.get('status') or '').lower() == 'approved' else 0.75,
            'strategy': 'context_glossary_lore_term',
        }
        hits.append(hit)
    return hits


def _merge_ontology_hits(primary: list, supplemental: list[dict]) -> list:
    merged = []
    seen = set()
    for hit in list(primary or []) + list(supplemental or []):
        if not isinstance(hit, dict):
            merged.append(hit)
            continue
        key = str(hit.get('canonical_en') or hit.get('term') or hit.get('en') or hit.get('entity_id') or '').casefold()
        if key and key in seen:
            # Prefer an earlier real ontology row, but if it lacks Korean evidence
            # and supplemental glossary has it, enrich the existing compact hit.
            if supplemental and hit in supplemental:
                for existing in merged:
                    existing_key = str(existing.get('canonical_en') or existing.get('term') or existing.get('en') or existing.get('entity_id') or '').casefold() if isinstance(existing, dict) else ''
                    if existing_key == key:
                        for field in ['canonical_ko', 'ko', 'aliases_ko', 'allowed_variants_ko', 'forbidden_ko', 'ko_status']:
                            if not existing.get(field) and hit.get(field):
                                existing[field] = hit[field]
                        existing.setdefault('supplemental_sources', []).append(hit.get('source'))
                        break
            continue
        if key:
            seen.add(key)
        merged.append(hit)
    return merged


def _section_ref(sections: dict[str, list[str]], key: str, occurrence: int) -> dict | None:
    values = sections.get(key, [])
    if occurrence < len(values):
        return {'section': key, 'index': occurrence}
    return None


def _choice_alignment(source_sections: dict[str, list[str]], translation_sections: dict[str, list[str]]) -> list[dict]:
    max_choices = max(len(source_sections.get('choice1', [])), len(source_sections.get('choice2', [])), len(translation_sections.get('choice1', [])), len(translation_sections.get('choice2', [])))
    if max_choices == 0:
        # Most card files store choice1/choice2 as separate keys, so align each key explicitly.
        keys = ['choice1', 'choice2']
    else:
        keys = ['choice1', 'choice2']
    rows = []
    for choice_pos, choice_key in enumerate(keys):
        if not source_sections.get(choice_key) and not translation_sections.get(choice_key):
            continue
        source_refs = {'choice': _section_ref(source_sections, choice_key, 0)}
        translation_refs = {'choice': _section_ref(translation_sections, choice_key, 0)}
        for section in ['choiceType', 'description', 'battleObjective']:
            source_refs[section] = _section_ref(source_sections, section, choice_pos)
            translation_refs[section] = _section_ref(translation_sections, section, choice_pos)
        missing = []
        required_ref_names = {'choice', 'choiceType', 'description'}
        for side, refs in [('source', source_refs), ('translation', translation_refs)]:
            for name, ref in refs.items():
                if name in required_ref_names and ref is None:
                    missing.append(f'{side}.{name}')
        rows.append({
            'choice_key': choice_key,
            'source_refs': source_refs,
            'translation_refs': translation_refs,
            'status': 'aligned' if not missing else 'partial',
            'missing_refs': missing,
        })
    return rows


def _quality(source_sections: dict, translation_sections: dict, alignment: list[dict], glossary_counts: dict, ontology_hits: list, patch_hits: list, similar: list, warnings: list[str]) -> dict:
    if not alignment:
        alignment_status = 'not_applicable'
    elif all(row['status'] == 'aligned' for row in alignment):
        alignment_status = 'ok'
    else:
        alignment_status = 'partial'
    return {
        'choice_alignment_status': alignment_status,
        'source_choice_count': len([k for k in ['choice1', 'choice2'] if source_sections.get(k)]),
        'translation_choice_count': len([k for k in ['choice1', 'choice2'] if translation_sections.get(k)]),
        'glossary_total_hits': glossary_counts['total'],
        'glossary_approved_hits': glossary_counts['approved'],
        'glossary_candidate_hits': glossary_counts['candidate'],
        'glossary_broad_common_hits': glossary_counts['broad_common'],
        'ontology_hits_count': len(ontology_hits),
        'patch_hits_count': len(patch_hits),
        'similar_prior_translations_count': len(similar),
        'warnings': warnings,
    }


def _write_deferred_options(run_id: str) -> None:
    root = QA_ROOT / 'runs' / run_id / 'review'
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        'default_profile': 'mini',
        'principle': 'Keep default context packs compact; log medium/high expansions for opt-in use when accuracy requires more evidence.',
        'options': DEFERRED_CONTEXT_OPTIONS,
    }
    (root / 'context_expansion_options.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def run(context):
    facts = context['facts']
    input_card = context.get('input_card', {})
    term_hits = term_hits_for(context['source_text'], context['current_ko'])
    syntax_hits = syntax_hits_for(context['source_text'], context['current_ko'])
    similar = similar_corpus(facts.get('patterns', []), context['code'])
    prior = QA_ROOT / 'runs' / 'RUN_2026_07_08_SAMPLE_CONFLICT_GE_01_08_PASS1' / 'qa_json' / f"{context['code']}.qa.json"
    qa_logs = []
    if prior.exists():
        qa_logs.append({'source': 'previous_sample_unapproved', 'path': str(prior), 'trust': 'unapproved_previous_run', 'use': 'compare only; not approved memory'})
    if input_card:
        term_hits = _explicit_or_fallback(input_card, 'term_glossary', term_hits)
        syntax_hits = _explicit_or_fallback(input_card, 'syntax_dictionary', syntax_hits)
        similar = _explicit_or_fallback(input_card, 'prior_translations', similar)
        qa_logs = _explicit_or_fallback(input_card, 'approved_qa_logs', qa_logs)
    ontology_lookup_quality = {'source': 'explicit_input', 'ontology_available': None, 'warnings': []}
    glossary_ontology_hits = _glossary_lore_ontology_hits(term_hits if isinstance(term_hits, list) else [])
    if input_card and 'lore_ontology' in input_card and input_card.get('lore_ontology'):
        ontology_hits = _merge_ontology_hits(input_card.get('lore_ontology', []), glossary_ontology_hits)
    elif input_card and 'lore_ontology' in input_card:
        ontology_hits = glossary_ontology_hits
    else:
        ontology_hits, ontology_lookup_quality = lookup_ontology_hits(context['source_text'])
        ontology_hits = _merge_ontology_hits(ontology_hits, glossary_ontology_hits)
    if glossary_ontology_hits:
        ontology_lookup_quality = dict(ontology_lookup_quality)
        ontology_lookup_quality['context_glossary_lore_hits'] = len(glossary_ontology_hits)
    patch_hits = input_card.get('patch_notes', [])
    source_sections = parse_sections(context['source_text'])
    translation_sections = parse_sections(context['current_ko'])
    alignment = _choice_alignment(source_sections, translation_sections)
    glossary_counts = _glossary_summary(term_hits)
    warnings = []
    if glossary_counts['total'] >= 25:
        warnings.append('glossary_hits_many_compacted')
    if glossary_counts['broad_common']:
        warnings.append('glossary_contains_broad_common_terms')
    if similar and all(str(x.get('item_id', '')).startswith('Conflict Encounter/') for x in similar):
        warnings.append('similar_corpus_scope_limited_conflict_only')
    pack = {
        'context_budget_profile': 'mini',
        'item_id': context['item_id'],
        'card_id': context['code'],
        'category': context['category'],
        'policy': context['policy'],
        'source_file': context['source_file'],
        'translation_file': context['translation_file'],
        'source_sections': source_sections,
        'translation_sections': translation_sections,
        'choice_alignment': alignment,
        'glossary_hits_compact': [_compact_glossary_hit(h) for h in term_hits],
        'glossary_summary': glossary_counts,
        'syntax_hits': syntax_hits,
        'ontology_hits': ontology_hits,
        'ontology_lookup_quality': ontology_lookup_quality,
        'patch_hits': patch_hits,
        'similar_prior_translations': similar,
        'similar_qa_logs': qa_logs,
        'relevant_pattern_rules': input_card.get('pattern_rules') if 'pattern_rules' in input_card else syntax_hits,
        'relevant_error_memory': input_card.get('error_memory', []),
        'regression_tests': input_card.get('regression_tests', []),
        'missing_optional_context': [k for k in ['lore_ontology', 'patch_notes', 'approved_qa_logs'] if (k != 'lore_ontology' or not ontology_hits) and (k not in input_card or not input_card.get(k))],
        'deferred_context_options': DEFERRED_CONTEXT_OPTIONS,
    }
    pack['context_pack_quality'] = _quality(source_sections, translation_sections, alignment, glossary_counts, ontology_hits, patch_hits, similar, warnings)
    pack['context_pack_quality']['ontology_lookup'] = ontology_lookup_quality
    if context.get('batch_index_refs'):
        pack['batch_index_refs'] = context['batch_index_refs']
    _write_deferred_options(context['run_id'])
    context['context_pack'] = pack
    context['context_summary'] = {
        'context_budget_profile': 'mini',
        'glossary_hits_count': len(term_hits),
        'glossary_compacted': True,
        'syntax_hits_count': len(syntax_hits),
        'ontology_hits_count': len(ontology_hits),
        'patch_hits_count': len(patch_hits),
        'similar_prior_translations_count': len(similar),
        'similar_qa_logs_count': len(qa_logs),
        'choice_alignment_status': pack['context_pack_quality']['choice_alignment_status'],
        'deferred_context_options_count': len(DEFERRED_CONTEXT_OPTIONS),
        'missing_optional_context': pack['missing_optional_context'],
    }
    return record_agent(context, AGENT_NAME, {'summary': f"context pack built [mini]: glossary={len(term_hits)} compact, syntax={len(syntax_hits)}, similar={len(similar)}, deferred_options={len(DEFERRED_CONTEXT_OPTIONS)}"})
