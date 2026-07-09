from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from .shared import record_agent

AGENT_NAME = 'terminology-manager'
TERM_DB_PATH = Path(__file__).resolve().parents[1] / 'memory' / 'term_db.jsonl'
ONTOLOGY_LOOKUP_DB = Path(__file__).resolve().parents[1] / 'indexes' / 'tes_ontology_lookup.sqlite'

MECHANIC_NOUNS = [
    'fatigue', 'damage', 'wound', 'token', 'resource', 'objective', 'hex', 'tile',
    'enemy', 'adventurer', 'choice', 'condition', 'effect', 'health', 'stamina',
]
COMMON_NON_LORE_TERMS = {
    'attack', 'deal', 'gain', 'lose', 'place', 'draw', 'discard', 'exhaust', 'heal',
    'enemy', 'adventurer', 'damage', 'fatigue', 'wound', 'token', 'resource',
    'objective', 'choice', 'condition', 'effect', 'health', 'stamina',
}
UNKNOWN_REVIEW_NOUNS = ['fatigue', 'damage', 'wound', 'token', 'resource', 'objective', 'condition']
STOP_PREFIXES = {'each', 'every', 'all', 'any', 'this', 'that', 'the', 'a', 'an', 'one', 'two', 'three'}

# Data-backed overrides for short locked terms whose glossary rows are too
# compact to carry policy metadata. Keep these term-level (not card-level)
# so one adversarial phrase does not become a one-off exception.
TERM_POLICY_OVERRIDES = {
    'hex': {
        'category': 'rules_term',
        'lock_policy': 'blocking',
        'forbidden_ko': ['비어있는 핵스', '비어있는 헥스', '빈 핵스', '빈 헥스', '핵스', '헥스'],
        'compound_ko': {'unoccupied hex': '빈 칸'},
        'source_refs': [
            'Evidence Ontology/번역 규칙/용어 사전/규칙 용어/hex.md',
            'Evidence Ontology/번역 규칙/용어 사전/규칙 용어/unoccupied.md',
        ],
    },
}

KO_PARTICLE_PREFIXES = tuple(sorted({
    '께서는', '께서', '으로부터', '으로써', '으로서', '이라도', '이라면', '이라고',
    '으로', '에서', '에게', '부터', '까지', '처럼', '보다', '마저', '조차',
    '이나', '나', '라도', '라면', '라고', '이며', '이고', '이다', '인',
    '이', '가', '은', '는', '을', '를', '와', '과', '로', '에', '의', '도', '만',
}, key=len, reverse=True))


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip().lower())


def _split_ko(value: str) -> list[str]:
    parts = re.split(r'[,/;]|\s+or\s+', value or '')
    return [p.strip() for p in parts if p.strip()]


@lru_cache(maxsize=1)
def _ontology_ko_terms() -> tuple[str, ...]:
    if not ONTOLOGY_LOOKUP_DB.exists():
        return tuple()
    terms: set[str] = set()
    try:
        con = sqlite3.connect(str(ONTOLOGY_LOOKUP_DB))
        for (hit_json,) in con.execute('select hit_json from entries'):
            try:
                hit = json.loads(hit_json)
            except Exception:
                continue
            entity_type = str(hit.get('type') or '').lower()
            canonical_ko = str(hit.get('canonical_ko') or hit.get('ko') or '')
            # For observed wrong-entity spans, avoid broad ontology glossary terms
            # (e.g. adjectives/common words). Use page/entity-like KO labels only.
            if entity_type == 'term' and '(' not in canonical_ko:
                continue
            values = []
            for key in ['canonical_ko', 'ko']:
                if hit.get(key):
                    values.append(str(hit[key]))
            for key in ['aliases_ko', 'allowed_variants_ko']:
                raw = hit.get(key)
                if isinstance(raw, list):
                    values.extend(str(x) for x in raw if x)
            for value in values:
                value = value.strip()
                if not re.search(r'[가-힣]', value):
                    continue
                terms.add(value)
                if '(' in value:
                    base = value.split('(', 1)[0].strip()
                    if len(base) >= 2:
                        terms.add(base)
    except Exception:
        return tuple()
    finally:
        try:
            con.close()
        except Exception:
            pass
    filtered = [t for t in terms if 2 <= len(t) <= 24 and not re.search(r'\s', t)]
    return tuple(sorted(filtered, key=len, reverse=True))


