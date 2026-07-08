from __future__ import annotations

import re
from typing import Any

from .shared import record_agent

AGENT_NAME = 'patch-note-checker'


def _as_list(value: Any) -> list[str]:
    if value is None or value == '':
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _normalize_probe(text: str) -> str:
    return re.sub(r'[^0-9a-z가-힣]+', ' ', text.lower()).strip()


def _contains(text: str, needle: str, case_sensitive: bool = False) -> bool:
    if not needle:
        return False
    if case_sensitive:
        return needle in text
    if needle.lower() in text.lower():
        return True
    # Patch notes are often human-readable (`Unstable Peaceful`) while source uses
    # icon IDs (`ICON_Unstable_Peaceful`). Normalize separators to avoid false
    # not_applicable decisions.
    return _normalize_probe(needle) in _normalize_probe(text)


def _regex_contains(text: str, pattern: str) -> bool:
    if not pattern:
        return False
    try:
        return re.search(pattern, text, re.I | re.M) is not None
    except re.error:
        return False


def _patch_id(note: dict[str, Any], idx: int) -> str:
    return str(note.get('id') or note.get('patch_id') or note.get('note_id') or f'patch_note_{idx:03d}')


def _source_triggers(note: dict[str, Any]) -> list[str]:
    triggers: list[str] = []
    for key in ['source_contains', 'source_term', 'trigger_en', 'source_pattern', 'applies_when_source_contains']:
        triggers.extend(_as_list(note.get(key)))
    return triggers


def _source_regexes(note: dict[str, Any]) -> list[str]:
    regexes: list[str] = []
    for key in ['source_regex', 'applies_when_source_regex']:
        regexes.extend(_as_list(note.get(key)))
    return regexes


def _expected_ko(note: dict[str, Any]) -> list[str]:
    expected: list[str] = []
    for key in ['expected_ko', 'required_ko', 'ko', 'canonical_ko', 'after_ko', 'replacement_ko']:
        expected.extend(_as_list(note.get(key)))
    return expected


def _forbidden_ko(note: dict[str, Any]) -> list[str]:
    forbidden: list[str] = []
    for key in ['forbidden_ko', 'deprecated_ko', 'old_ko', 'before_ko', 'incorrect_ko']:
        forbidden.extend(_as_list(note.get(key)))
    return forbidden


def _supersedes(note: dict[str, Any]) -> list[str]:
    superseded: list[str] = []
    for key in ['supersedes', 'replaces', 'overrides', 'superseded_patch_ids']:
        superseded.extend(_as_list(note.get(key)))
    return superseded


def _superseded_by(note: dict[str, Any]) -> list[str]:
    replacers: list[str] = []
    for key in ['superseded_by', 'replaced_by', 'overridden_by']:
        replacers.extend(_as_list(note.get(key)))
    return replacers


def _is_real_patch_note(note: dict[str, Any]) -> bool:
    status = str(note.get('status', '')).lower()
    if status in {'draft', 'rejected', 'obsolete', 'ignored'}:
        return False
    if note.get('expected_ko') or note.get('required_ko') or note.get('forbidden_ko'):
        return True
    kind = str(note.get('change_type') or note.get('type') or '').lower()
    return any(token in kind for token in ['patch', 'improvement', 'fix', 'errata', 'correction'])


def _check_patch_note(note: dict[str, Any], idx: int, source_text: str, ko_text: str) -> dict[str, Any]:
    patch_id = _patch_id(note, idx)
    source_triggers = _source_triggers(note)
    source_regexes = _source_regexes(note)
    expected = _expected_ko(note)
    forbidden = _forbidden_ko(note)

    trigger_hits = [t for t in source_triggers if _contains(source_text, t)]
    regex_hits = [r for r in source_regexes if _regex_contains(source_text, r)]
    has_trigger = bool(source_triggers or source_regexes)
    applicable = bool(trigger_hits or regex_hits) if has_trigger else True

    base = {
        'patch_id': patch_id,
        'change_type': note.get('change_type') or note.get('type') or 'patch_note',
        'source': note.get('source'),
        'note': note.get('note') or note.get('description') or note.get('memo'),
        'status': note.get('status'),
        'source_triggers': source_triggers,
        'source_trigger_hits': trigger_hits,
        'source_regex_hits': regex_hits,
        'expected_ko': expected,
        'forbidden_ko': forbidden,
        'applicable': applicable,
    }

    if not applicable:
        return {**base, 'decision': 'not_applicable_to_source', 'requires_human_review': False, 'evidence': 'Patch note trigger did not appear in source_text.'}

    expected_hits = [x for x in expected if _contains(ko_text, x, case_sensitive=True)]
    forbidden_hits = [x for x in forbidden if _contains(ko_text, x, case_sensitive=True)]

    if expected and expected_hits and not forbidden_hits:
        return {**base, 'decision': 'applied', 'requires_human_review': False, 'expected_hits': expected_hits, 'forbidden_hits': forbidden_hits, 'evidence': 'Applicable patch/improvement is reflected in current_ko.'}
    if not expected and forbidden and not forbidden_hits:
        return {**base, 'decision': 'applied', 'requires_human_review': False, 'expected_hits': expected_hits, 'forbidden_hits': forbidden_hits, 'evidence': 'Deprecated/forbidden KO form is absent.'}
    if expected and not expected_hits:
        return {**base, 'decision': 'expected_patch_not_reflected', 'requires_human_review': True, 'expected_hits': expected_hits, 'forbidden_hits': forbidden_hits, 'evidence': 'Applicable patch/improvement exists, but expected KO form was not found in current_ko.'}
    if forbidden_hits:
        return {**base, 'decision': 'deprecated_form_still_present', 'requires_human_review': True, 'expected_hits': expected_hits, 'forbidden_hits': forbidden_hits, 'evidence': 'Applicable patch/improvement exists, but deprecated/forbidden KO form is still present.'}
    return {**base, 'decision': 'insufficient_patch_assertion', 'requires_human_review': True, 'expected_hits': expected_hits, 'forbidden_hits': forbidden_hits, 'evidence': 'Patch note was applicable, but it lacks a machine-checkable expected_ko/forbidden_ko assertion.'}


