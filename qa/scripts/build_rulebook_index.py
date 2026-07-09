#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

QA_ROOT = Path(__file__).resolve().parents[1]
PROJECT = QA_ROOT.parent
DEFAULT_PDF = QA_ROOT / 'rulebook' / 'rulebook.pdf'
DEFAULT_OUT = QA_ROOT / 'indexes' / 'rulebook'

SECTION_STOPWORDS = {
    'the elder scrolls: betrayal of the second era',
    'table of contents',
    'components',
    'rulebook',
    'version 1.0',
}

TRIGGER_PATTERNS = [
    'must', 'may', 'cannot', 'can', 'if possible', 'if successful', 'after each time',
    'after', 'before', 'during', 'until', 'at the start', 'at the end', 'regardless of location',
    'that adventurer', 'each adventurer', 'all adventurers', 'enemy', 'enemies', 'target',
    'range', 'sight', 'adjacent', 'closest', 'farthest', 'impassable', 'occupied', 'unoccupied',
    'recover', 'heal', 'damage', 'fatigue', 'overfatigue', 'tenacity', 'unstable', 'persistent',
    'deploy', 'defeat', 'discard', 'draw', 'roll', 'reroll', 'exhaust', 'refresh',
]

ENTITY_PATTERNS = [
    'adventurer', 'enemy', 'companion', 'quest unit', 'cache', 'item', 'skill', 'battle', 'round',
    'hex', 'tile', 'delve', 'overland', 'town', 'province', 'daedra', 'humanoid', 'beast',
]


def normalize_ws(text: str) -> str:
    text = text.replace('\x08', '')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_text_pages(source: Path) -> tuple[list[dict], str]:
    suffix = source.suffix.lower()
    if suffix == '.pdf':
        try:
            import fitz  # type: ignore
        except Exception as exc:
            raise SystemExit(
                'PyMuPDF is required for PDF extraction. Install it in the active venv or run with a .txt/.md source. '
                f'Import error: {exc}'
            )
        doc = fitz.open(str(source))
        pages = []
        for idx in range(doc.page_count):
            text = doc[idx].get_text('text')
            pages.append({'page': idx + 1, 'text': text})
        doc.close()
        return pages, 'pdf'
    text = source.read_text(encoding='utf-8')
    # Treat form-feed as page break if present; otherwise one pseudo-page.
    raw_pages = text.split('\f') if '\f' in text else [text]
    return [{'page': idx + 1, 'text': page} for idx, page in enumerate(raw_pages)], 'text'


def is_toc_or_noise_line(line: str) -> bool:
    clean = normalize_ws(line)
    if not clean:
        return True
    # PDF TOC rows use long dot leaders and page numbers; they are navigation, not rules.
    if re.search(r'\.{5,}\s*\d+[A-Za-z]?$', clean):
        return True
    if clean.lower() == 'table of contents':
        return True
    return False


def looks_like_heading(line: str) -> bool:
    clean = normalize_ws(line).strip(' .')
    if not clean:
        return False
    low = clean.lower()
    if low in SECTION_STOPWORDS:
        return True
    if len(clean) > 80:
        return False
    if re.match(r'^\d+$', clean):
        return False
    if re.search(r'[.!?:;]$', clean):
        return False
    words = clean.split()
    if len(words) <= 6 and sum(1 for w in words if w[:1].isupper()) >= max(1, len(words) // 2):
        return True
    return False


def sentence_split(text: str) -> list[str]:
    text = normalize_ws(text)
    if not text:
        return []
    # Keep rule-like semicolon clauses together unless very long.
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9\[])', text)
    out = []
    for part in parts:
        part = normalize_ws(part)
        if len(part) > 450:
            out.extend([normalize_ws(x) for x in re.split(r'(?<=;)\s+', part) if normalize_ws(x)])
        elif part:
            out.append(part)
    return out