def _observed_other_ontology_ko_span(text: str, approved_ko: list[str]) -> str | None:
    approved = {x for x in approved_ko if x}
    for term in _ontology_ko_terms():
        if term in approved:
            continue
        if term and term in text:
            return term
    return None


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
    for row in context.get('glossary_hits_compact') or []:
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
        en = _norm(str(row.get('en') or ''))
        override = TERM_POLICY_OVERRIDES.get(en, {})
        policy = str(row.get('lock_policy') or row.get('term_policy') or override.get('lock_policy') or '').strip().lower()
        category = str(row.get('category') or row.get('term_category') or override.get('category') or '').strip().lower()
        if policy in {'blocking', 'locked', 'required', 'strict'}:
            return 'blocking'
        if category in {'rules_term', 'mechanic', 'mechanics', 'keyword', 'proper_noun', 'lore_term'}:
            return 'blocking'
    return 'multi_word_only'


def _is_locked_term(term: str, rows: list[dict] | None) -> bool:
    if not rows:
        return False
    if any(_row_term_class(row) == 'proper_noun_or_lore' for row in rows):
        return True
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


def _override_for_term(term: str) -> dict:
    return TERM_POLICY_OVERRIDES.get(_norm(term), {})


def _forbidden_ko_for_term(term: str, rows: list[dict]) -> list[str]:
    values: list[str] = []
    override = _override_for_term(term)
    for key in ['forbidden_ko', 'deprecated_ko', 'wrong_ko', 'near_miss_ko']:
        raw = override.get(key)
        if isinstance(raw, list):
            values.extend(str(x) for x in raw)
        elif isinstance(raw, str):
            values.extend(_split_ko(raw))
    for row in rows:
        for key in ['forbidden_ko', 'deprecated_ko', 'wrong_ko', 'near_miss_ko']:
            raw = row.get(key)
            if isinstance(raw, list):
                values.extend(str(x) for x in raw)
            elif isinstance(raw, str):
                values.extend(_split_ko(raw))
    return sorted({v.strip() for v in values if v and v.strip()}, key=len, reverse=True)


def _source_contains_phrase(source: str, phrase: str) -> bool:
    return bool(re.search(rf'(?<![a-z]){re.escape(_norm(phrase))}(?![a-z])', _norm(source)))


def _expected_ko_for_source(term: str, rows: list[dict], source: str) -> tuple[str, list[str]]:
    override = _override_for_term(term)
    compound = override.get('compound_ko') or {}
    if isinstance(compound, dict):
        for phrase, ko in sorted(compound.items(), key=lambda kv: len(kv[0]), reverse=True):
            if _source_contains_phrase(source, phrase):
                return str(ko), [str(ko)]
    approved = _ko_for_term(rows)
    return (approved[0] if approved else ''), approved


def _ko_suffix_allowed(remainder: str) -> bool:
    if not remainder:
        return True
    first = remainder[0]
    if first.isspace() or first in '.,;:!?)]}〉》」』”’"\'·/\\-–—':
        return True
    return any(remainder.startswith(p) for p in KO_PARTICLE_PREFIXES)


def _find_approved_ko_span(text: str, approved: list[str]) -> str | None:
    for term in sorted(approved, key=len, reverse=True):
        if not term:
            continue
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            if _ko_suffix_allowed(text[idx + len(term):]):
                return term
            start = idx + 1
    return None


KO_VERB_INFLECTION_PREFIXES = (
    '합니다', '하십시오', '하시고', '하세요', '하였다', '했습니다', '했다',
    '하며', '하고', '하는', '하면', '해서', '하여', '해도', '했다면',
    '하', '했', '해', '합',
)


def _find_approved_ko_inflected_span(text: str, approved: list[str]) -> str | None:
    """Accept normal Korean predicate inflection for non-lore glossary stems.

    Example: approved action term `공격` should clear `공격합니다`. Proper nouns
    deliberately do not use this path, so `와바잭크` still remains a near-miss
    instead of being accepted as `와바잭` + suffix.
    """
    for term in sorted(approved, key=len, reverse=True):
        if not term:
            continue
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            remainder = text[idx + len(term):]
            if remainder.startswith(KO_VERB_INFLECTION_PREFIXES):
                return term
            start = idx + 1
    return None


def _find_forbidden_ko_span(text: str, forbidden: list[str]) -> str | None:
    for term in sorted(forbidden, key=len, reverse=True):
        if term and term in text:
            return term
    return None


def _find_prefixed_near_miss_span(text: str, approved: list[str]) -> str | None:
    # Catch approved Korean term plus an extra Hangul syllable before a normal
    # boundary, e.g. 와바잭 + 크 -> 와바잭크. Valid particle suffixes are
    # accepted by _find_approved_ko_span and should not be reported here.
    for term in sorted(approved, key=len, reverse=True):
        if not term:
            continue
        pattern = re.escape(term) + r'[가-힣]+'
        for match in re.finditer(pattern, text):
            suffix = text[match.start() + len(term):match.end()]
            if _ko_suffix_allowed(suffix):
                continue
            # Return the shortest non-approved extension that is followed by a
            # legal boundary/particle. For 와바잭크가, report 와바잭크, not the 조사.
            for end in range(match.start() + len(term) + 1, match.end() + 1):
                if _ko_suffix_allowed(text[end:]):
                    return text[match.start():end]
            return match.group(0)
    return None