def _detect_active_conflicts(active_checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    applicable = [c for c in active_checks if c.get('applicable')]
    for i, left in enumerate(applicable):
        for right in applicable[i + 1:]:
            left_expected = set(left.get('expected_ko') or [])
            right_expected = set(right.get('expected_ko') or [])
            left_forbidden = set(left.get('forbidden_ko') or [])
            right_forbidden = set(right.get('forbidden_ko') or [])
            conflict_terms = sorted((left_expected & right_forbidden) | (right_expected & left_forbidden))
            if not conflict_terms:
                continue
            conflicts.append({
                'patch_id': f"PATCH_CONFLICT:{left.get('patch_id')}:{right.get('patch_id')}",
                'change_type': 'patch_history_conflict',
                'decision': 'active_patch_conflict',
                'applicable': True,
                'requires_human_review': True,
                'conflicting_patch_ids': [left.get('patch_id'), right.get('patch_id')],
                'conflict_terms': conflict_terms,
                'expected_ko': [],
                'forbidden_ko': [],
                'expected_hits': [],
                'forbidden_hits': [],
                'evidence': 'Multiple active patch/improvement notes conflict and no explicit supersedes/replaces relationship was provided.',
            })
    return conflicts


def run(context: dict[str, Any]) -> dict[str, Any]:
    input_card = context.get('input_card') or {}
    patch_notes = input_card.get('patch_notes') or context.get('patch_notes') or []
    if not patch_notes:
        context['patch_result'] = {
            'status': 'not_available',
            'patch_hits': [],
            'patch_checks': [],
            'patch_application_log': [],
            'violations': [],
            'quality': {'input_patch_count': 0, 'real_patch_count': 0, 'applicable_patch_count': 0, 'applied_count': 0, 'missing_count': 0},
        }
        return record_agent(context, AGENT_NAME, {'summary': 'not_available'})

    real_notes = [n for n in patch_notes if isinstance(n, dict) and _is_real_patch_note(n)]
    superseded_by: dict[str, list[str]] = {}
    for i, note in enumerate(real_notes, start=1):
        pid = _patch_id(note, i)
        for old_id in _supersedes(note):
            superseded_by.setdefault(old_id, []).append(pid)
        for newer_id in _superseded_by(note):
            superseded_by.setdefault(pid, []).append(newer_id)

    checks = []
    for i, note in enumerate(real_notes, start=1):
        pid = _patch_id(note, i)
        check = _check_patch_note(note, i, context.get('source_text', ''), context.get('current_ko', ''))
        if pid in superseded_by:
            check = {
                **check,
                'decision_before_supersession': check.get('decision'),
                'decision': 'superseded_by_newer_patch',
                'requires_human_review': False,
                'superseded_by': superseded_by[pid],
                'evidence': f"Patch `{pid}` is superseded by newer patch(es): {', '.join(superseded_by[pid])}. Kept in history log but excluded from active pass/fail.",
            }
        checks.append(check)

    active_checks = [c for c in checks if c.get('decision') != 'superseded_by_newer_patch']
    conflict_checks = _detect_active_conflicts(active_checks)
    if conflict_checks:
        checks.extend(conflict_checks)
        active_checks.extend(conflict_checks)
    applicable = [c for c in active_checks if c.get('applicable')]
    real_applicable = [c for c in applicable if c.get('decision') != 'active_patch_conflict']
    missing = [c for c in applicable if c.get('requires_human_review')]
    applied = [c for c in applicable if c.get('decision') == 'applied']
    skipped = [c for c in active_checks if not c.get('applicable')]
    superseded_checks = [c for c in checks if c.get('decision') == 'superseded_by_newer_patch']

    if not real_notes:
        status = 'not_available'
        summary = 'no machine-checkable patch/improvement notes'
    elif missing:
        status = 'warn'
        summary = f"patch notes checked: {len(applied)} applied, {len(missing)} need review"
    elif applicable:
        status = 'pass'
        summary = f"patch notes checked: {len(applied)} applied"
    else:
        status = 'not_applicable'
        summary = f"patch notes present but not applicable to this source: {len(skipped)} skipped"

    context['patch_result'] = {
        'status': status,
        'patch_hits': applicable,
        'patch_checks': checks,
        'patch_application_log': checks,
        'violations': missing,
        'quality': {
            'input_patch_count': len(patch_notes),
            'real_patch_count': len(real_notes),
            'applicable_patch_count': len(real_applicable),
            'active_applicable_patch_count': len(real_applicable),
            'review_item_count': len(missing),
            'applied_count': len(applied),
            'missing_count': len(missing),
            'skipped_not_applicable_count': len(skipped),
            'superseded_count': len(superseded_checks),
            'active_conflict_count': len(conflict_checks),
        },
    }
    return record_agent(context, AGENT_NAME, {'summary': summary})