def iter_meaning_units(pages: list[dict]) -> Iterable[dict]:
    section = 'Unsectioned'
    paragraph_lines: list[str] = []
    paragraph_page = 1

    def flush() -> list[dict]:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return []
        paragraph = normalize_ws(' '.join(paragraph_lines))
        paragraph_lines = []
        if not paragraph or len(paragraph) < 12:
            return []
        return [{'page': paragraph_page, 'section': section, 'raw_text': sent} for sent in sentence_split(paragraph) if len(sent) >= 12]

    for page in pages:
        page_no = page['page']
        for raw in page.get('text', '').splitlines():
            line = normalize_ws(raw)
            if is_toc_or_noise_line(line):
                for unit in flush():
                    yield unit
                continue
            if re.fullmatch(r'\d+', line):
                continue
            if looks_like_heading(line):
                for unit in flush():
                    yield unit
                section = line.strip(' .')
                continue
            if not paragraph_lines:
                paragraph_page = page_no
            paragraph_lines.append(line)
        for unit in flush():
            yield unit


def detect_triggers(text: str) -> list[str]:
    low = text.lower()
    hits = []
    for trigger in TRIGGER_PATTERNS:
        if trigger in low:
            hits.append(trigger)
    # Add dice/number patterns as triggers.
    if re.search(r'\bD6\b|\bdie\b|\bdice\b|\broll\b', text, re.I):
        hits.append('dice')
    if re.search(r'\b\d+\b', text):
        hits.append('number')
    return sorted(set(hits), key=hits.index)


def detect_entities(text: str) -> list[str]:
    low = text.lower()
    return [entity for entity in ENTITY_PATTERNS if entity in low]


def detect_modality(text: str) -> list[str]:
    low = text.lower()
    vals = []
    for modal in ['must', 'may', 'cannot', 'can']:
        if re.search(rf'\b{modal}\b', low):
            vals.append(modal)
    return vals


def detect_scope(text: str) -> list[str]:
    low = text.lower()
    vals = []
    scope_terms = ['that adventurer', 'each adventurer', 'all adventurers', 'each enemy', 'all enemies', 'target', 'regardless of location', 'adjacent', 'range', 'sight']
    for term in scope_terms:
        if term in low:
            vals.append(term)
    return vals


def detect_timing(text: str) -> list[str]:
    low = text.lower()
    vals = []
    for term in ['after each time', 'after', 'before', 'during', 'until', 'at the start', 'at the end', 'defeats', 'round', 'turn']:
        if term in low:
            vals.append(term)
    return vals


def detect_axes(text: str) -> list[str]:
    axes = []
    if detect_modality(text):
        axes.append('modal_force')
    if detect_scope(text):
        axes.append('target_scope')
    if detect_timing(text):
        axes.append('timing')
    if any(x in text.lower() for x in ['range', 'sight', 'adjacent', 'hex', 'tile', 'location']):
        axes.append('spatial')
    if re.search(r'\b\d+\b|D6|dice|die', text, re.I):
        axes.append('numbers_dice')
    return sorted(set(axes), key=axes.index)


def summarize(text: str) -> str:
    text = normalize_ws(text)
    if len(text) <= 180:
        return text
    return text[:177].rstrip() + '...'


def make_rule_id(page: int, section: str, raw_text: str, ordinal: int) -> str:
    digest = hashlib.sha1(f'{page}\n{section}\n{raw_text}'.encode('utf-8')).hexdigest()[:10]
    return f'RB-P{page:03d}-{ordinal:04d}-{digest}'


def build_units(source: Path) -> tuple[list[dict], dict]:
    pages, source_format = extract_text_pages(source)
    units = []
    seen = set()
    for ordinal, unit in enumerate(iter_meaning_units(pages), start=1):
        raw_text = normalize_ws(unit['raw_text'])
        key = (unit['page'], unit['section'], raw_text.lower())
        if key in seen:
            continue
        seen.add(key)
        row = {
            'rule_id': make_rule_id(unit['page'], unit['section'], raw_text, ordinal),
            'source_file': str(source),
            'page': unit['page'],
            'section': unit['section'],
            'raw_text': raw_text,
            'summary': summarize(raw_text),
            'triggers': detect_triggers(raw_text),
            'entities': detect_entities(raw_text),
            'modality': detect_modality(raw_text),
            'scope': detect_scope(raw_text),
            'timing': detect_timing(raw_text),
            'rule_axes': detect_axes(raw_text),
            'status': 'active',
            'supersedes': [],
        }
        units.append(row)
    meta = {
        'source_file': str(source),
        'source_format': source_format,
        'pages': len(pages),
        'rule_units': len(units),
    }
    return units, meta


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + ('\n' if rows else ''), encoding='utf-8')