def _levenshtein_at_most_one(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        j += 1
    return True


_HANGUL_BASE = 0xAC00
_HANGUL_END = 0xD7A3
_CHO = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
_JUNG = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ'
_JONG = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']


def _hangul_jamo_key(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if _HANGUL_BASE <= code <= _HANGUL_END:
            value = code - _HANGUL_BASE
            cho = value // 588
            jung = (value % 588) // 28
            jong = value % 28
            out.append(_CHO[cho])
            out.append(_JUNG[jung])
            if _JONG[jong]:
                out.append(_JONG[jong])
        else:
            out.append(ch)
    return ''.join(out)


def _levenshtein_distance(a: str, b: str, max_distance: int | None = None) -> int:
    if a == b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    previous = list(range(len(a) + 1))
    for j, cb in enumerate(b, start=1):
        current = [j]
        row_min = current[0]
        for i, ca in enumerate(a, start=1):
            cost = 0 if ca == cb else 1
            value = min(previous[i] + 1, current[i - 1] + 1, previous[i - 1] + cost)
            current.append(value)
            row_min = min(row_min, value)
        if max_distance is not None and row_min > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def _short_ko_entity_near_miss(candidate: str, approved: str) -> bool:
    # Short TES names such as 팔머 are exactly where an existential "approved KO
    # appears somewhere" check is weakest: 팔메르 can coexist with 팔머 and slip
    # through. Restrict this fuzzy path to compact Hangul tokens sharing the
    # first syllable so ordinary Korean words do not become broad fuzzy matches.
    if candidate == approved:
        return False
    if not (2 <= len(approved) <= 3 and 2 <= len(candidate) <= len(approved) + 2):
        return False
    if candidate[0] != approved[0]:
        return False
    approved_key = _hangul_jamo_key(approved)
    candidate_key = _hangul_jamo_key(candidate)
    return _levenshtein_distance(approved_key, candidate_key, max_distance=3) <= 3


def _strip_trailing_ko_particle(token: str) -> str:
    for particle in KO_PARTICLE_PREFIXES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[:-len(particle)]
    return token


def _find_internal_near_miss_span(text: str, approved: list[str]) -> str | None:
    # Catch syllable substitutions/deletions/insertions inside compact Korean
    # proper nouns even when the approved form appears elsewhere in the card,
    # e.g. 데슬란드라 and 데스란드라, or short lore terms like 팔머 and 팔메르
    # in the same KO text. This function is only called for rows already
    # classified as proper_noun_or_lore, keeping fuzzy matching out of broad terms.
    approved_hangul = [term for term in approved if re.fullmatch(r'[가-힣]{2,}', term or '')]
    if not approved_hangul:
        return None
    approved_set = set(approved_hangul)
    seen: set[str] = set()
    for match in re.finditer(r'[가-힣]{2,}', text):
        raw = match.group(0)
        candidate = _strip_trailing_ko_particle(raw)
        if candidate in seen or candidate in approved_set:
            continue
        seen.add(candidate)
        if len(candidate) < 2:
            continue
        for term in approved_hangul:
            if len(term) >= 4 and _levenshtein_at_most_one(candidate, term):
                return candidate
            if _short_ko_entity_near_miss(candidate, term):
                return candidate
    return None


def _issue_for_term(context: dict, term: str, rows: list[dict], span_source: str, expected_ko: str, approved_ko: list[str], observed_ko: str | None, reason: str) -> dict:
    code = context['code']
    source_refs = []
    override = _override_for_term(term)
    if isinstance(override.get('source_refs'), list):
        source_refs.extend(override['source_refs'])
    for row in rows:
        ref = row.get('source_ref') or row.get('source_refs') or row.get('source')
        if isinstance(ref, list):
            source_refs.extend(str(x) for x in ref)
        elif ref:
            source_refs.append(str(ref))
    expected_list = approved_ko or ([expected_ko] if expected_ko else [])
    issue_id_source = span_source or term
    return {
        'issue_id': f'{code}-TERM_{re.sub(r"[^A-Z0-9]+", "_", issue_id_source.upper()).strip("_")}_MISMATCH',
        'issue_type': 'Terminology consistency',
        'severity': 'Major',
        'span_source': span_source or term,
        'span_ko': observed_ko or '',
        'evidence': f'{reason} Approved glossary term `{term}` requires KO `{expected_ko}` for source `{span_source or term}`.',
        'suggested_fix': f'Use approved glossary translation for `{span_source or term}`: {expected_ko or ", ".join(expected_list)}.',
        'confidence': 0.94 if observed_ko else 0.9,
        'blocks_approval': True,
        'term_source': rows[0].get('source', 'term_db') if rows else 'term_policy',
        'source_refs': sorted(set(source_refs)),
        'semantic_diff': {
            'field': 'terminology.locked_term',
            'source_value': span_source or term,
            'expected_ko': expected_ko,
            'approved_ko': expected_list,
            'observed_ko': observed_ko,
            'ko_value': observed_ko or 'missing',
        },
    }


def _locked_term_violations(context: dict, source_terms: set[str], approved_terms: dict[str, list[dict]]) -> list[dict]:
    source = context.get('source_text', '')
    ko = context.get('current_ko', '')
    violations: list[dict] = []
    seen_ids: set[str] = set()
    for term in sorted(source_terms):
        rows = approved_terms.get(term)
        if not rows:
            continue
        # Broad single-word glossary rows are ignored unless explicitly locked
        # by lock_policy/category metadata. Those rows need syntax/style context,
        # not blind substring matching.
        if not _is_locked_term(term, rows):
            continue
        expected_ko, approved_ko = _expected_ko_for_source(term, rows, source)
        if not approved_ko:
            continue
        span_source = term
        if term == 'hex' and _source_contains_phrase(source, 'unoccupied hex'):
            span_source = 'unoccupied hex'
        forbidden_span = _find_forbidden_ko_span(ko, _forbidden_ko_for_term(term, rows))
        is_proper_noun_or_lore = any(_row_term_class(row) == 'proper_noun_or_lore' for row in rows)
        # Fuzzy Hangul near-miss checks are valuable for TES proper nouns/lore
        # (와바잭→와바잭크, 팔머→팔메르), but broad action/mechanic stems like
        # 공격 should not flag normal Korean verb inflection such as 공격합니다.
        near_miss = _find_prefixed_near_miss_span(ko, approved_ko) if is_proper_noun_or_lore else None
        if not near_miss and is_proper_noun_or_lore:
            near_miss = _find_internal_near_miss_span(ko, approved_ko)
        if forbidden_span:
            issue = _issue_for_term(context, term, rows, span_source, expected_ko, approved_ko, forbidden_span, f'KO contains forbidden/deprecated variant `{forbidden_span}`.')
        elif near_miss:
            issue = _issue_for_term(
                context, term, rows, span_source, expected_ko, approved_ko, near_miss,
                f'KO contains a near-miss form `{near_miss}` even though the approved form is `{expected_ko}`.'
            )
        elif _find_approved_ko_span(ko, approved_ko) or (not is_proper_noun_or_lore and _find_approved_ko_inflected_span(ko, approved_ko)):
            continue
        else:
            observed_wrong_entity = None
            if is_proper_noun_or_lore:
                observed_wrong_entity = _observed_other_ontology_ko_span(ko, approved_ko)
            issue = _issue_for_term(
                context, term, rows, span_source, expected_ko, approved_ko, observed_wrong_entity,
                f'KO does not contain an approved boundary-safe form of `{expected_ko}`.'
            )
        if issue['issue_id'] not in seen_ids:
            violations.append(issue)
            seen_ids.add(issue['issue_id'])
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


def _looks_like_compact_context_lore(row: dict, en: str) -> bool:
    source = str(row.get('source') or '').strip().lower()
    if _norm(en) in COMMON_NON_LORE_TERMS:
        return False
    if source not in {'context_glossary', 'input_card.term_glossary'}:
        return False
    if not _status_is_approved(row.get('status', 'approved')):
        return False
    ko = str(row.get('ko') or '').strip()
    if not re.search(r'[가-힣]', ko):
        return False
    # Many compact context-pack glossary rows intentionally omit category/policy
    # to save tokens. Preserve obvious TES entity/proper-noun rows such as
    # Wabbajack/Khajiit/Skooma Cat as locked rather than demoting them to broad
    # ordinary words. Lowercase mechanics/common words still need metadata.
    words = [w for w in re.split(r'\s+', en.strip()) if w]
    return bool(words) and all(w[:1].isupper() for w in words if re.search(r'[A-Za-z]', w))


def _row_term_class(row: dict) -> str:
    en = _norm(str(row.get('en') or ''))
    raw_en = str(row.get('en') or '')
    override = TERM_POLICY_OVERRIDES.get(en, {})
    category = str(row.get('category') or row.get('term_category') or override.get('category') or '').strip().lower()
    policy = str(row.get('lock_policy') or row.get('term_policy') or override.get('lock_policy') or '').strip().lower()
    if category in {'proper_noun', 'lore_term', 'named_entity', 'entity'}:
        return 'proper_noun_or_lore'
    if _looks_like_compact_context_lore(row, raw_en):
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
