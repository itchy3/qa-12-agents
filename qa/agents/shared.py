from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
QA_ROOT = PROJECT / 'qa'
ORIG_BASE = PROJECT / 'data' / 'originals'
TRANS_BASE = PROJECT / 'data' / 'translations'
ORIG_BASES = [PROJECT / 'data' / 'originals']
TRANS_BASES = [PROJECT / 'data' / 'translations']
POLICY = {'choice_xp':'omit_allowed','icon_markup':'format_tolerated_if_meaning_preserved','conditional_position':'description_or_battleObjective_allowed_if_scope_clear','auto_apply':False,'memory_update':'proposal_only_until_human_approval'}
PIPELINE_NAMES = ['카드 입력','Context pack 생성','원문 의미 패턴 추출','원문 슬롯 추출','번역문 슬롯 추출','용어집 검사','lore ontology 검사','patch note 검사','번역문 문형 지문 추출','구문사전/문형 규칙 검사','기존 번역 corpus에서 유사 문형 검색','기존 QA 로그와 비교','문형 일관성 판정','가독성 예외 여부 판단','룰 리스크 평가','수정안 생성','수정안 self-verification','최종 QA 판정','카드별 JSON QA 로그 저장','카드별 MD QA 리포트 저장','학습 반영 제안 생성','사람 승인 후 memory 업데이트']
AGENT_SEQUENCE = ['context-pack-builder','source-meaning-checker','terminology-manager','syntax-pattern-controller','inductive-style-learner','cross-card-consistency-checker','lore-ontology-checker','patch-note-checker','rules-lawyer','korean-editor','verifier','qa-reviewer']

CARD_FACTS: dict[str, dict[str, Any]] = {}

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()] if path.exists() else []

def parse_sections(text: str) -> dict[str, list[str]]:
    if text.startswith('---'):
        parts = text.split('---', 2)
        if len(parts) >= 3: text = parts[2]
    keys={'encounterName','description','choice1','choiceType','battleObjective','choice2'}; cur=None; buf=[]; out=[]
    for line in text.splitlines():
        if line.strip() in keys:
            if cur is not None: out.append((cur,'\n'.join(buf).strip()))
            cur=line.strip(); buf=[]
        elif cur is not None: buf.append(line)
    if cur is not None: out.append((cur,'\n'.join(buf).strip()))
    d={}
    for k,v in out: d.setdefault(k,[]).append(v)
    return d


def parse_ordered_sections(text: str) -> list[tuple[str, str]]:
    if text.startswith('---'):
        parts = text.split('---', 2)
        if len(parts) >= 3:
            text = parts[2]
    keys={'encounterName','description','choice1','choiceType','battleObjective','choice2'}
    cur=None; buf=[]; out=[]
    for line in text.splitlines():
        if line.strip() in keys:
            if cur is not None:
                out.append((cur,'\n'.join(buf).strip()))
            cur=line.strip(); buf=[]
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur,'\n'.join(buf).strip()))
    return out

def _choice_scoped_sections(text: str) -> list[dict[str, str]]:
    ordered = parse_ordered_sections(text)
    choices: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for key, value in ordered:
        if key in ['choice1', 'choice2']:
            current = {'choice_key': key, 'choice_text': value}
            choices.append(current)
        elif current is not None and key in ['choiceType', 'description', 'battleObjective']:
            if key in current and current[key]:
                current[key] += '\n' + value
            else:
                current[key] = value
    return choices

def term_hits_for(source_text: str, ko_text: str) -> list[dict[str, Any]]:
    s=source_text.lower(); hits=[]
    for t in read_jsonl(QA_ROOT/'memory/term_db.jsonl'):
        en=str(t.get('en','')).strip().lower(); ko=str(t.get('ko','')).strip()
        probes={en,en.replace('(s)',''),en.replace('~','').strip(),en.replace('(주사위)','').strip()}
        if en and any(p and re.search(r'(?<![a-z])'+re.escape(p)+r'(?![a-z])',s) for p in probes): hits.append(t)
        elif ko and ko in ko_text: hits.append(t)
    seen=set(); out=[]
    for h in hits:
        key=h.get('sheet_row') or json.dumps(h,ensure_ascii=False,sort_keys=True)
        if key not in seen: out.append(h); seen.add(key)
    return out[:30]

def syntax_hits_for(source_text: str, ko_text: str) -> list[dict[str, Any]]:
    s=source_text.lower(); hits=[]
    for r in read_jsonl(QA_ROOT/'memory/syntax_rules.jsonl'):
        pat=str(r.get('source_pattern','')).lower().strip(); kt=str(r.get('ko_template','')); kp=kt.replace('X','').replace('~','').strip()
        if pat and (pat.replace('x','').strip() in s or pat in s): hits.append(r)
        elif kp and kp in ko_text: hits.append(r)
    return hits[:10]