def write_sqlite(path: Path, rows: list[dict], meta: dict) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('''CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
    for key, value in meta.items():
        con.execute('INSERT INTO metadata(key, value) VALUES (?, ?)', (key, json.dumps(value, ensure_ascii=False)))
    con.execute('''
        CREATE TABLE rule_units (
            rule_id TEXT PRIMARY KEY,
            page INTEGER,
            section TEXT,
            raw_text TEXT,
            summary TEXT,
            triggers_json TEXT,
            entities_json TEXT,
            modality_json TEXT,
            scope_json TEXT,
            timing_json TEXT,
            rule_axes_json TEXT,
            status TEXT,
            supersedes_json TEXT
        )
    ''')
    con.execute("CREATE VIRTUAL TABLE rule_units_fts USING fts5(rule_id UNINDEXED, section, raw_text, summary, triggers, entities, axes)")
    for row in rows:
        con.execute('''
            INSERT INTO rule_units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            row['rule_id'], row['page'], row['section'], row['raw_text'], row['summary'],
            json.dumps(row['triggers'], ensure_ascii=False), json.dumps(row['entities'], ensure_ascii=False),
            json.dumps(row['modality'], ensure_ascii=False), json.dumps(row['scope'], ensure_ascii=False),
            json.dumps(row['timing'], ensure_ascii=False), json.dumps(row['rule_axes'], ensure_ascii=False),
            row['status'], json.dumps(row['supersedes'], ensure_ascii=False),
        ))
        con.execute('INSERT INTO rule_units_fts VALUES (?, ?, ?, ?, ?, ?, ?)', (
            row['rule_id'], row['section'], row['raw_text'], row['summary'],
            ' '.join(row['triggers']), ' '.join(row['entities']), ' '.join(row['rule_axes']),
        ))
    con.commit()
    con.close()


def write_report(path: Path, rows: list[dict], meta: dict) -> None:
    sections = Counter(row['section'] for row in rows)
    axes = Counter(axis for row in rows for axis in row['rule_axes'])
    lines = [
        '# Rulebook preflight report',
        '',
        f"- source_file: {meta['source_file']}",
        f"- source_format: {meta['source_format']}",
        f"- pages: {meta['pages']}",
        f"- rule units: {meta['rule_units']}",
        '',
        '## Top sections',
    ]
    for section, count in sections.most_common(20):
        lines.append(f'- {section}: {count}')
    lines += ['', '## Rule axes']
    for axis, count in axes.most_common():
        lines.append(f'- {axis}: {count}')
    lines += ['', '## Sample rule units']
    for row in rows[:12]:
        lines.append(f"- `{row['rule_id']}` p.{row['page']} / {row['section']}: {row['summary']}")
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Preflight: build TES rulebook meaning-unit index.')
    parser.add_argument('--source', default=str(DEFAULT_PDF), help='Rulebook PDF/TXT/MD path')
    parser.add_argument('--out-dir', default=str(DEFAULT_OUT), help='Output directory for rule bank')
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, meta = build_units(source)
    meta['builder'] = 'build_rulebook_index.py'
    meta['schema_version'] = 1
    write_jsonl(out_dir / 'rule_bank.jsonl', rows)
    write_sqlite(out_dir / 'rule_bank.sqlite', rows, meta)
    write_report(out_dir / 'rulebook_preflight_report.md', rows, meta)
    (out_dir / 'rulebook_preflight_manifest.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(out_dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
