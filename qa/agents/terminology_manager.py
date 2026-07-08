from __future__ import annotations

import json
import re
from pathlib import Path
from .shared import record_agent

AGENT_NAME = 'terminology-manager'
TERM_DB_PATH = Path(__file__).resolve().parents[1] / 'memory' / 'term_db.jsonl'

MECHANIC_NOUNS = [
    'fatigue', 'damage', 'wound', 'token', 'resource', 'objective', 'hex', 'tile',
    'enemy', 'adventurer', 'choice', 'condition', 'effect', 'health', 'stamina',
]
UNKNOWN_REVIEW_NOUNS = ['fatigue', 'damage', 'wound', 'token', 'resource', 'objective', 'condition']
STOP_PREFIXES = {'each', 'every', 'all', 'any', 'this', 'that', 'the', 'a', 'an', 'one', 'two', 'three'}


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip().lower())


def _split_ko(value: str) -> list[str]:
    parts = re.split(r'[,/;]|\s+or\s+', value or '')
    return [p.strip() for p in parts if p.strip()]


def _load_term_db() -> dict[str, list[dict]]:
    terms: dict[str, list[dict]] = {}
    if not TERM_DB_PATH.exists():
        return terms
    for line in TERM_DB_PATH.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get('status') != 'approved':
            continue
        en = _norm(str(row.get('en', '')))
        ko = str(row.get('ko', '')).strip()
        if not en or not ko:
            continue
        terms.setdefault(en, []).append(row)
    return terms


def _status_is_approved(status) -> bool:
    if status is True:
        return True
    return str(status or '').strip().lower() in {'approved', 'y', 'yes', 'true', '승인', '확정'}


def _iter_context_glossary_rows(context: dict):
    for row in context.get('term_glossary') or []:
        yield row
    for row in context.get('input_card', {}).get('term_glossary') or []:
        yield row


def _context_glossary_terms(context: dict) -> dict[str, list[dict]]:
    terms: dict[str, list[dict]] = {}
    for row in _iter_context_glossary_rows(context):
        if isinstance(row, dict):
            en = row.get('en') or row.get('source') or row.get('term') or row.get('english')
            ko = row.get('ko') or row.get('target') or row.get('korean')
            status = row.get('status', 'approved')
            if en and ko and _status_is_approved(status):
                row_out = dict(row)
                row_out.update({'en': str(en), 'ko': str(ko), 'source': row.get('source', 'context_glossary')})
                terms.setdefault(_norm(str(en)), []).append(row_out)
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            en, ko = row[0], row[1]
            if en and ko:
                terms.setdefault(_norm(str(en)), []).append({'en': str(en), 'ko': str(ko), 'source': 'context_glossary'})
    return terms


def _approved_terms(context: dict) -> dict[str, list[dict]]:
    terms = _load_term_db()
    for en, rows in _context_glossary_terms(context).items():
        terms.setdefault(en, []).extend(rows)
    return terms


def _term_variants(term: str) -> set[str]:
    term = _norm(term)
    variants = {term}
    words = term.split()
    if not words:
        return variants
    last = words[-1]
    plural_forms = {last + 's'}
    if last.endswith('y') and len(last) > 1:
        plural_forms.add(last[:-1] + 'ies')
    if last.endswith(('s', 'x', 'ch', 'sh')):
        plural_forms.add(last + 'es')
    for plural in plural_forms:
        variants.add(' '.join(words[:-1] + [plural]))
    return variants


def _row_lock_policy(rows: list[dict]) -> str:
    for row in rows:
        policy = str(row.get('lock_policy') or row.get('term_policy') or '').strip().lower()
        category = str(row.get('category') or row.get('term_category') or '').strip().lower()
        if policy in {'blocking', 'locked', 'required', 'strict'}:
            return 'blocking'
        if category in {'rules_term', 'mechanic', 'mechanics', 'keyword', 'proper_noun', 'lore_term'}:
            return 'blocking'
    return 'multi_word_only'


def _is_locked_term(term: str, rows: list[dict] | None) -> bool:
    if not rows:
        return False
    return len(term.split()) >= 2 or _row_lock_policy(rows) == 'blocking'


def _source_slot_terms(context: dict) -> set[str]:
    out: set[str] = set()
    def add(value):
        if isinstance(value, str):
            v = _norm(value)
            if v and not re.fullmatch(r'\d+', v):
                out.add(v)
    slots = context.get('source_analysis', {}).get('source_slots', {})
    for action in slots.get('actions', []) if isinstance(slots, dict) else []:
        if isinstance(action, dict):
            add(action.get('object'))
            add(action.get('target'))
    if isinstance(slots, dict):
        table = slots.get('random_table') or {}
        if isinstance(table, dict):
            add(table.get('roller'))
            for outcome in table.get('outcomes', []) or []:
                if isinstance(outcome, dict):
                    add(outcome.get('object'))
        for choice in slots.get('choices', []) or []:
            if isinstance(choice, dict):
                for action in choice.get('actions', []) or []:
                    if isinstance(action, dict):
                        add(action.get('object'))
                        add(action.get('target'))
    return out