def similar_corpus(patterns: list[str], exclude: str) -> list[dict[str, Any]]:
    results=[]
    for op in sorted(ORIG_BASE.glob('*.md')):
        code=op.name.split(' - ')[0]
        if code==exclude: continue
        txt=op.read_text(encoding='utf-8'); matched=[p for p in patterns if p.lower() in txt.lower()]
        if not matched: continue
        trans=list(TRANS_BASE.glob(code+' - *.md')); ko_lines=[]
        if trans:
            k=trans[0].read_text(encoding='utf-8')
            ko_lines=[line for line in k.splitlines() if any(x in line for x in ['각 모험가','배치','굴','불안정','3+ 인','라운드','고정 피해','가벼운 피로','전투 목표'])]
        src_lines=[line for line in txt.splitlines() if any(p.lower() in line.lower() for p in matched)]
        results.append({'item_id':f'Conflict Encounter/{code}','matched_patterns':matched,'source_lines':src_lines[:3],'ko_lines':ko_lines[:6],'trust':'observed_example'})
        if len(results)>=8: break
    return results

def find_card_files(code: str) -> tuple[Path, Path]:
    orig_matches=[]
    trans_matches=[]
    for base in ORIG_BASES:
        orig_matches.extend(sorted(base.glob(code+' - *.md')))
    for base in TRANS_BASES:
        trans_matches.extend(sorted(base.glob(code+' - *.md')))
    if not orig_matches:
        raise FileNotFoundError(f'No source card file found for {code}')
    orig = orig_matches[0]
    title = orig.stem.split(' - ', 1)[1] if ' - ' in orig.stem else ''
    if title and len(trans_matches) > 1:
        titled = [p for p in trans_matches if title in p.name]
        if titled:
            trans_matches = titled
    if not trans_matches:
        raise FileNotFoundError(f'No translation card file found for {code}')
    return orig, trans_matches[0]

def seeded_range_facts(code: str) -> dict[str, Any] | None:
    # Public package: project-specific seed facts are intentionally excluded.
    # Use --input-json with explicit source_text/current_ko, or add your own private seeds locally.
    return None

def _first_icon(text: str) -> str | None:
    m = re.search(r'\[\[(ICON_[A-Za-z0-9_]+)(?:\|[^\]]+)?\]\]', text)
    return m.group(1) if m else None

def _section_text(sections: dict[str, list[str]], name: str) -> str:
    vals = sections.get(name) or []
    return '\n'.join(v for v in vals if v).strip()

def _parse_rule_action(text: str, include_target: bool = True) -> dict[str, Any] | None:
    cleaned = re.sub(r'\s+', ' ', (text or '').strip()).rstrip('.')
    pattern = re.compile(r'(?:(each adventurer|that adventurer|each enemy|an enemy|you)\s+)?(?:(must|may|cannot|can)\s+)?(discard|discards|recover|recovers|gain|gains|draw|draws|place|places|deal|deals)\s+(\d+)\s+(.+?)(?:,?\s+if possible)?$', re.I)
    m = pattern.search(cleaned)
    if not m:
        return None
    target, modal, verb, amount, obj = m.groups()
    verb = {
        'discards': 'discard',
        'recovers': 'recover',
        'gains': 'gain',
        'draws': 'draw',
        'places': 'place',
        'deals': 'deal',
    }.get(verb.lower(), verb.lower())
    if obj.lower() in ['health']:
        obj = 'health'
    elif obj.lower() in ['item', 'items']:
        obj = 'item'
    elif obj.lower() in ['bonus xp', 'xp']:
        obj = 'bonus XP' if 'bonus' in obj.lower() else 'XP'
    action: dict[str, Any] = {}
    if include_target and target:
        action['target'] = target.lower()
    if modal:
        action['modal'] = modal.lower()
    action.update({'verb': verb.lower(), 'amount': amount, 'object': obj})
    return action


