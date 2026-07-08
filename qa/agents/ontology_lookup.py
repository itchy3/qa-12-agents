from __future__ import annotations

import json
import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

QA_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = QA_ROOT.parents[1]
DEFAULT_ONTOLOGY_ROOT = QA_ROOT / 'external_ontology_download' / 'extracted' / 'ontology'
DEFAULT_DB = DEFAULT_ONTOLOGY_ROOT / 'index' / 'tes_ontology.sqlite'
DEFAULT_LOOKUP_INDEX = QA_ROOT / 'indexes' / 'tes_ontology_lookup.json'
DEFAULT_LOOKUP_DB = QA_ROOT / 'indexes' / 'tes_ontology_lookup.sqlite'

_STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'each', 'for', 'from', 'has', 'have', 'if', 'in', 'into',
    'is', 'it', 'of', 'on', 'or', 'that', 'the', 'then', 'this', 'to', 'with', 'you', 'your', 'gain', 'lose',
    'draw', 'card', 'cards', 'turn', 'turns', 'choice', 'choose', 'after', 'before', 'during', 'may', 'must',
}

_HANGUL_RE = re.compile(r'[가-힣]')
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]*(?:\s+[A-Za-z][A-Za-z0-9'’\-]*)*")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]*")


def ontology_db_path() -> Path | None:
    explicit = os.environ.get('TES_ONTOLOGY_DB')
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    root = os.environ.get('TES_ONTOLOGY_ROOT')
    if root:
        p = Path(root).expanduser() / 'index' / 'tes_ontology.sqlite'
        return p if p.exists() else None
    return DEFAULT_DB if DEFAULT_DB.exists() else None


def ontology_root() -> Path | None:
    explicit = os.environ.get('TES_ONTOLOGY_ROOT')
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    return DEFAULT_ONTOLOGY_ROOT if DEFAULT_ONTOLOGY_ROOT.exists() else None


@lru_cache(maxsize=1)
def _connect(db_path_str: str):
    con = sqlite3.connect(f'file:{db_path_str}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def _load_json(text: str | None) -> dict:
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _candidate_phrases(source_text: str, limit: int = 120) -> list[str]:
    tokens = [m.group(0).replace('’', "'") for m in _TOKEN_RE.finditer(source_text or '')]
    phrases: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = re.sub(r'\s+', ' ', s.strip(" \t\r\n.,;:!?()[]{}\"“”‘’"))
        if not s:
            return
        key = s.lower()
        if key in seen:
            return
        # Single broad/common words create too many false positives. Keep capitalized/lore-looking singles only.
        if ' ' not in s:
            if key in _STOPWORDS or len(key) < 4:
                return
            if not (s[:1].isupper() or '-' in s or key in {'nirn', 'mora', 'daedra', 'daedric', 'ayleid', 'mundus', 'oblivion', 'apocrypha'}):
                return
        seen.add(key)
        phrases.append(s)

    # Prefer exact surface spans and longest n-grams first.
    for n in range(5, 0, -1):
        for i in range(0, max(0, len(tokens) - n + 1)):
            add(' '.join(tokens[i:i+n]))
            if len(phrases) >= limit:
                return phrases
    return phrases


def _ko_values_from_term(obj: dict) -> list[dict]:
    values = []
    for cand in obj.get('ko_candidates') or []:
        if isinstance(cand, str):
            value = cand
            meta = {}
        elif isinstance(cand, dict):
            value = cand.get('value') or cand.get('ko') or cand.get('canonical_ko')
            meta = cand
        else:
            continue
        if not value or not isinstance(value, str):
            continue
        if not _HANGUL_RE.search(value):
            continue
        if value.startswith('[review from JP]'):
            continue
        values.append({
            'value': value.strip(),
            'authority': meta.get('authority') or meta.get('basis') or obj.get('strategy'),
            'status': meta.get('status') or obj.get('ko_status'),
            'source_ref': meta.get('source_ref'),
        })
    return values


def _ko_values_from_node(obj: dict) -> list[dict]:
    values = []
    names = obj.get('names') or {}
    for value in names.get('ko_candidates') or []:
        if isinstance(value, str) and _HANGUL_RE.search(value) and not value.startswith('[review from JP]'):
            values.append({'value': value.strip(), 'authority': 'ontology_node', 'status': 'candidate', 'source_ref': None})
    return values


def _rank_ko_candidate(c: dict) -> tuple[int, int]:
    status = str(c.get('status') or '').lower()
    authority = str(c.get('authority') or '').lower()
    value = str(c.get('value') or '')
    score = 0
    if status in {'approved', 'dictionary'}:
        score += 100
    elif status in {'patch', 'official'}:
        score += 95
    elif status in {'discussion'}:
        score += 70
    elif status in {'candidate'}:
        score += 50
    if 'google_sheet' in authority or 'patch' in authority or 'korean' in authority:
        score += 20
    if value and not re.search(r'[A-Za-z]', value):
        score += 5
    return (-score, len(value))


def _merge_hit_rows(rows: list[dict], source_span: str) -> dict | None:
    if not rows:
        return None
    entity_id = rows[0].get('entity_id') or rows[0].get('id')
    canonical_en = rows[0].get('en') or rows[0].get('canonical_name') or source_span
    entity_type = rows[0].get('type') or 'Term'
    ko_values: list[dict] = []
    source_refs: list[str] = []
    strategies: list[str] = []
    statuses: list[str] = []
    ids: list[str] = []
    for row in rows:
        if row.get('id'):
            ids.append(str(row.get('id')))
        obj = row.get('obj') or {}
        if row.get('kind') == 'term':
            ko_values.extend(_ko_values_from_term(obj))
            if row.get('strategy'):
                strategies.append(row['strategy'])
            if obj.get('ko_status'):
                statuses.append(obj['ko_status'])
        else:
            ko_values.extend(_ko_values_from_node(obj))
        source_refs.extend([x for x in (obj.get('source_refs') or []) if isinstance(x, str)])
        if obj.get('entity_id') and not entity_id:
            entity_id = obj['entity_id']
        if obj.get('canonical_name') and not canonical_en:
            canonical_en = obj['canonical_name']
    dedup = {}
    for c in ko_values:
        dedup.setdefault(c['value'], c)
    ko_values = sorted(dedup.values(), key=_rank_ko_candidate)
    canonical_ko = ko_values[0]['value'] if ko_values else None
    ko_status = ko_values[0].get('status') if ko_values else None
    aliases_ko = [c['value'] for c in ko_values[1:5]]
    confidence = 0.9 if canonical_ko and str(ko_status).lower() in {'approved', 'dictionary'} else (0.72 if canonical_ko else 0.55)
    return {
        'id': entity_id or (ids[0] if ids else source_span),
        'entity_id': entity_id or (ids[0] if ids else source_span),
        'type': entity_type,
        'canonical_en': canonical_en,
        'term': canonical_en,
        'aliases_en': [],
        'canonical_ko': canonical_ko,
        'ko': canonical_ko,
        'aliases_ko': aliases_ko,
        'allowed_variants_ko': [c['value'] for c in ko_values[:6]],
        'ko_status': ko_status,
        'ko_candidates': ko_values[:6],
        'source_span': source_span,
        'source_refs': sorted(set(source_refs))[:8],
        'strategy': strategies[0] if strategies else None,
        'strategies': sorted(set(strategies))[:6],
        'statuses': sorted(set(statuses))[:6],
        'confidence': confidence,
        'source': 'tes_ontology.sqlite',
    }


def lookup_index_path() -> Path:
    # Backward-compatible JSON path; not used for normal lookup.
    explicit = os.environ.get('TES_ONTOLOGY_LOOKUP_INDEX')
    return Path(explicit).expanduser() if explicit else DEFAULT_LOOKUP_INDEX


def lookup_db_path() -> Path:
    explicit = os.environ.get('TES_ONTOLOGY_LOOKUP_DB')
    return Path(explicit).expanduser() if explicit else DEFAULT_LOOKUP_DB


def _index_score(hit: dict) -> int:
    status = str(hit.get('ko_status') or '').lower()
    score = 0
    if hit.get('canonical_ko'):
        score += 100
    if status in {'approved', 'dictionary'}:
        score += 40
    elif status == 'discussion':
        score += 20
    score += int(float(hit.get('confidence') or 0) * 10)
    return score


def build_compact_lookup_index(output_path: Path | None = None, max_node_rows: int | None = None) -> dict:
    """Build indexed key-value SQLite for fast source-term -> ontology hit lookup."""
    db = ontology_db_path()
    if not db:
        raise FileNotFoundError('TES ontology SQLite not found')
    output_path = output_path or lookup_db_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + '.tmp')
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(tmp)
    out.execute('pragma journal_mode=OFF')
    out.execute('pragma synchronous=OFF')
    out.execute('create table meta(key text primary key, value text not null)')
    out.execute('create table entries(key text primary key, canonical_en text, entity_id text, score integer not null, hit_json text not null)')
    src = _connect(str(db))
    term_rows = 0
    node_rows = 0
    upserts = 0

    def maybe_put(key: str, hit: dict) -> None:
        nonlocal upserts
        score = _index_score(hit)
        old = out.execute('select score from entries where key=?', (key,)).fetchone()
        if old is not None and int(old[0]) >= score:
            return
        out.execute(
            'insert or replace into entries(key, canonical_en, entity_id, score, hit_json) values(?,?,?,?,?)',
            (key, hit.get('canonical_en'), hit.get('entity_id'), score, json.dumps(hit, ensure_ascii=False, separators=(',', ':'))),
        )
        upserts += 1

    for r in src.execute('select id, entity_id, en, strategy, json from terms'):
        term_rows += 1
        en = str(r['en'] or '').strip()
        if not en:
            continue
        obj = _load_json(r['json'])
        hit = _merge_hit_rows([{'kind': 'term', 'id': r['id'], 'entity_id': r['entity_id'], 'en': r['en'], 'strategy': r['strategy'], 'obj': obj}], en)
        if hit:
            maybe_put(en.lower(), hit)
        if term_rows % 50000 == 0:
            out.commit()
    for r in src.execute('select id, type, canonical_name, json from nodes'):
        node_rows += 1
        if max_node_rows is not None and node_rows > max_node_rows:
            break
        name = str(r['canonical_name'] or '').strip()
        if not name:
            continue
        key = name.lower()
        if out.execute('select 1 from entries where key=?', (key,)).fetchone() is not None:
            continue
        obj = _load_json(r['json'])
        hit = _merge_hit_rows([{'kind': 'node', 'id': r['id'], 'entity_id': r['id'], 'canonical_name': r['canonical_name'], 'type': r['type'], 'obj': obj}], name)
        if hit:
            maybe_put(key, hit)
        if node_rows % 50000 == 0:
            out.commit()
    entry_count = out.execute('select count(*) from entries').fetchone()[0]
    for k, v in {
        'source_db': str(db),
        'entry_count': str(entry_count),
        'term_rows_scanned': str(term_rows),
        'node_rows_scanned': str(node_rows),
        'upserts': str(upserts),
    }.items():
        out.execute('insert or replace into meta(key,value) values(?,?)', (k, v))
    out.commit()
    out.close()
    tmp.replace(output_path)
    return {'source_db': str(db), 'entry_count': entry_count, 'term_rows_scanned': term_rows, 'node_rows_scanned': node_rows, 'upserts': upserts}


@lru_cache(maxsize=1)
def _connect_lookup_db(path_str: str):
    path = Path(path_str)
    if not path.exists():
        return None
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def _lookup_from_compact_index(source_text: str, max_hits: int) -> tuple[list[dict], dict] | None:
    path = lookup_db_path()
    con = _connect_lookup_db(str(path))
    if con is None:
        return None
    phrases = _candidate_phrases(source_text)
    hits = []
    seen_entities: set[str] = set()
    for phrase in phrases:
        row = con.execute('select hit_json from entries where key=?', (phrase.lower(),)).fetchone()
        if row is None:
            continue
        hit = json.loads(row['hit_json'])
        hit['source_span'] = phrase
        key = str(hit.get('entity_id') or hit.get('id') or hit.get('canonical_en')).lower()
        if key in seen_entities:
            continue
        seen_entities.add(key)
        hits.append(hit)
        if len(hits) >= max_hits:
            break
    quality = {
        'ontology_available': True,
        'ontology_root': str(ontology_root()) if ontology_root() else None,
        'ontology_db': str(ontology_db_path()) if ontology_db_path() else None,
        'lookup_db': str(path),
        'candidate_phrases': len(phrases),
        'matched_phrases': len(hits),
        'hits_returned': len(hits),
        'warnings': [],
    }
    return hits, quality


def lookup_ontology_hits(source_text: str, max_hits: int = 12) -> tuple[list[dict], dict]:
    compact = _lookup_from_compact_index(source_text, max_hits)
    if compact is not None:
        return compact
    db = ontology_db_path()
    root = ontology_root()
    quality = {
        'ontology_available': bool(db),
        'ontology_root': str(root) if root else None,
        'ontology_db': str(db) if db else None,
        'candidate_phrases': 0,
        'matched_phrases': 0,
        'hits_returned': 0,
        'warnings': [],
    }
    if not db:
        quality['warnings'].append('ontology_db_missing')
        return [], quality
    phrases = _candidate_phrases(source_text)
    quality['candidate_phrases'] = len(phrases)
    con = _connect(str(db))
    hits: list[dict] = []
    seen_entities: set[str] = set()
    for phrase in phrases:
        rows: list[dict] = []
        try:
            for r in con.execute(
                "select id, entity_id, en, strategy, json from terms where lower(en)=lower(?) limit 20",
                (phrase,),
            ):
                obj = _load_json(r['json'])
                rows.append({'kind': 'term', 'id': r['id'], 'entity_id': r['entity_id'], 'en': r['en'], 'strategy': r['strategy'], 'obj': obj})
            for r in con.execute(
                "select id, type, canonical_name, json from nodes where lower(canonical_name)=lower(?) limit 8",
                (phrase,),
            ):
                obj = _load_json(r['json'])
                rows.append({'kind': 'node', 'id': r['id'], 'entity_id': r['id'], 'canonical_name': r['canonical_name'], 'type': r['type'], 'obj': obj})
        except Exception as exc:
            quality['warnings'].append(f'lookup_error:{type(exc).__name__}')
            continue
        hit = _merge_hit_rows(rows, phrase)
        if not hit:
            continue
        key = str(hit.get('entity_id') or hit.get('id') or hit.get('canonical_en')).lower()
        # Allow longer exact title to outrank its short duplicate; skip duplicate entities.
        if key in seen_entities:
            continue
        seen_entities.add(key)
        hits.append(hit)
        quality['matched_phrases'] += 1
        if len(hits) >= max_hits:
            break
    quality['hits_returned'] = len(hits)
    return hits, quality
