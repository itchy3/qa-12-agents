from __future__ import annotations

import re
from typing import Any

from .shared import record_agent

AGENT_NAME = 'lore-ontology-checker'


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    return [text] if text else []


def _first_nonempty(entry: dict, keys: list[str]) -> str:
    for key in keys:
        value = entry.get(key)
        if value not in [None, '']:
            return str(value).strip()
    return ''


def _entity_id(entry: dict, fallback: int) -> str:
    return _first_nonempty(entry, ['id', 'entity_id', 'qid', 'slug']) or f'lore_entity_{fallback}'


def _canonical_en(entry: dict) -> str:
    return _first_nonempty(entry, ['canonical_en', 'term', 'en', 'name_en', 'title_en', 'name'])


def _canonical_ko(entry: dict) -> str:
    return _first_nonempty(entry, ['canonical_ko', 'ko', 'name_ko', 'title_ko', 'official_ko', 'patch_ko'])


def _en_aliases(entry: dict, canonical: str) -> list[str]:
    aliases = []
    for key in ['aliases_en', 'alias_en', 'aliases', 'english_aliases', 'wiki_titles_en']:
        aliases.extend(_as_list(entry.get(key)))
    if canonical:
        aliases.insert(0, canonical)
    seen = set()
    out = []
    for value in aliases:
        key = value.casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _ko_aliases(entry: dict, canonical: str) -> list[str]:
    aliases = []
    for key in ['aliases_ko', 'alias_ko', 'allowed_variants_ko', 'ko_aliases', 'wiki_titles_ko']:
        aliases.extend(_as_list(entry.get(key)))
    if canonical:
        aliases.insert(0, canonical)
    seen = set()
    out = []
    for value in aliases:
        key = value.casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _find_en_span(text: str, names: list[str]) -> dict | None:
    # Prefer longer names first so "Hermaeus Mora" wins over "Mora".
    for name in sorted(names, key=len, reverse=True):
        if not name:
            continue
        pattern = r'(?<![A-Za-z])' + re.escape(name) + r'(?![A-Za-z])'
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return {'text': match.group(0), 'start': match.start(), 'end': match.end(), 'matched_name': name}
    return None


def _find_ko_span(text: str, names: list[str]) -> dict | None:
    for name in sorted(names, key=len, reverse=True):
        if not name:
            continue
        start = text.find(name)
        if start >= 0:
            return {'text': name, 'start': start, 'end': start + len(name), 'matched_name': name}
    return None


def _check_entity(entry: dict, idx: int, source_text: str, current_ko: str) -> dict | None:
    entity_id = _entity_id(entry, idx)
    canonical_en = _canonical_en(entry)
    canonical_ko = _canonical_ko(entry)
    en_names = _en_aliases(entry, canonical_en)
    ko_names = _ko_aliases(entry, canonical_ko)
    forbidden = _as_list(entry.get('forbidden_ko')) + _as_list(entry.get('deprecated_ko'))

    source_span = _find_en_span(source_text, en_names)
    if not source_span:
        return None

    forbidden_span = _find_ko_span(current_ko, forbidden)
    approved_span = _find_ko_span(current_ko, ko_names)

    base = {
        'check_type': 'lore_ontology_consistency',
        'entity_id': entity_id,
        'entity_type': entry.get('type') or entry.get('entity_type'),
        'canonical_en': canonical_en,
        'expected_ko': canonical_ko,
        'source_span': source_span['text'],
        'source_match': source_span,
        'ontology_status': entry.get('status'),
        'source': entry.get('source'),
        'requires_human_review': False,
        'confidence': 0.95 if str(entry.get('status', '')).lower() == 'approved' else 0.75,
    }

    if forbidden_span:
        base.update({
            'status': 'warn',
            'decision': 'forbidden_or_deprecated_ko',
            'observed_ko': forbidden_span['text'],
            'observed_match': forbidden_span,
            'severity': 'StyleWarning',
            'requires_human_review': True,
            'suggested_action': f"Review TES lore rendering: `{canonical_en}` should use `{canonical_ko}` rather than `{forbidden_span['text']}` if the ontology entry is approved.",
        })
        return base

    if approved_span:
        base.update({
            'status': 'pass',
            'decision': 'approved_ko_present',
            'observed_ko': approved_span['text'],
            'observed_match': approved_span,
            'severity': 'Info',
        })
        return base

    base.update({
        'status': 'warn',
        'decision': 'expected_ko_missing',
        'observed_ko': None,
        'severity': 'StyleWarning',
        'requires_human_review': True,
        'suggested_action': f"Review TES lore rendering: source contains `{source_span['text']}` but current KO does not contain approved/allowed Korean form `{canonical_ko}`.",
    })
    return base


def run(context):
    ontology_hits = context.get('context_pack', {}).get('ontology_hits') or []
    seeded_violations = [
        x for x in context['facts'].get('issues', [])
        if 'Lore' in x.get('issue_type', '') or 'lore' in x.get('issue_type', '').lower()
    ]

    if not ontology_hits:
        context['ontology_result'] = {
            'source': 'not_available',
            'status': 'not_available',
            'checks': [],
            'violations': seeded_violations,
            'quality': {
                'ontology_available': False,
                'entities_loaded': 0,
                'source_entities_detected': 0,
                'checked_entities': 0,
                'memory_updates_applied': False,
                'warnings': ['ontology_hits_not_available'],
            },
        }
        return record_agent(context, AGENT_NAME, {'summary': 'not_available'})

    checks = []
    for idx, entry in enumerate(ontology_hits, start=1):
        if not isinstance(entry, dict):
            continue
        check = _check_entity(entry, idx, context.get('source_text', ''), context.get('current_ko', ''))
        if check:
            checks.append(check)

    review_required = [c for c in checks if c.get('requires_human_review')]
    status = 'warn' if review_required or seeded_violations else 'pass'
    context['ontology_result'] = {
        'source': 'context_pack.ontology_hits',
        'status': status,
        'checks': checks,
        'violations': seeded_violations + review_required,
        'quality': {
            'ontology_available': True,
            'entities_loaded': len([x for x in ontology_hits if isinstance(x, dict)]),
            'source_entities_detected': len(checks),
            'checked_entities': len(checks),
            'review_required_count': len(review_required),
            'memory_updates_applied': False,
            'warnings': [] if checks else ['no_ontology_entities_detected_in_source'],
        },
    }
    return record_agent(context, AGENT_NAME, {'summary': status})