def _parse_rule_actions(text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for part in re.split(r'\n|;|(?<=\.)\s+', text or ''):
        action = _parse_rule_action(part, include_target=True)
        if action:
            actions.append(action)
    return actions


def _parse_random_table(text: str) -> dict[str, Any] | None:
    if not re.search(r'rolls?\s+1\s+D6|roll\s+1\s+D6|rolled result', text or '', re.I):
        return None
    outcomes = []
    for rng, body in re.findall(r'(?m)^\s*(\d+\s*-\s*\d+)\s*:\s*(.+?)\s*$', text or ''):
        action = _parse_rule_action(body, include_target=False) or {'raw': body.strip()}
        action = {'range': rng.replace(' ', ''), **action}
        outcomes.append(action)
    table = {'die': 'D6', 'outcomes': outcomes}
    if re.search(r'each adventurer\s+rolls?', text or '', re.I):
        table['roller'] = 'each adventurer'
    return table if outcomes else None


def extract_source_slots_generic(source: str) -> tuple[str, dict[str, Any], list[str]]:
    whole = source
    scoped = _choice_scoped_sections(source)
    slots: dict[str, Any] = {}
    patterns: list[str] = []
    choices: list[dict[str, Any]] = []
    for choice in scoped:
        choice_text = choice.get('choice_text', '')
        choice_type_text = choice.get('choiceType', '') or choice_text
        description = choice.get('description', '')
        battle = choice.get('battleObjective', '')
        icon = _first_icon(choice_type_text) or _first_icon(choice_text)
        cslot: dict[str, Any] = {'choice_key': choice.get('choice_key', ''), 'choice_text': re.sub(r'\[\[[^\]]+\]\]', '', choice_text).strip()}
        if icon:
            cslot['choice_type_icon'] = icon
            patterns.append(icon)
        if battle:
            cslot['battle_objective'] = battle
            patterns.append('battleObjective')
        m = re.search(r'Unstable:\s*(.+?)(?:\n|$)', description, re.I)
        if m:
            cslot['unstable_effect'] = {'raw': m.group(1).strip()}
            patterns.append('Unstable')
        m = re.search(r'Persistent:\s*(.+?)(?:\n|$)', description, re.I)
        if m:
            raw = m.group(1).strip()
            cslot['persistent_effect'] = {'raw': raw}
            tm = re.search(r'(After each time|Each time|At the start|At the end|When)(.+?),\s*(.+)', raw, re.I)
            if tm:
                cslot['persistent_effect'].update({'timing': (tm.group(1)+tm.group(2)).strip(), 'effect': tm.group(3).strip()})
            patterns.append('Persistent')
        m = re.search(r'3\+\s*players:\s*(.+?)(?:\n|$)', description, re.I)
        if m:
            raw = m.group(1).strip()
            scope = 'each hex' if re.search(r'\b(each|every)\s+hex\b|\ball\s+hexes\b', raw, re.I) else 'unspecified'
            cslot['three_plus_players'] = {'raw': raw, 'scope': scope}
            patterns.append('3+ players')
        actions = _parse_rule_actions(description)
        if actions:
            cslot['actions'] = actions
            patterns.append('rule_actions')
        random_table = _parse_random_table(description)
        if random_table:
            cslot['random_table'] = random_table
            patterns.append('random_table')
        choices.append(cslot)
    if choices:
        slots['choices'] = choices
        first = choices[0]
        for key in ['choice_type_icon','choice_text','battle_objective','unstable_effect','persistent_effect','three_plus_players']:
            if key in first:
                slots[key] = first[key]
    if re.search(r'\beach adventurer\b', whole, re.I):
        slots.setdefault('targets', []).append('each adventurer')
        patterns.append('each adventurer')
    if re.search(r'\b(each|every)\s+hex\b|\ball\s+hexes\b', whole, re.I) and not any('three_plus_players' in c for c in choices):
        slots['board_scope'] = {'scope': 'each hex'}
        patterns.append('each hex')
    first_icon = choices[0].get('choice_type_icon') if choices else _first_icon(whole)
    any_unstable_icon = any(str(c.get('choice_type_icon','')).startswith('ICON_Unstable') for c in choices)
    any_persistent = any('persistent_effect' in c for c in choices)
    any_unstable_text = any('unstable_effect' in c for c in choices) or 'Unstable:' in whole
    any_each_hex_3p = any((c.get('three_plus_players') or {}).get('scope') == 'each hex' for c in choices)
    if first_icon == 'ICON_Unstable_Peaceful' and any_persistent:
        semantic = 'PEACEFUL_UNSTABLE_PERSISTENT_EFFECT'
    elif first_icon == 'ICON_Unstable_Clash' or any(str(c.get('choice_type_icon','')).startswith('ICON_Unstable_Clash') for c in choices):
        semantic = 'CONFLICT_UNSTABLE_CLASH_EFFECT'
    elif any_unstable_icon or any_unstable_text:
        semantic = 'UNSTABLE_CHOICE_EFFECT'
    elif any_each_hex_3p:
        semantic = 'THREE_PLUS_EACH_HEX_MODIFIER'
    elif slots:
        semantic = 'SECTIONED_ENCOUNTER_EFFECT'
    else:
        semantic = 'UNRESOLVED'
    return semantic, slots, patterns

def extract_ko_slots_generic(ko: str) -> dict[str, Any]:
    whole = ko
    scoped = _choice_scoped_sections(ko)
    slots: dict[str, Any] = {}
    choices: list[dict[str, Any]] = []
    for choice in scoped:
        choice_text = choice.get('choice_text', '')
        choice_type_text = choice.get('choiceType', '') or choice_text
        description = choice.get('description', '')
        icon = _first_icon(choice_type_text) or _first_icon(choice_text)
        cslot: dict[str, Any] = {'choice_key': choice.get('choice_key', ''), 'choice_text': re.sub(r'\[\[[^\]]+\]\]', '', choice_text).strip()}
        if icon:
            cslot['choice_type_icon'] = icon
        if choice.get('battleObjective') or '전투 목표' in choice.get('battleObjective',''):
            cslot['battle_objective'] = 'present'
        if '불안정' in description:
            line = next((x.strip() for x in description.splitlines() if '불안정' in x), '')
            cslot['unstable_effect'] = {'raw': line}
        if '지속:' in description or '지속' in description:
            line = next((x.strip() for x in description.splitlines() if x.strip().startswith('지속:')), '')
            if not line:
                line = next((x.strip() for x in description.splitlines() if '지속' in x), '')
            cslot['persistent_effect'] = {'raw': line}
        m = re.search(r'3\+\s*인[:：]?\s*(.+?)(?:\n|$)', description)
        if m:
            raw = m.group(1).strip()
            if '[별]' in raw or '별 칸' in raw:
                scope = 'star hex'
            elif '각 칸' in raw or '모든 칸' in raw:
                scope = 'each hex'
            else:
                scope = 'unspecified'
            cslot['three_plus_players'] = {'raw': raw, 'scope': scope}
        choices.append(cslot)
    if choices:
        slots['choices'] = choices
        first = choices[0]
        for key in ['choice_type_icon','choice_text','battle_objective','unstable_effect','persistent_effect','three_plus_players']:
            if key in first:
                slots[key] = first[key]
    if '전투 목표' in whole and 'battle_objective' not in slots:
        slots['battle_objective'] = 'present'
    return slots


def detect_source_quality_issues(code: str, source_slots: dict[str, Any]) -> list[dict[str, Any]]:
    """Log source/OCR/extracted-text anomalies without treating them as translation QA failures."""
    issues: list[dict[str, Any]] = []
    for idx, choice in enumerate(source_slots.get('choices', []) or [], start=1):
        persistent = choice.get('persistent_effect') if isinstance(choice, dict) else None
        raw = persistent.get('raw', '') if isinstance(persistent, dict) else ''
        marker = 'This persistent is not discarded at the end of a session.'
        if marker in raw:
            tail = raw.split(marker, 1)[1].strip()
            if tail:
                issues.append({
                    'issue_id': f'{code}-SOURCE_TEXT_LEAKAGE-001',
                    'issue_type': 'Source text extraction leakage',
                    'severity': 'SourceWarning',
                    'choice_key': choice.get('choice_key', f'choice{idx}'),
                    'span_source': tail,
                    'evidence': 'Source persistent text appears to include leaked narrative/next-choice text after the persistent-discard sentence.',
                    'suggested_action': 'Log only. Do not auto-fix source or translation; confirm against PDF/source if this affects QA interpretation.',
                    'blocks_translation_approval': False,
                    'requires_source_review': True,
                })
    return issues

def _slot_key_set(value: Any, prefix: str = '') -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            path = f'{prefix}.{k}' if prefix else str(k)
            keys.add(path)
            keys |= _slot_key_set(v, path)
    elif isinstance(value, list):
        for v in value:
            keys |= _slot_key_set(v, prefix)
    elif prefix:
        keys.add(prefix)
    return keys

def compare_seed_to_parser(seed_slots: dict[str, Any] | None, parser_slots: dict[str, Any]) -> dict[str, Any]:
    if not seed_slots:
        return {'coverage': 1.0 if parser_slots else 0.0, 'missing_from_parser': [], 'parser_extra': sorted(_slot_key_set(parser_slots)), 'seed_slot_count': 0, 'parser_slot_count': len(_slot_key_set(parser_slots))}
    seed_keys = _slot_key_set(seed_slots)
    parser_keys = _slot_key_set(parser_slots)
    family_hits = set()
    family_map = {
        'choice': ['choice_type_icon', 'choice_text'],
        'battleObjective': ['battle_objective'],
        'battle_objective': ['battle_objective'],
        'Persistent': ['persistent_effect'],
        'persistent': ['persistent_effect'],
        'Unstable': ['unstable_effect', 'choice_type_icon'],
        '3+': ['three_plus_players'],
        'three_plus': ['three_plus_players'],
    }
    for sk in seed_keys:
        for needle, pks in family_map.items():
            if needle.lower() in sk.lower() and any(pk in parser_keys for pk in pks):
                family_hits.add(sk)
    direct_hits = {sk for sk in seed_keys if any(part in parser_keys for part in sk.split('.'))}
    hits = family_hits | direct_hits
    # Broad family matches prove the parser saw the right section, not every seeded rule detail.
    # Keep the score conservative unless exact nested seed keys are covered.
    exact_hits = seed_keys & parser_keys
    denom = max(1, len(seed_keys))
    weighted_hits = (0.35 * len(family_hits)) + (0.65 * len(exact_hits)) + min(len(parser_keys), 3) * 0.15
    coverage = min(0.95 if len(exact_hits) < len(seed_keys) else 1.0, weighted_hits / denom)
    return {'coverage': round(coverage, 3), 'missing_from_parser': sorted(seed_keys - hits)[:30], 'parser_extra': sorted(parser_keys - seed_keys)[:30], 'seed_slot_count': len(seed_keys), 'parser_slot_count': len(parser_keys), 'exact_seed_key_hits': len(exact_hits), 'family_seed_key_hits': len(family_hits)}

def infer_parser_facts(card: dict[str, Any], seeded: dict[str, Any] | None = None) -> dict[str, Any]:
    source = str(card.get('source_text','')).strip()
    ko = str(card.get('current_ko','')).strip()
    code = str(card.get('card_id') or card.get('code') or 'CARD')
    semantic, source_slots, patterns = extract_source_slots_generic(source)
    ko_slots = extract_ko_slots_generic(ko)
    issues: list[dict[str, Any]] = []
    src_icon = source_slots.get('choice_type_icon')
    ko_icon = ko_slots.get('choice_type_icon')
    if src_icon and str(src_icon).startswith('ICON_Unstable') and (not ko_icon or not str(ko_icon).startswith('ICON_Unstable')):
        issues.append({'issue_id': f'{code}-REG_UNSTABLE_ICON_MISMATCH', 'issue_type': 'Choice type/icon mismatch', 'severity': 'Major', 'span_source': src_icon, 'span_ko': ko_icon or '', 'evidence': 'Source choice icon is unstable but KO choice icon is non-unstable or missing.', 'suggested_fix': f'Use {src_icon} unstable choice icon/choiceType in KO.', 'confidence': 0.94, 'blocks_approval': True})
    src_3p = source_slots.get('three_plus_players') if isinstance(source_slots.get('three_plus_players'), dict) else {}
    ko_3p = ko_slots.get('three_plus_players') if isinstance(ko_slots.get('three_plus_players'), dict) else {}
    source_quality_issues = detect_source_quality_issues(code, source_slots)
    if src_3p.get('scope') == 'each hex' and ko_3p.get('scope') == 'star hex' and '[' in str(ko_3p.get('raw', '')):
        source_quality_issues.append({
            'issue_id': f'{code}-SOURCE_ICON_CRAWL_MISSING-001',
            'issue_type': 'Source crawl/icon omission candidate',
            'severity': 'SourceWarning',
            'span_source': src_3p.get('raw', ''),
            'span_ko': ko_3p.get('raw', ''),
            'evidence': 'KO contains a bracketed board icon marker, while crawled/source text only says each/every hex. Treat as likely source crawl/OCR icon omission, not a translation scope failure.',
            'suggested_action': 'Log only. Confirm against PDF/source image; do not auto-fix translation from crawled English text alone.',
            'blocks_translation_approval': False,
            'requires_source_review': True,
        })
    elif src_3p.get('scope') == 'each hex' and ko_3p.get('scope') == 'star hex':
        issues.append({'issue_id': f'{code}-REG_EACH_HEX_SCOPE_NOT_STAR_HEX', 'issue_type': 'Scope/board-location mismatch', 'severity': 'Major', 'span_source': src_3p.get('raw',''), 'span_ko': ko_3p.get('raw',''), 'evidence': 'Source says each/every hex, but KO narrows the scope to star/special hex.', 'suggested_fix': '각 칸마다 보물을 배치합니다.', 'confidence': 0.92, 'blocks_approval': True})
    lower_source = source.lower()
    if re.search(r'\bmust\b', lower_source) and re.search(r'수 있|가능', ko):
        issues.append({'issue_id': f'{code}-REG_MODAL_MUST_TO_MAY', 'issue_type': 'Modal force mismatch', 'severity': 'Major', 'span_source': 'must', 'span_ko': '할 수 있습니다/가능', 'evidence': 'Source obligation uses must, but KO weakens it into optional may/can wording.', 'suggested_fix': 'must는 “해야 합니다/반드시 …합니다” 계열로 보존합니다.', 'confidence': 0.9, 'blocks_approval': True})
    if 'regardless of location' in lower_source and '위치와 관계없이' not in ko and '위치에 관계없이' not in ko:
        issues.append({'issue_id': f'{code}-REG_SCOPE_REGARDLESS_OMITTED', 'issue_type': 'Scope qualifier omission', 'severity': 'Major', 'span_source': 'regardless of location', 'span_ko': '', 'evidence': 'Source explicitly applies regardless-of-location scope, but KO omits the location-independent qualifier.', 'suggested_fix': '대상 문장에 “위치와 관계없이” 범위를 명시합니다.', 'confidence': 0.9, 'blocks_approval': True})
    if semantic == 'UNRESOLVED':
        issues.append({'issue_id':f'{code}-UNRESOLVED-001','issue_type':'Unresolved Pattern','severity':'Major','evidence':'Generic parser could not infer semantic pattern.','suggested_fix':'사람 검토 필요','confidence':0.2,'blocks_approval':True})
    seed_issues = list(seeded.get('issues', [])) if seeded else []
    seen = {i.get('issue_id') for i in issues}
    for issue in seed_issues:
        if issue.get('issue_id', '').endswith('REG_EACH_HEX_SCOPE_NOT_STAR_HEX') or issue.get('issue_type') == 'Scope/board-location mismatch':
            if '[' in str(issue.get('span_ko', '')):
                source_quality_issues.append({
                    'issue_id': f'{code}-SOURCE_ICON_CRAWL_MISSING-001',
                    'issue_type': 'Source crawl/icon omission candidate',
                    'severity': 'SourceWarning',
                    'span_source': issue.get('span_source', ''),
                    'span_ko': issue.get('span_ko', ''),
                    'evidence': 'Seeded board-scope mismatch includes a bracketed KO icon marker. Per policy, bracketed KO icons can indicate English crawl/source icon omission rather than translation scope narrowing.',
                    'suggested_action': 'Log only. Confirm against PDF/source image; do not auto-fix translation from crawled English text alone.',
                    'blocks_translation_approval': False,
                    'requires_source_review': True,
                })
                continue
        if issue.get('issue_id') not in seen:
            issues.append(issue)
    quality = compare_seed_to_parser(seeded.get('source_slots') if seeded else None, source_slots)
    has_major = any(i.get('severity') in ['Critical','Major'] for i in issues)
    has_note = any(i.get('severity') in ['Note','Minor','Info'] for i in issues)
    score = seeded.get('score') if seeded else (82 if has_major else 94 if has_note else 97)
    verdict = seeded.get('verdict') if seeded else ('Needs revision' if has_major else 'Suggestion' if has_note else 'Pass')
    suggested = seeded.get('suggested_ko') if seeded else ('사람 검토 필요' if has_major else '현행 유지 가능.')
    return {'mode':'parser_extracted','semantic_pattern':semantic,'source_slots':source_slots,'ko_slots':ko_slots,'expected_seed_slots':seeded.get('source_slots') if seeded else None,'slot_extraction_quality':quality,'patterns':patterns,'issues':issues,'source_quality_issues':source_quality_issues,'suggested_ko':suggested,'score':score,'verdict':verdict,'actual_template':'parser_extracted','expected_template':'seed_or_parser_rules','rule_source':'generic_parser+seed_oracle' if seeded else 'generic_parser'}

def infer_generic_facts(card: dict[str, Any]) -> dict[str, Any]:
    source = str(card.get('source_text','')).strip()
    ko = str(card.get('current_ko','')).strip()
    code = str(card.get('card_id') or card.get('code') or '')
    seeded = seeded_range_facts(code)
    if seeded:
        return infer_parser_facts(card, seeded)
    # Prefer the section-aware generic encounter parser for real card text.
    if any(token in source for token in ['choice1', 'choiceType', 'battleObjective', 'Persistent:', 'Unstable:', '3+ players', '[[ICON_']):
        parsed = infer_parser_facts(card, None)
        if parsed['semantic_pattern'] != 'UNRESOLVED':
            return parsed
    prior = card.get('prior_translations') or []
    approved_logs = card.get('approved_qa_logs') or []
    lower = source.lower()
    issues: list[dict[str, Any]] = []
    suggested = '현행 유지 가능.'
    score = 100
    verdict = 'Pass'
    patterns: list[str] = []

    if re.search(r'attack an enemy .*times?\.?$', lower):
        m = re.search(r'attack an enemy (.+?times?)\.?$', lower)
        count = m.group(1) if m else 'multiple times'
        semantic = 'REPEAT_ACTION_COUNT'
        slots = {'action':'Attack','target':'an enemy','count':count}
        ko_slots = {'action':'공격','target':'적 하나','count':('세 번' if 'three' in count else count)}
        actual = '{횟수}번 {대상}을 {행동}합니다.' if re.match(r'\s*\d+번\s+', ko) else '{대상}을 {횟수}번 {행동}합니다.'
        expected = None
        for log in approved_logs:
            if log.get('semantic_pattern') == semantic and log.get('ko_template'):
                expected = log['ko_template']; break
        expected = expected or '{대상}을 {횟수}번 {행동}합니다.'
        if actual != expected:
            suggested = '적 하나를 세 번 공격합니다.' if 'three' in count else ko
            issues.append({'issue_id':f"{card.get('card_id','CARD')}-SYNTAX-001",'issue_type':'Syntax Pattern Consistency','severity':'Major','evidence':f"기존 승인 문형은 '{expected}'이나 현재 번역은 '{actual}' 구조.",'suggested_fix':suggested,'confidence':0.92,'blocks_approval':True})
            score = 92; verdict = 'Pass with fixes'
        patterns = ['Attack an enemy', 'times']
        return {'semantic_pattern':semantic,'source_slots':slots,'ko_slots':ko_slots,'patterns':patterns,'issues':issues,'suggested_ko':suggested,'score':score,'verdict':verdict,'actual_template':actual,'expected_template':expected,'rule_source':'approved_qa_logs' if approved_logs else 'dominant_observed'}

    if re.search(r'deal\s+\d+\s+damage\.?$', lower):
        m = re.search(r'deal\s+(\d+)\s+damage', lower); amount = m.group(1) if m else ''
        semantic = 'DEAL_DAMAGE_AMOUNT'
        slots = {'action':'Deal damage','amount':amount}
        ko_slots = {'action':'피해를 줌','amount':amount}
        actual = '피해 {수량}을 줍니다.' if re.search(r'피해\s*\d+을\s*줍니다', ko) else '{수량} 피해를 줍니다.'
        expected = '{수량} 피해를 줍니다.'
        if actual != expected:
            suggested = f'{amount} 피해를 줍니다.'
            issues.append({'issue_id':f"{card.get('card_id','CARD')}-SYNTAX-001",'issue_type':'Syntax Pattern Consistency','severity':'Major','evidence':f"같은 피해량 부여 패턴에서 수량 위치와 조사 구조가 기존 승인 문형과 다름. expected={expected}, actual={actual}",'suggested_fix':suggested,'confidence':0.92,'blocks_approval':True})
            score = 92; verdict = 'Pass with fixes'
        patterns = ['Deal', 'damage']
        return {'semantic_pattern':semantic,'source_slots':slots,'ko_slots':ko_slots,'patterns':patterns,'issues':issues,'suggested_ko':suggested,'score':score,'verdict':verdict,'actual_template':actual,'expected_template':expected,'rule_source':'dominant_prior_translations'}

    simple_actions = _parse_rule_actions(source)
    if simple_actions:
        issues = []
        if re.search(r'\bmust\b', lower) and re.search(r'수 있|가능', ko):
            issues.append({'issue_id': f"{card.get('card_id','CARD')}-REG_MODAL_MUST_TO_MAY", 'issue_type': 'Modal force mismatch', 'severity': 'Major', 'span_source': 'must', 'span_ko': '할 수 있습니다/가능', 'evidence': 'Source obligation uses must, but KO weakens it into optional may/can wording.', 'suggested_fix': 'must는 “해야 합니다/반드시 …합니다” 계열로 보존합니다.', 'confidence': 0.9, 'blocks_approval': True})
        return {'mode': 'parser_extracted', 'semantic_pattern': 'SIMPLE_ACTION_EFFECT', 'source_slots': {'actions': simple_actions}, 'ko_slots': {'raw': ko}, 'patterns': ['rule_actions'], 'issues': issues, 'suggested_ko': '사람 검토 필요' if issues else '현행 유지 가능.', 'score': 82 if issues else 97, 'verdict': 'Needs revision' if issues else 'Pass', 'actual_template': 'parser_extracted', 'expected_template': 'parser_extracted', 'rule_source': 'generic_parser'}

    return {'semantic_pattern':'UNRESOLVED','source_slots':{'raw':source},'ko_slots':{'raw':ko},'patterns':source.split()[:5],'issues':[{'issue_id':f"{card.get('card_id','CARD')}-UNRESOLVED-001",'issue_type':'Unresolved Pattern','severity':'Major','evidence':'Generic analyzer could not infer semantic pattern.','suggested_fix':'사람 검토 필요','confidence':0.2,'blocks_approval':True}],'suggested_ko':'사람 검토 필요','score':50,'verdict':'Needs revision','actual_template':'unresolved','expected_template':'unresolved','rule_source':'unresolved'}

def new_context_from_card(card: dict[str, Any], run_id: str) -> dict[str, Any]:
    code = str(card.get('card_id') or card.get('code') or 'CARD_UNKNOWN')
    facts = infer_generic_facts(card)
    source = str(card.get('source_text',''))
    current_ko = str(card.get('current_ko',''))
    return {'project':str(PROJECT),'qa_root':str(QA_ROOT),'run_id':run_id,'code':code,'card_id':code,'item_id':code,'category':card.get('category',{}),'source_file':None,'translation_file':None,'source_text':source,'current_ko':current_ko,'polished_ko':card.get('polished_ko'),'input_card':card,'facts':facts,'policy':POLICY.copy(),'agent_trace':[],'agent_results':{},'memory_updates_applied':False,'auto_apply':False}

def card_category_from_path(orig: Path) -> tuple[str, dict[str, str]]:
    parent = orig.parent.name
    if parent == 'Peaceful Encounter':
        return f'Peaceful Encounter/{orig.name.split(" - ")[0]}', {'component_type':'card','card_type':'peaceful_encounter','text_type':'encounter_choice_text'}
    if parent == 'Delve':
        return f'Delve/{orig.name.split(" - ")[0]}', {'component_type':'card','card_type':'delve','text_type':'delve_tile_text'}
    return f'Conflict Encounter/{orig.name.split(" - ")[0]}', {'component_type':'card','card_type':'conflict_encounter','text_type':'encounter_choice_text'}

def new_context(code: str, run_id: str) -> dict[str, Any]:
    orig, trans=find_card_files(code)
    source_text=orig.read_text(encoding='utf-8')
    current_ko=trans.read_text(encoding='utf-8')
    item_id, category = card_category_from_path(orig)
    if code in CARD_FACTS:
        facts=CARD_FACTS[code]
    else:
        facts=infer_generic_facts({'card_id': code, 'category': category, 'source_text': source_text, 'current_ko': current_ko, 'term_glossary': read_jsonl(QA_ROOT/'memory/term_db.jsonl'), 'syntax_dictionary': read_jsonl(QA_ROOT/'memory/syntax_rules.jsonl'), 'prior_translations': [], 'approved_qa_logs': []})
    return {'project':str(PROJECT),'qa_root':str(QA_ROOT),'run_id':run_id,'code':code,'card_id':code,'item_id':item_id,'category':category,'source_file':str(orig),'translation_file':str(trans),'source_text':source_text,'current_ko':current_ko,'facts':facts,'policy':POLICY.copy(),'agent_trace':[],'agent_results':{},'memory_updates_applied':False,'auto_apply':False}

def record_agent(context: dict[str, Any], agent_name: str, result: dict[str, Any]) -> dict[str, Any]:
    result=dict(result); result.setdefault('status','done')
    context.setdefault('agent_results',{})[agent_name]=result
    context.setdefault('agent_trace',[]).append({'agent_name':agent_name,'status':result['status'],'summary':result.get('summary','')})
    return context

def _count_slots(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count_slots(v) for v in value.values()) or len(value)
    if isinstance(value, list):
        return sum(_count_slots(v) for v in value) or len(value)
    return 1 if value not in [None, ''] else 0

def heuristic_parser_probe(source_text: str, ko_text: str) -> dict[str, Any]:
    """Lightweight generic parser probe used to measure seed dependence.

    This is not the authoritative QA result. It records what the generic parser can
    see without card-specific seed facts, so the harness can honestly report where
    it is still relying on seeded slots.
    """
    patterns = {
        'unstable': bool(re.search(r'ICON_Unstable_|\bUnstable:', source_text)),
        'persistent': 'Persistent:' in source_text,
        'three_plus_players': '3+ players' in source_text,
        'each_adventurer': 'Each adventurer' in source_text,
        'battle_objective': 'battleObjective' in source_text,
        'choice_type': 'choiceType' in source_text,
        'timing_after_each': bool(re.search(r'After each time|Each time|At the start|At the end', source_text)),
        'scope_each_hex': 'each hex' in source_text.lower(),
    }
    extracted = [k for k, v in patterns.items() if v]
    ko_markers = {
        'unstable_icon_ko': 'ICON_Unstable' in ko_text or '불안정' in ko_text,
        'persistent_ko': '지속:' in ko_text,
        'three_plus_ko': '3+ 인' in ko_text or '3 이상' in ko_text,
        'each_adventurer_ko': '각 모험가' in ko_text,
        'battle_objective_ko': '전투 목표' in ko_text,
    }
    return {'extracted_markers': extracted, 'marker_count': len(extracted), 'ko_markers': ko_markers}

def build_step_quality(context: dict[str, Any]) -> dict[str, Any]:
    facts = context.get('facts', {})
    probe = heuristic_parser_probe(context.get('source_text', ''), context.get('current_ko', ''))
    seeded_slots = _count_slots(facts.get('source_slots', {}))
    marker_count = probe['marker_count']
    return {
        'seed_vs_parser': {
            'seeded_slot_count': seeded_slots,
            'generic_marker_count': marker_count,
            'seed_dependency': 'high' if seeded_slots > marker_count + 3 else 'medium' if seeded_slots > marker_count else 'low',
            'generic_parser_probe': probe,
        }
    }

def pipeline_steps(context: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    steps = {f'{i:02d}_{name}': {'status': 'done', 'evidence': 'agent pipeline artifact에 결과 기록'} for i, name in enumerate(PIPELINE_NAMES, 1)}
    steps['22_사람 승인 후 memory 업데이트'] = {'status': 'skipped_pending_human_approval', 'evidence': '본 pass는 suggestion_only; memory 확정 업데이트 금지'}
    if not context:
        return steps
    issues = context.get('issues') or context.get('facts', {}).get('issues', [])
    has_major = any(i.get('severity') in ['Critical', 'Major'] for i in issues)
    has_note = any(i.get('severity') in ['Note', 'Minor', 'Info'] for i in issues)
    if context.get('ontology_result', {}).get('status') == 'not_available':
        steps['07_lore ontology 검사'] = {'status': 'not_available', 'evidence': 'ontology DB/hits 없음; 검증 대신 가용성 기록'}
    if context.get('patch_result', {}).get('status') == 'not_available':
        steps['08_patch note 검사'] = {'status': 'not_available', 'evidence': 'patch/improvement notes 없음; 검증 대신 가용성 기록'}
    elif context.get('patch_result', {}).get('status') == 'not_applicable':
        steps['08_patch note 검사'] = {'status': 'skipped_not_applicable', 'evidence': 'patch/improvement notes는 있으나 이 source에는 적용되지 않음'}
    elif context.get('patch_result', {}).get('status') == 'warn':
        steps['08_patch note 검사'] = {'status': 'warn', 'evidence': '적용 가능한 patch/improvement 중 current_ko 반영 여부 확인 필요'}
    elif context.get('patch_result', {}).get('status') == 'pass':
        steps['08_patch note 검사'] = {'status': 'done', 'evidence': '적용 가능한 patch/improvement가 current_ko에 반영됨'}
    if context.get('source_analysis', {}).get('semantic_pattern') == 'UNRESOLVED':
        steps['03_원문 의미 패턴 추출'] = {'status': 'block', 'evidence': 'semantic pattern unresolved'}
        steps['04_원문 슬롯 추출'] = {'status': 'block', 'evidence': 'source slots unresolved'}
    if has_major:
        steps['15_룰 리스크 평가'] = {'status': 'block', 'evidence': 'Major/Critical issue blocks approval'}
        steps['18_최종 QA 판정'] = {'status': 'block', 'evidence': context.get('verdict', 'Needs revision')}
    elif has_note:
        steps['15_룰 리스크 평가'] = {'status': 'warn', 'evidence': 'Note/Minor issue only'}
        steps['18_최종 QA 판정'] = {'status': 'warn', 'evidence': context.get('verdict', 'Suggestion')}
    if context.get('learning_update_proposal'):
        steps['21_학습 반영 제안 생성'] = {'status': 'warn' if not has_major else 'block', 'evidence': 'learning/proposal candidates generated; human approval required'}
    sv = context.get('self_verification') or {}
    if sv:
        failed_passes = [k for k, v in sv.items() if k.endswith('_pass') and v is False]
        if not sv.get('blocking_issue_pass', True):
            steps['17_수정안 self-verification'] = {'status': 'block', 'evidence': f"blocking issues remain: {', '.join(sv.get('blocking_issue_ids') or [])}"}
        elif failed_passes or not sv.get('meaning_preserved', True):
            steps['17_수정안 self-verification'] = {'status': 'warn', 'evidence': f"self-verification warning: {', '.join(failed_passes) or 'meaning_preserved'}"}
        else:
            steps['17_수정안 self-verification'] = {'status': 'done', 'evidence': 'self-verification gates passed'}
    return steps

def now_iso() -> str: return datetime.datetime.now().isoformat()