def _find_source_terms(source: str, approved_terms: dict[str, list[dict]], context: dict | None = None) -> tuple[set[str], dict]:
    lower = _norm(source)
    found: set[str] = set()
    variant_terms_matched = 0
    for term in approved_terms:
        for variant in _term_variants(term):
            if re.search(rf'(?<![a-z]){re.escape(variant)}(?![a-z])', lower):
                found.add(term)
                if variant != term:
                    variant_terms_matched += 1
                break
    slot_terms = _source_slot_terms(context or {})
    for slot_term in slot_terms:
        for term in approved_terms:
            if slot_term in _term_variants(term):
                found.add(term)
                if slot_term != term:
                    variant_terms_matched += 1
    noun_alt = '|'.join(re.escape(noun) for noun in UNKNOWN_REVIEW_NOUNS)
    # Capture compact mechanics terms such as "over fatigue", "moon fatigue",
    # "poison token". Work line-by-line so field labels like "description" do
    # not leak across newlines into fake terms such as "description each adventurer".
    for raw_line in source.lower().splitlines():
        line = _norm(raw_line)
        if line in {'choice1', 'choice2', 'choicetype', 'description', 'persistent', 'unstable'}:
            continue
        for m in re.finditer(rf'\b([a-z]+(?:\s+[a-z]+)?\s+(?:{noun_alt}))\b', line):
            term = _norm(m.group(1))
            words = term.split()
            if len(words) < 2 or words[0] in STOP_PREFIXES:
                continue
            found.add(term)
    return found, {
        'source_slot_terms_considered': len(slot_terms),
        'variant_terms_matched': variant_terms_matched,
    }


def _ko_for_term(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for row in rows:
        out.extend(_split_ko(str(row.get('ko', ''))))
    return sorted(set(out), key=len, reverse=True)


def _locked_term_violations(context: dict, source_terms: set[str], approved_terms: dict[str, list[dict]]) -> list[dict]:
    code = context['code']
    ko = context.get('current_ko', '')
    violations: list[dict] = []
    for term in sorted(source_terms):
        rows = approved_terms.get(term)
        if not rows:
            continue
        # Broad single-word glossary rows are ignored unless explicitly locked
        # by lock_policy/category metadata. Those rows need syntax/style context,
        # not blind substring matching.
        if not _is_locked_term(term, rows):
            continue
        approved_ko = _ko_for_term(rows)
        if approved_ko and not any(k in ko for k in approved_ko):
            violations.append({
                'issue_id': f'{code}-TERM_{re.sub(r"[^A-Z0-9]+", "_", term.upper()).strip("_")}_MISMATCH',
                'issue_type': 'Terminology consistency',
                'severity': 'Major',
                'span_source': term,
                'span_ko': '',
                'evidence': f'Approved glossary term `{term}` exists but KO does not contain approved translation(s): {", ".join(approved_ko)}.',
                'suggested_fix': f'Use approved glossary translation for `{term}`: {", ".join(approved_ko)}.',
                'confidence': 0.9,
                'blocks_approval': True,
                'term_source': rows[0].get('source', 'term_db'),
            })
    return violations


def _unknown_rule_term_proposals(context: dict, source_terms: set[str], approved_terms: dict[str, list[dict]]) -> list[dict]:
    code = context['code']
    ko = context.get('current_ko', '')
    proposals: list[dict] = []
    for term in sorted(source_terms):
        if term in approved_terms:
            continue
        noun = term.split()[-1]
        proposals.append({
            'proposal_type': 'glossary_review',
            'issue_id': f'{code}-GLOSSARY_REVIEW_{re.sub(r"[^A-Z0-9]+", "_", term.upper()).strip("_")}',
            'source_term': term,
            'current_ko_excerpt': ko,
            'reason': f'`{term}` appears in source and ends with rules-relevant mechanics noun `{noun}`, but no approved glossary entry was found. Do not treat as a confirmed terminology error until human glossary review.',
            'suggested_action': 'Review whether this source term is real/intentional and add an approved glossary entry if rules-important.',
            'candidate_ko': None,
            'requires_human_approval': True,
        })
    return proposals


def _row_term_class(row: dict) -> str:
    category = str(row.get('category') or row.get('term_category') or '').strip().lower()
    policy = str(row.get('lock_policy') or row.get('term_policy') or '').strip().lower()
    if category in {'proper_noun', 'lore_term', 'named_entity', 'entity'}:
        return 'proper_noun_or_lore'
    if category in {'common_word', 'ordinary_word', 'general_word', 'broad'} or policy in {'broad', 'contextual'}:
        return 'ordinary_word'
    if category in {'rules_term', 'mechanic', 'mechanics', 'keyword'} or policy in {'blocking', 'locked', 'required', 'strict'}:
        return 'rules_term'
    return 'multi_word_term' if len(str(row.get('en') or '').split()) >= 2 else 'ordinary_word'


def _term_class(term: str, rows: list[dict] | None) -> str:
    if not rows:
        return 'unknown_rule_term' if term.split()[-1] in UNKNOWN_REVIEW_NOUNS else 'unknown'
    classes = {_row_term_class(row) for row in rows}
    if 'proper_noun_or_lore' in classes:
        return 'proper_noun_or_lore'
    if 'rules_term' in classes:
        return 'rules_term'
    if 'multi_word_term' in classes:
        return 'multi_word_term'
    return 'ordinary_word'


def _source_terms_by_policy(source_terms: set[str], approved_terms: dict[str, list[dict]]) -> dict[str, list[str]]:
    locked_terms = []
    broad_terms_ignored = []
    unknown_rule_terms = []
    proper_noun_terms = []
    ordinary_words_ignored = []
    for term in sorted(source_terms):
        cls = _term_class(term, approved_terms.get(term))
        if cls == 'proper_noun_or_lore':
            proper_noun_terms.append(term)
            locked_terms.append(term)
        elif term in approved_terms and _is_locked_term(term, approved_terms.get(term)):
            locked_terms.append(term)
        elif cls == 'ordinary_word':
            ordinary_words_ignored.append(term)
            broad_terms_ignored.append(term)
        elif term in approved_terms:
            broad_terms_ignored.append(term)
        else:
            unknown_rule_terms.append(term)
    return {
        'locked_terms': locked_terms,
        'proper_noun_terms': proper_noun_terms,
        'ordinary_words_ignored': ordinary_words_ignored,
        'broad_terms_ignored': broad_terms_ignored,
        'unknown_rule_terms': unknown_rule_terms,
    }


def _terminology_quality(approved_terms: dict[str, list[dict]], context_terms: dict[str, list[dict]], by_policy: dict[str, list[str]], glossary_review_proposals: list[dict], match_stats: dict) -> dict:
    warnings = []
    if context_terms:
        context_found = set(context_terms) & set(by_policy['locked_terms'] + by_policy['broad_terms_ignored'])
        if not context_found:
            warnings.append('context_glossary_loaded_but_no_source_terms_matched')
    return {
        'approved_terms_loaded': len(approved_terms),
        'context_terms_loaded': sum(len(rows) for rows in context_terms.values()),
        'locked_terms_checked': len(by_policy['locked_terms']),
        'lock_policy_terms_checked': sum(1 for term in by_policy['locked_terms'] if _row_lock_policy(approved_terms.get(term, [])) == 'blocking'),
        'broad_terms_ignored': len(by_policy['broad_terms_ignored']),
        'unknown_review_terms': len(glossary_review_proposals),
        'source_slot_terms_considered': int(match_stats.get('source_slot_terms_considered', 0)),
        'variant_terms_matched': int(match_stats.get('variant_terms_matched', 0)),
        'warnings': warnings,
    }


def run(context):
    context_terms = _context_glossary_terms(context)
    approved_terms = _load_term_db()
    for en, rows in context_terms.items():
        approved_terms.setdefault(en, []).extend(rows)
    source_terms, match_stats = _find_source_terms(context.get('source_text', ''), approved_terms, context)
    existing = [x for x in context['facts'].get('issues', []) if 'Terminology' in x.get('issue_type', '')]
    direct = _locked_term_violations(context, source_terms, approved_terms)
    glossary_review_proposals = _unknown_rule_term_proposals(context, source_terms, approved_terms)
    by_policy = _source_terms_by_policy(source_terms, approved_terms)
    quality = _terminology_quality(approved_terms, context_terms, by_policy, glossary_review_proposals, match_stats)

    seen = {x.get('issue_id') for x in context['facts'].setdefault('issues', [])}
    for issue in direct:
        if issue.get('issue_id') not in seen:
            context['facts']['issues'].append(issue)
            seen.add(issue.get('issue_id'))

    violations = existing + [x for x in direct if x.get('issue_id') not in {e.get('issue_id') for e in existing}]
    if any(x.get('severity') in ['Critical', 'Major'] for x in violations):
        status = 'fail'
    elif glossary_review_proposals:
        status = 'needs_glossary_review'
    elif violations:
        status = 'needs_review'
    else:
        status = 'pass'

    context['terminology_result'] = {
        'status': status,
        'violations': violations,
        'glossary_review_proposals': glossary_review_proposals,
        'source_terms_checked': sorted(source_terms),
        'source_terms_by_policy': by_policy,
        'term_classification': {term: _term_class(term, approved_terms.get(term)) for term in sorted(source_terms)},
        'terminology_quality': quality,
    }
    return record_agent(context, AGENT_NAME, {'summary': f'terminology {status}; locked={quality["locked_terms_checked"]}; review={quality["unknown_review_terms"]}'})
