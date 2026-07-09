#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

QA_ROOT = Path(__file__).resolve().parents[1]
if str(QA_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_ROOT))

from agents.shared import AGENT_SEQUENCE, PIPELINE_NAMES, QA_ROOT as SHARED_QA_ROOT, new_context, new_context_from_card, now_iso, pipeline_steps, build_step_quality

MODULES = ['context_pack_builder','source_meaning_checker','terminology_manager','terminology_pattern_worker','syntax_pattern_controller','syntax_style_worker','inductive_style_learner','cross_card_consistency_checker','cross_card_pattern_worker','lore_ontology_checker','lore_ontology_worker','patch_note_checker','rules_lawyer','korean_editor','verifier','qa_reviewer','harness_meta_auditor']

STYLE_PATTERNS = {
    'Persistent:': ['지속:'],
    'Unstable:': ['불안정:'],
    'If successful': ['성공하면', '성공 시'],
    'If unsuccessful': ['실패하면', '실패 시'],
    'Each adventurer': ['각 모험가'],
}

TERM_PATTERNS = {
    'light fatigue': ['가벼운 피로'],
    'heavy fatigue': ['강한 피로'],
    'Combat skill dice': ['전투 스킬 주사위', '전투 기술 주사위'],
    'social check': ['사회 판정'],
    'Persistent': ['지속'],
    'Unstable': ['불안정'],
}

def execute_agents(context: dict) -> dict:
    for module_name in MODULES:
        module = importlib.import_module(f'agents.{module_name}')
        context = module.run(context)
    return context

def _inject_batch_indexes(context: dict, batch_indexes: dict | None) -> dict:
    if not batch_indexes:
        return context
    context['batch_indexes'] = {
        'dominant_pattern_index': batch_indexes['dominant_pattern_index'],
        'cross_card_consistency_index': batch_indexes['cross_card_consistency_index'],
        'source_syntax_pattern_index': batch_indexes.get('source_syntax_pattern_index', {}),
    }
    context['batch_index_refs'] = {
        'dominant_pattern_index': batch_indexes['refs']['dominant_pattern_index'],
        'cross_card_consistency_index': batch_indexes['refs']['cross_card_consistency_index'],
        'source_syntax_pattern_index': batch_indexes['refs'].get('source_syntax_pattern_index'),
    }
    return context

def run_context_card(context: dict, out: Path, legacy_root: bool, batch_indexes: dict | None) -> dict:
    return persist_context(execute_agents(_inject_batch_indexes(context, batch_indexes)), out, legacy_root=legacy_root)

def persist_context(context: dict, out: Path, legacy_root: bool) -> dict:
    code = context['code']
    roots = [out / 'output']
    if legacy_root:
        roots.append(out)
    for root in roots:
        for sub in ['context_packs','qa_json','qa_md']:
            (root/sub).mkdir(parents=True, exist_ok=True)
        (root/'context_packs'/f'{code}.context.json').write_text(json.dumps(context['context_pack'], ensure_ascii=False, indent=2), encoding='utf-8')
        qa = build_qa_json(context)
        (root/'qa_json'/f'{code}.qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding='utf-8')
        (root/'qa_md'/f'{code}.qa.md').write_text(build_qa_md(qa), encoding='utf-8')
    return {'code':code,'score':context['score'],'verdict':context['verdict'],'issues':len(context['issues']),'major':sum(1 for x in context['issues'] if x.get('severity')=='Major'),'requires_human_review':context['requires_human_review']}

def _dominant(counter: Counter) -> tuple[str, int, float]:
    if not counter:
        return '', 0, 0.0
    value, count = counter.most_common(1)[0]
    total = sum(counter.values()) or 1
    return value, count, round(count / total, 3)

def _build_manifest(run_id: str, contexts: list[dict], scope: str) -> dict:
    seen = Counter(c['code'] for c in contexts)
    return {
        'run_id': run_id,
        'created_at': now_iso(),
        'scope': scope,
        'cards': [
            {
                'card_id': c['code'],
                'item_id': c['item_id'],
                'category': c['category'],
                'source_file': c.get('source_file'),
                'translation_file': c.get('translation_file'),
            }
            for c in contexts
        ],
        'duplicates': [code for code, count in seen.items() if count > 1],
        'missing_pairs': [],
    }

def _build_dominant_pattern_index(run_id: str, contexts: list[dict], scope: str) -> dict:
    patterns = {}
    for pattern, ko_markers in STYLE_PATTERNS.items():
        variants = Counter()
        examples = []
        source_count = 0
        for c in contexts:
            if pattern not in c.get('source_text',''):
                continue
            source_count += 1
            examples.append(c['code'])
            ko = c.get('current_ko','')
            matched = next((marker for marker in ko_markers if marker in ko), '')
            if matched:
                variants[matched] += 1
            else:
                variants['<missing>'] += 1
        if source_count:
            dominant, count, confidence = _dominant(variants)
            patterns[pattern] = {
                'dominant_ko': dominant,
                'count': source_count,
                'variants': dict(variants),
                'confidence': confidence,
                'examples': examples[:10],
            }
    return {'run_id': run_id, 'created_at': now_iso(), 'scope': scope, 'patterns': patterns}

def _source_syntax_pattern(source: str) -> str:
    lower = ' '.join((source or '').lower().split())
    if re.search(r'attack an enemy (?:\d+|once|twice|three|four|five) times?', lower):
        return 'Attack an enemy N times'
    if re.search(r'recover \d+ health \d+ times', lower):
        return 'Recover N health M times'
    if re.search(r'\b(?:gain|gains|lose|loses)\s+\d+\s+[a-z][a-z -]*\b', lower):
        return 'Gain/Lose N TERM'
    if re.search(r'use\s+(?:the|this|that|an?|your)?\s*[a-z][a-z\s-]*?\s+\d+\s+times?', lower):
        return 'VERB OBJECT N times'
    return ''


def _syntax_pattern_quality(total: int, confidence: float, unclassified: int = 0) -> dict:
    warnings = []
    if total < 5:
        warnings.append('batch_local_observed_only_not_promoted')
    if confidence < 0.85:
        warnings.append('dominant_template_confidence_below_promotion_threshold')
    if unclassified:
        warnings.append('unclassified_ko_templates_present')
    return {
        'evidence_count': total,
        'confidence': confidence,
        'unclassified_count': unclassified,
        'promotion_eligible': total >= 5 and confidence >= 0.85 and unclassified == 0,
        'warnings': warnings,
    }


def _ko_syntax_template(ko: str, source_pattern: str) -> str:
    text = ' '.join((ko or '').split())
    if source_pattern == 'Attack an enemy N times':
        if re.search(r'적 하나를 \d+번 공격', text):
            return 'OBJ_COUNT_VERB'
        if re.search(r'\d+번 적 하나를 공격', text):
            return 'COUNT_OBJ_VERB'
    if source_pattern == 'Recover N health M times':
        if re.search(r'체력 \d+을 \d+번 회복', text):
            return 'OBJ_COUNT_VERB'
        if re.search(r'\d+번 체력 \d+을 회복', text):
            return 'COUNT_OBJ_VERB'
    if source_pattern == 'Gain/Lose N TERM':
        if re.search(r'\d+\s*(?:\[\[[^]|]+\|)?(?![을를이가은는]\s)[가-힣A-Za-z_]{2,}(?:\]\])?(?:을|를)?\s*(?:얻|잃)', text):
            return 'NUM_TERM_VERB'
        if re.search(r'(?:\[\[[^]|]+\|)?(?![을를이가은는]\s)[가-힣A-Za-z_]{2,}(?:\]\])?\s*\d+(?:을|를)?\s*(?:얻|잃)', text):
            return 'TERM_NUM_VERB'
    if source_pattern == 'VERB OBJECT N times':
        if re.search(r'[가-힣A-Za-z0-9_\s]+(?:을|를)\s*\d+번\s*(?:반복해\s*)?사용', text):
            return 'OBJ_COUNT_VERB'
        if re.search(r'(?:반복해서\s*)?\d+번\s*[가-힣A-Za-z0-9_\s]+(?:을|를)\s*사용', text):
            return 'COUNT_OBJ_VERB'
    return '<unclassified>'


def _build_source_syntax_pattern_index(run_id: str, contexts: list[dict], scope: str) -> dict:
    grouped: dict[str, dict] = {}
    for c in contexts:
        pattern = _source_syntax_pattern(c.get('source_text', ''))
        if not pattern:
            continue
        template = _ko_syntax_template(c.get('current_ko', ''), pattern)
        entry = grouped.setdefault(pattern, {
            'source_pattern': pattern,
            'source_examples': [],
            'ko_template_variants': defaultdict(lambda: {'count': 0, 'examples': []}),
        })
        entry['source_examples'].append({
            'card_id': c['code'],
            'source_excerpt': _syntax_excerpt(c.get('source_text', ''), pattern),
            'ko_template': template,
        })
        variant = entry['ko_template_variants'][template]
        variant['count'] += 1
        variant['examples'].append(c['code'])
    patterns = {}
    unclassified_total = 0
    for pattern, entry in grouped.items():
        variants = entry['ko_template_variants']
        counts = Counter({k: v['count'] for k, v in variants.items()})
        dominant, _, confidence = _dominant(counts)
        total = sum(counts.values())
        unclassified = counts.get('<unclassified>', 0)
        unclassified_total += unclassified
        quality = _syntax_pattern_quality(total, confidence, unclassified)
        patterns[pattern] = {
            'source_pattern': pattern,
            'total_count': total,
            'dominant_template': dominant,
            'confidence': confidence,
            'ko_template_variants': {k: {'count': v['count'], 'examples': v['examples'][:10]} for k, v in variants.items()},
            'source_examples': entry['source_examples'][:10],
            'quality': quality,
            'promotion_eligible': quality['promotion_eligible'],
            'requires_human_approval': quality['promotion_eligible'],
        }
    return {
        'run_id': run_id,
        'created_at': now_iso(),
        'scope': scope,
        'index_type': 'source_syntax_pattern_index',
        'patterns': patterns,
        'quality': {
            'created_from_batch_contexts': True,
            'card_count': len(contexts),
            'pattern_count': len(patterns),
            'observed_pair_count': sum(p['total_count'] for p in patterns.values()),
            'unclassified_ko_template_count': unclassified_total,
            'promotion_candidates_count': sum(1 for p in patterns.values() if p.get('promotion_eligible')),
            'memory_updates_applied': False,
            'warnings': ['batch_local_index_only_persistent_rules_require_human_approval'],
        }
    }


def _syntax_excerpt(text: str, pattern: str) -> str:
    for line in (text or '').splitlines():
        probe = ' '.join(line.split())
        if not probe:
            continue
        if pattern == 'VERB OBJECT N times' and re.search(r'use\s+.+?\s+\d+\s+times?', probe, re.I):
            return probe
        if pattern == 'Attack an enemy N times' and re.search(r'attack an enemy .+? times?', probe, re.I):
            return probe
        if pattern == 'Recover N health M times' and re.search(r'recover .+? health .+? times?', probe, re.I):
            return probe
    return ''


def _write_source_syntax_index_md(path: Path, index: dict) -> None:
    lines = [f"# Source Syntax Pattern Index", '', f"Run ID: `{index['run_id']}`", f"Scope: {index['scope']}", '']
    lines += ['## Quality']
    for k, v in sorted((index.get('quality') or {}).items()):
        lines.append(f'- {k}: `{json.dumps(v, ensure_ascii=False)}`')
    lines.append('')
    for pattern, entry in sorted((index.get('patterns') or {}).items()):
        lines += [
            f'## {pattern}',
            f"- total_count: {entry.get('total_count')}",
            f"- dominant_template: {entry.get('dominant_template')}",
            f"- confidence: {entry.get('confidence')}",
            f"- promotion_eligible: {entry.get('promotion_eligible')}",
            '- variants:',
        ]
        for tmpl, variant in sorted((entry.get('ko_template_variants') or {}).items()):
            lines.append(f"  - {tmpl}: {variant.get('count')} — {', '.join(variant.get('examples', []))}")
        lines.append('- examples:')
        for ex in entry.get('source_examples', []):
            lines.append(f"  - {ex.get('card_id')}: {ex.get('source_excerpt')} -> {ex.get('ko_template')}")
        lines.append('')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _write_corpus_preflight_report(path: Path, syntax_index: dict, dominant: dict, consistency: dict) -> None:
    lines = [
        '# Corpus Preflight Report',
        '',
        f"Run ID: `{syntax_index['run_id']}`",
        f"Scope: {syntax_index['scope']}",
        '',
        '## Summary',
        f"- source syntax patterns: {syntax_index['quality']['pattern_count']}",
        f"- observed syntax pairs: {syntax_index['quality']['observed_pair_count']}",
        f"- promotion candidates: {syntax_index['quality']['promotion_candidates_count']}",
        f"- memory updates applied: {syntax_index['quality']['memory_updates_applied']}",
        '',
        '## Source syntax patterns',
    ]
    for pattern, entry in sorted((syntax_index.get('patterns') or {}).items()):
        lines += [
            f'### {pattern}',
            f"- total_count: {entry.get('total_count')}",
            f"- dominant_template: {entry.get('dominant_template')}",
            f"- confidence: {entry.get('confidence')}",
            f"- promotion_eligible: {entry.get('promotion_eligible')}",
            '- variants:',
        ]
        for tmpl, variant in sorted((entry.get('ko_template_variants') or {}).items()):
            lines.append(f"  - {tmpl}: {variant.get('count')} ({', '.join(variant.get('examples', []))})")
        lines.append('')
    lines += [
        '## Existing preflight indexes',
        f"- dominant_pattern_index patterns: {len((dominant.get('patterns') or {}))}",
        f"- cross_card choice icons: {len((consistency.get('choice_icons') or {}))}",
        f"- cross_card terms: {len((consistency.get('terms') or {}))}",
        f"- cross_card syntax structures: {len((consistency.get('syntax_structures') or {}))}",
        '',
        'Persistent rule updates are proposal-only; no memory/rule database is modified by preflight.',
    ]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _norm_source_term(term: str) -> str:
    term = re.sub(r'\bavailable\b', ' ', (term or '').lower())
    term = re.sub(r'\b(skill) die\b', r'\1 dice', term)
    term = re.sub(r'\bdie\b', 'dice', term)
    term = re.sub(r'[^a-z0-9 ]+', ' ', term)
    return re.sub(r'\s+', ' ', term).strip()


def _quantity_source_observations(source: str) -> list[dict]:
    text = ' '.join((source or '').split())
    observations = []
    for m in re.finditer(r'\b(?:gain|gains|lose|loses)\s+(?P<num>\d+)\s+(?P<term>[A-Za-z][A-Za-z -]*?)(?=\.|,|;| and | or |$)', text, re.I):
        term = _norm_source_term(m.group('term'))
        if term:
            observations.append({'source_family': 'GAIN_LOSE_NUM_TERM', 'term_source': term, 'index_key': f'GAIN_LOSE_NUM_TERM::{term}'})
    for m in re.finditer(r'\bexhaust\s+(?:(?P<num>\d+)\s+)?(?P<term>(?:available\s+)?[A-Za-z][A-Za-z -]*?(?:die|dice))\b', text, re.I):
        term = _norm_source_term(m.group('term'))
        if term:
            observations.append({'source_family': 'EXHAUST_NUM_TERM', 'term_source': term, 'index_key': f'EXHAUST_NUM_TERM::{term}'})
    # De-duplicate while preserving order.
    seen = set()
    out = []
    for obs in observations:
        if obs['index_key'] in seen:
            continue
        seen.add(obs['index_key'])
        out.append(obs)
    return out


def _clean_ko_term(term: str) -> str:
    term = re.sub(r'\[\[[^]|]+\|([^]]+)\]\]', r'\1', term or '')
    term = re.sub(r'\[\[([^]]+)\]\]', r'\1', term)
    term = re.sub(r'\b(?:사용 가능한|각|모든|아무|해당)\b', ' ', term)
    term = re.sub(r'\s+', ' ', term).strip(' .,:;')
    term = re.sub(r'(?:을|를|이|가|은|는)$', '', term)
    if '스킬 주사위' in term:
        m = re.search(r'((?:전투|궁술|중갑|그림자|곡예술|데이드라 소환술)\s+스킬 주사위)$', term)
        term = m.group(1).strip() if m else '스킬 주사위'
    elif '주사위' in term:
        m = re.search(r'((?:[가-힣A-Za-z_]+\s+){0,1}[가-힣A-Za-z_]*주사위)$', term)
        if m:
            term = m.group(1).strip()
    return term


def _quantity_ko_candidates(ko: str) -> list[dict]:
    text = ' '.join((ko or '').split())
    candidates = []
    # 5 끈기를 얻습니다 / 2 명성을 잃습니다
    for m in re.finditer(r'(?P<num>\d+)\s*(?P<term>(?:\[\[[^]|]+\|)?(?![을를이가은는]\s)[가-힣A-Za-z_]{2,}(?:\]\])?)(?:을|를)?\s*(?P<verb>얻|잃)', text):
        candidates.append({'template': 'NUM_TERM_VERB', 'term_ko': _clean_ko_term(m.group('term')), 'span_ko': m.group(0)})
    # 끈기 5를 얻습니다 / 명성 2를 잃습니다
    for m in re.finditer(r'(?P<term>(?:\[\[[^]|]+\|)?(?![을를이가은는]\s)[가-힣A-Za-z_]{2,}(?:\]\])?)\s*(?P<num>\d+)(?:을|를)?\s*(?P<verb>얻|잃)', text):
        candidates.append({'template': 'TERM_NUM_VERB', 'term_ko': _clean_ko_term(m.group('term')), 'span_ko': m.group(0)})
    # 스킬 주사위 1개를 소진합니다 / 사용 가능한 스킬 주사위 2개를 소진해야 합니다
    for m in re.finditer(r'(?P<term>(?:사용 가능한\s+)?(?:[가-힣A-Za-z_]+\s+){0,2}주사위)\s*(?P<num>\d+)개(?:를)?\s*소진', text):
        candidates.append({'template': 'TERM_NUM_COUNTER_VERB', 'term_ko': _clean_ko_term(m.group('term')), 'span_ko': m.group(0)})
    # 1개 스킬 주사위를 소진합니다
    for m in re.finditer(r'(?P<num>\d+)개\s*(?P<term>(?:사용 가능한\s+)?(?:[가-힣A-Za-z_]+\s+){0,2}주사위)(?:를)?\s*소진', text):
        candidates.append({'template': 'NUM_COUNTER_TERM_VERB', 'term_ko': _clean_ko_term(m.group('term')), 'span_ko': m.group(0)})
    return [c for c in candidates if c.get('term_ko')]


def _select_quantity_ko_candidate(source_obs: dict, ko: str) -> dict | None:
    candidates = _quantity_ko_candidates(ko)
    if not candidates:
        return None
    family = source_obs.get('source_family')
    term_source = source_obs.get('term_source', '')
    if family == 'EXHAUST_NUM_TERM' or 'dice' in term_source:
        dice_candidates = [c for c in candidates if '주사위' in c.get('term_ko', '')]
        return dice_candidates[0] if dice_candidates else None
    if family == 'GAIN_LOSE_NUM_TERM':
        non_dice = [c for c in candidates if '주사위' not in c.get('term_ko', '')]
        return non_dice[0] if non_dice else None
    return candidates[0]


def _build_quantity_term_patterns(contexts: list[dict]) -> dict:
    grouped: dict[str, dict] = {}
    for c in contexts:
        for source_obs in _quantity_source_observations(c.get('source_text', '')):
            ko_obs = _select_quantity_ko_candidate(source_obs, c.get('current_ko', ''))
            if not ko_obs:
                template = '<unclassified>'
                term_ko = '<missing>'
                span_ko = ''
            else:
                template = ko_obs['template']
                term_ko = ko_obs['term_ko']
                span_ko = ko_obs.get('span_ko', '')
            entry = grouped.setdefault(source_obs['index_key'], {
                'source_family': source_obs['source_family'],
                'term_source': source_obs['term_source'],
                'variants': defaultdict(list),
                'term_ko_variants': defaultdict(list),
                'examples': [],
            })
            entry['variants'][template].append(c['code'])
            entry['term_ko_variants'][term_ko].append(c['code'])
            entry['examples'].append({'card_id': c['code'], 'template': template, 'term_ko': term_ko, 'span_ko': span_ko})
    out = {}
    for key, entry in grouped.items():
        template_counts = Counter({k: len(v) for k, v in entry['variants'].items()})
        dominant_template, dominant_count, confidence = _dominant(template_counts)
        term_counts = Counter({k: len(v) for k, v in entry['term_ko_variants'].items() if k != '<missing>'})
        term_ko_dominant, _, term_confidence = _dominant(term_counts)
        total_count = sum(template_counts.values())
        out[key] = {
            'source_family': entry['source_family'],
            'term_source': entry['term_source'],
            'term_ko_dominant': term_ko_dominant,
            'term_ko_confidence': term_confidence,
            'dominant_template': dominant_template,
            'dominant_count': dominant_count,
            'total_count': total_count,
            'confidence': confidence,
            'variants': {k: v for k, v in entry['variants'].items()},
            'term_ko_variants': {k: v for k, v in entry['term_ko_variants'].items()},
            'examples': entry['examples'][:10],
        }
    return out


def _build_cross_card_consistency_index(run_id: str, contexts: list[dict], scope: str) -> dict:
    choice_icons = {}
    icon_source_cards = defaultdict(list)
    icon_ko_variants = defaultdict(Counter)
    for c in contexts:
        source_choices = c.get('facts', {}).get('source_slots', {}).get('choices') or []
        ko_choices = c.get('facts', {}).get('ko_slots', {}).get('choices') or []
        for idx, choice in enumerate(source_choices):
            icon = choice.get('choice_type_icon')
            if not icon:
                continue
            icon_source_cards[icon].append(c['code'])
            ko_icon = ko_choices[idx].get('choice_type_icon') if idx < len(ko_choices) else '<missing>'
            icon_ko_variants[icon][ko_icon or '<missing>'] += 1
    duplicate_icon_card_entries = False
    for icon, cards in icon_source_cards.items():
        unique_cards = sorted(set(cards))
        duplicate_icon_card_entries = duplicate_icon_card_entries or len(unique_cards) < len(cards)
        choice_icons[icon] = {
            'source_cards': cards,
            'source_cards_unique': unique_cards,
            'source_occurrences': len(cards),
            'source_card_count': len(unique_cards),
            'ko_icons': dict(icon_ko_variants[icon]),
        }

    terms = {}
    for term, ko_markers in TERM_PATTERNS.items():
        variants = defaultdict(list)
        for c in contexts:
            if term not in c.get('source_text',''):
                continue
            ko = c.get('current_ko','')
            marker = next((m for m in ko_markers if m in ko), '<missing>')
            variants[marker].append(c['code'])
        if variants:
            counts = Counter({k: len(v) for k, v in variants.items()})
            dominant, dominant_count, confidence = _dominant(counts)
            total_count = sum(counts.values())
            terms[term] = {
                'dominant': dominant,
                'dominant_count': dominant_count,
                'total_count': total_count,
                'confidence': confidence,
                'variants': dict(variants),
            }

    syntax_structures = {}
    for c in contexts:
        pattern = _source_syntax_pattern(c.get('source_text', ''))
        if not pattern:
            continue
        template = _ko_syntax_template(c.get('current_ko', ''), pattern)
        entry = syntax_structures.setdefault(pattern, {'variants': defaultdict(list)})
        entry['variants'][template].append(c['code'])
    for pattern, entry in list(syntax_structures.items()):
        variants = entry['variants']
        counts = Counter({k: len(v) for k, v in variants.items()})
        dominant, dominant_count, confidence = _dominant(counts)
        syntax_structures[pattern] = {
            'dominant_template': dominant,
            'dominant_count': dominant_count,
            'total_count': sum(counts.values()),
            'confidence': confidence,
            'variants': {k: v for k, v in variants.items()},
        }
    quantity_term_patterns = _build_quantity_term_patterns(contexts)
    return {
        'run_id': run_id,
        'created_at': now_iso(),
        'scope': scope,
        'choice_icons': choice_icons,
        'terms': terms,
        'syntax_structures': syntax_structures,
        'quantity_term_patterns': quantity_term_patterns,
        'quality': {
            'created_from_batch_contexts': True,
            'card_count': len(contexts),
            'choice_icon_count': len(choice_icons),
            'choice_icon_occurrences': sum(entry.get('source_occurrences', 0) for entry in choice_icons.values()),
            'choice_icon_unique_cards': len({card for entry in choice_icons.values() for card in entry.get('source_cards_unique', [])}),
            'term_count': len(terms),
            'syntax_structure_count': len(syntax_structures),
            'quantity_term_pattern_count': len(quantity_term_patterns),
            'duplicate_icon_card_entries': duplicate_icon_card_entries,
            'memory_updates_applied': False,
        }
    }

def _write_index_md(path: Path, title: str, index: dict) -> None:
    lines = [f'# {title}', '', f"Run ID: `{index['run_id']}`", f"Scope: {index['scope']}", '']
    if 'patterns' in index:
        for pattern, entry in sorted(index['patterns'].items()):
            lines += [f'## {pattern}', f"- 지배 번역: {entry.get('dominant_ko')}", f"- 빈도: {entry.get('count')}", f"- 신뢰도: {entry.get('confidence')}", '- 변형:']
            for variant, count in sorted((entry.get('variants') or {}).items()):
                lines.append(f'  - {variant}: {count}')
            lines.append(f"- 예시: {', '.join(entry.get('examples', []))}")
            lines.append('')
    if 'choice_icons' in index:
        lines += ['## Choice icons']
        for icon, entry in sorted(index['choice_icons'].items()):
            lines += [f'### {icon}', f"- source cards: {', '.join(entry.get('source_cards', []))}", f"- KO icons: `{json.dumps(entry.get('ko_icons', {}), ensure_ascii=False)}`", '']
        lines += ['## Terms']
        for term, entry in sorted(index.get('terms', {}).items()):
            lines += [f'### {term}', f"- dominant: {entry.get('dominant')}", f"- confidence: {entry.get('confidence')}", f"- variants: `{json.dumps(entry.get('variants', {}), ensure_ascii=False)}`", '']
        if index.get('syntax_structures'):
            lines += ['## Syntax structures']
            for pattern, entry in sorted(index.get('syntax_structures', {}).items()):
                lines += [f'### {pattern}', f"- dominant_template: {entry.get('dominant_template')}", f"- confidence: {entry.get('confidence')}", f"- variants: `{json.dumps(entry.get('variants', {}), ensure_ascii=False)}`", '']
        if index.get('quantity_term_patterns'):
            lines += ['## Quantity term patterns']
            for key, entry in sorted(index.get('quantity_term_patterns', {}).items()):
                lines += [
                    f'### {key}',
                    f"- source_family: {entry.get('source_family')}",
                    f"- term_source: {entry.get('term_source')}",
                    f"- term_ko_dominant: {entry.get('term_ko_dominant')}",
                    f"- dominant_template: {entry.get('dominant_template')}",
                    f"- confidence: {entry.get('confidence')}",
                    f"- variants: `{json.dumps(entry.get('variants', {}), ensure_ascii=False)}`",
                    '',
                ]
    path.write_text('\n'.join(lines), encoding='utf-8')

def build_batch_indexes(run_id: str, contexts: list[dict], out: Path, scope: str) -> dict:
    index_dir = out / 'indexes'
    index_dir.mkdir(parents=True, exist_ok=True)
    manifest = _build_manifest(run_id, contexts, scope)
    dominant = _build_dominant_pattern_index(run_id, contexts, scope)
    consistency = _build_cross_card_consistency_index(run_id, contexts, scope)
    source_syntax = _build_source_syntax_pattern_index(run_id, contexts, scope)
    paths = {
        'batch_manifest': index_dir / 'batch_manifest.json',
        'dominant_pattern_index': index_dir / 'dominant_pattern_index.json',
        'dominant_pattern_index_md': index_dir / 'dominant_pattern_index.md',
        'cross_card_consistency_index': index_dir / 'cross_card_consistency_index.json',
        'cross_card_consistency_index_md': index_dir / 'cross_card_consistency_index.md',
        'source_syntax_pattern_index': index_dir / 'source_syntax_pattern_index.json',
        'source_syntax_pattern_index_md': index_dir / 'source_syntax_pattern_index.md',
        'corpus_preflight_report': index_dir / 'corpus_preflight_report.md',
    }
    paths['batch_manifest'].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    paths['dominant_pattern_index'].write_text(json.dumps(dominant, ensure_ascii=False, indent=2), encoding='utf-8')
    paths['cross_card_consistency_index'].write_text(json.dumps(consistency, ensure_ascii=False, indent=2), encoding='utf-8')
    paths['source_syntax_pattern_index'].write_text(json.dumps(source_syntax, ensure_ascii=False, indent=2), encoding='utf-8')
    _write_index_md(paths['dominant_pattern_index_md'], 'Dominant Pattern Index', dominant)
    _write_index_md(paths['cross_card_consistency_index_md'], 'Cross-Card Consistency Index', consistency)
    _write_source_syntax_index_md(paths['source_syntax_pattern_index_md'], source_syntax)
    _write_corpus_preflight_report(paths['corpus_preflight_report'], source_syntax, dominant, consistency)
    return {
        'manifest': manifest,
        'dominant_pattern_index': dominant,
        'cross_card_consistency_index': consistency,
        'source_syntax_pattern_index': source_syntax,
        'refs': {
            'batch_manifest': str(paths['batch_manifest']),
            'dominant_pattern_index': str(paths['dominant_pattern_index']),
            'cross_card_consistency_index': str(paths['cross_card_consistency_index']),
            'source_syntax_pattern_index': str(paths['source_syntax_pattern_index']),
            'corpus_preflight_report': str(paths['corpus_preflight_report']),
        }
    }

def build_qa_json(context: dict) -> dict:
    return {'card_id':context['code'],'item_id':context['item_id'],'run_id':context['run_id'],'category':context['category'],'source_file':context['source_file'],'translation_file':context['translation_file'],'pipeline_steps':pipeline_steps(context),'step_quality':build_step_quality(context),'agent_trace':context['agent_trace'],'agent_results':context['agent_results'],'llm_usage':context.get('llm_usage',{}),'source_text':context['source_text'],'current_ko':context['current_ko'],'polished_ko':context.get('polished_ko'),'translation_comparison':context.get('translation_comparison',{}),'context_summary':context['context_summary'],'semantic_ir':context.get('semantic_ir',{}),'source_analysis':context['source_analysis'],'translation_slot_result':context['translation_slot_result'],'source_quality_issues':context.get('source_quality_issues',[]),'expected_seed_slots':context.get('expected_seed_slots'),'slot_extraction_quality':context.get('slot_extraction_quality'),'terminology_result':context['terminology_result'],'ontology_result':context['ontology_result'],'patch_result':context['patch_result'],'syntax_pattern_result':context['syntax_pattern_result'],'style_pattern_checks':context.get('style_pattern_checks',[]),'style_learning_quality':context.get('style_learning_quality',{}),'learned_rule_candidates':context.get('learned_rule_candidates',[]),'cross_card_consistency':context['cross_card_consistency'],'readability_exception':context.get('readability_exception'),'rules_lawyer_result':context['rules_lawyer_result'],'im_not_ai_result':context.get('im_not_ai_result',{}),'issues':context['issues'],'suggested_ko':context['suggested_ko'],'final_translation':context['final_translation'],'self_verification':context['self_verification'],'qa_reviewer_result':context.get('qa_reviewer_result',{}),'harness_meta_audit':context.get('harness_meta_audit',{}),'score':context['score'],'verdict':context['verdict'],'requires_human_review':context['requires_human_review'],'auto_apply':False,'memory_updates_applied':False,'output_type':'suggestion_only','learning_update_proposal':context['learning_update_proposal']}

def build_qa_md(qa: dict) -> str:
    lines=[]
    lines.append(f"# {qa['item_id']} QA 결과")
    lines += ['','## 최종 판정',f"- 판정: {qa['verdict']}",f"- 점수: {qa['score']}",f"- 사람 검토 필요: {'Yes' if qa['requires_human_review'] else 'No'}",'- 자동 수정: No']
    lines += ['','## 에이전트 실행 Trace']
    for entry in qa['agent_trace']:
        lines.append(f"- {entry['agent_name']}: {entry['status']} — {entry.get('summary','')}")
    lines += ['','## 22단계 실행 상태']
    for key, val in qa['pipeline_steps'].items(): lines.append(f"- {key}: {val['status']}")
    lines += ['','## Context pack 요약']
    for k,v in qa['context_summary'].items(): lines.append(f'- {k}: {v}')
    lines += ['','## 원문 의미 패턴',qa['source_analysis']['semantic_pattern'],'','## 원문 슬롯','```json',json.dumps(qa['source_analysis']['source_slots'], ensure_ascii=False, indent=2),'```','','## 번역문 슬롯','```json',json.dumps(qa['translation_slot_result']['ko_slots'], ensure_ascii=False, indent=2),'```','','## 구문/문형 판정',json.dumps(qa['syntax_pattern_result'], ensure_ascii=False, indent=2),'','## 주요 문제']
    if qa['issues']:
        for issue in qa['issues']:
            lines.append(f"- [{issue['severity']}] {issue['issue_type']} ({issue['issue_id']})")
            lines.append(f"  - 근거: {issue.get('evidence','')}")
            lines.append(f"  - 제안: {issue.get('suggested_fix','')}")
    else: lines.append('- 없음')
    lines += ['','## Source text issues']
    if qa.get('source_quality_issues'):
        for issue in qa['source_quality_issues']:
            lines.append(f"- [{issue['severity']}] {issue['issue_type']} ({issue['issue_id']})")
            lines.append(f"  - 근거: {issue.get('evidence','')}")
            lines.append(f"  - 조치: {issue.get('suggested_action','')}")
    else:
        lines.append('- 없음')
    lines += ['','## 룰 리스크',f"- {qa['rules_lawyer_result']['risk']}"]
    if qa['rules_lawyer_result'].get('scope_checks'):
        lines += ['','### scope checks','```json',json.dumps(qa['rules_lawyer_result']['scope_checks'], ensure_ascii=False, indent=2),'```']
    lines += ['','## 번역 비교: current_ko vs polished_ko']
    cmp = qa.get('translation_comparison') or {}
    if cmp:
        lines += [
            f"- status: {cmp.get('status')}",
            f"- winner: {cmp.get('winner')}",
            f"- candidate_decision: {cmp.get('candidate_decision')}",
            f"- meaning_delta: {cmp.get('meaning_delta')}",
            f"- rule_delta: {cmp.get('rule_delta')}",
            f"- style_delta: {cmp.get('style_delta')}",
            f"- safe_to_apply: {cmp.get('safe_to_apply')}",
            '- reasons:',
        ]
        for reason in cmp.get('reasons', []): lines.append(f"  - {reason}")
        if cmp.get('required_fixes_before_use'):
            lines.append('- required_fixes_before_use:')
            for fix in cmp.get('required_fixes_before_use', []): lines.append(f"  - {fix}")
    else:
        lines.append('- 없음')
    im = qa.get('im_not_ai_result') or {}
    lines += ['', '## im-not-ai / 번역투 정리 검사']
    if im:
        lines += [
            f"- status: {im.get('status')}",
            f"- candidate_source: {im.get('candidate_source')}",
            f"- candidate_decision: {im.get('candidate_decision')}",
            f"- rejected_candidate_source: {im.get('rejected_candidate_source')}",
            f"- meaning_structure_preserved: {im.get('meaning_structure_preserved')}",
            f"- change_ratio: {im.get('change_ratio')}",
            f"- requires_human_review: {im.get('requires_human_review')}",
        ]
        for check in im.get('checks', []):
            if isinstance(check, dict):
                lines.append(f"  - {check.get('check_id')}: {check.get('status')} — {check.get('evidence')}")
            else:
                lines.append(f"  - note: {check}")
    else:
        lines.append('- 없음')
    lines += ['','## 제안 번역/수정',qa['suggested_ko'],'','## self-verification','```json',json.dumps(qa['self_verification'], ensure_ascii=False, indent=2),'```']
    lines += ['','## qa-reviewer final synthesis','```json',json.dumps(qa.get('qa_reviewer_result', {}), ensure_ascii=False, indent=2),'```']
    lines += ['','## harness meta-audit','```json',json.dumps(qa.get('harness_meta_audit', {}), ensure_ascii=False, indent=2),'```','','## 학습 반영 제안']
    if qa['learning_update_proposal']:
        for p in qa['learning_update_proposal']: lines.append(f"- {p['type']}: {p['issue_id']} / {p['proposal']} (승인 필요)")
    else: lines.append('- 없음')
    return '\n'.join(lines)+'\n'

def write_run_review_files(out: Path, run_id: str, items: list[dict]) -> None:
    review = out / 'review'; review.mkdir(parents=True, exist_ok=True)
    files = {'human_review_queue.jsonl': [],'learning_update_proposals.jsonl': [],'pattern_update_proposals.jsonl': [],'glossary_update_proposals.jsonl': [],'regression_update_proposals.jsonl': [],'source_pdf_check_queue.jsonl': [],'source_text_issue_queue.jsonl': [],'lore_ontology_review_queue.jsonl': [],'patch_note_review_queue.jsonl': [],'meta_harness_review_queue.jsonl': []}
    for item in items:
        code = item['code']
        qa_path = out / 'output' / 'qa_json' / f'{code}.qa.json'
        if not qa_path.exists():
            continue
        qa = json.loads(qa_path.read_text(encoding='utf-8'))
        if qa.get('requires_human_review') or qa.get('learning_update_proposal'):
            files['human_review_queue.jsonl'].append({'run_id':run_id,'card_id':code,'qa_json':str(qa_path),'reason':'review required or learning proposal present'})
        for source_issue in qa.get('source_quality_issues', []):
            files['source_text_issue_queue.jsonl'].append({'run_id':run_id,'card_id':code,'qa_json':str(qa_path), **source_issue})
        for proposal in qa.get('learning_update_proposal',[]):
            issue_id = proposal.get('issue_id','')
            row = {'run_id':run_id,'card_id':code, **proposal}
            files['learning_update_proposals.jsonl'].append(row)
            route = proposal.get('route') or ''
            ptype = proposal.get('type') or ''
            if route in ['parser_rule', 'syntax_rule', 'cross_card_consistency_review'] or 'SYNTAX' in issue_id or 'Pattern' in ptype or 'syntax' in ptype.lower():
                files['pattern_update_proposals.jsonl'].append(row)
            if route == 'glossary_candidate' or 'TERM' in issue_id or 'term' in ptype.lower() or 'glossary' in ptype.lower():
                files['glossary_update_proposals.jsonl'].append(row)
            if proposal.get('test_id'):
                files['regression_update_proposals.jsonl'].append({'run_id':run_id,'card_id':code,'test_id':proposal.get('test_id'),'route':route,'expected_fix':proposal.get('card_fix') or proposal.get('proposal'),'requires_human_approval':True})
            if route == 'source_pdf_check' or ptype == 'source_pdf_check':
                files['source_pdf_check_queue.jsonl'].append(row)
            if route == 'lore_ontology_review':
                files['lore_ontology_review_queue.jsonl'].append(row)
            if route == 'patch_note_review':
                files['patch_note_review_queue.jsonl'].append(row)
            if route == 'meta_harness_review' or proposal.get('meta_audit_candidate') or ptype == 'harness_meta_gap':
                files['meta_harness_review_queue.jsonl'].append(row)
    for name, rows in files.items():
        path = review / name
        path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')

def write_summaries(out: Path, summary: dict) -> None:
    (out/'run_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out/'output').mkdir(parents=True, exist_ok=True)
    (out/'output'/'run_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id', default='RUN_AGENT_PIPELINE')
    parser.add_argument('--cards', default='', help='Optional comma-separated card codes resolved from data/originals and data/translations. Prefer --input-json for portable public use.')
    parser.add_argument('--input-json', help='Document-schema card JSON list or single card object')
    parser.add_argument('--compare-prev-run', help='Previous run id/path to compare against after writing current QA artifacts')
    parser.add_argument('--human-feedback', help='Optional JSONL feedback file for iteration comparison')
    args=parser.parse_args()
    out=SHARED_QA_ROOT/'runs'/args.run_id; out.mkdir(parents=True, exist_ok=True)
    if args.input_json:
        raw=json.loads(Path(args.input_json).read_text(encoding='utf-8'))
        cards=raw if isinstance(raw, list) else [raw]
        contexts=[new_context_from_card(card,args.run_id) for card in cards]
        scope=f"document-schema card-level QA: {', '.join(c['code'] for c in contexts)}"
        batch_indexes=build_batch_indexes(args.run_id, contexts, out, scope)
        items=[run_context_card(context,out,legacy_root=False,batch_indexes=batch_indexes) for context in contexts]
    else:
        codes=[c.strip() for c in args.cards.split(',') if c.strip()]
        contexts=[new_context(code,args.run_id) for code in codes]
        scope=f"agent pipeline card-level QA: {', '.join(codes)}"
        batch_indexes=build_batch_indexes(args.run_id, contexts, out, scope)
        items=[run_context_card(context,out,legacy_root=True,batch_indexes=batch_indexes) for context in contexts]
    summary={'run_id':args.run_id,'created_at':now_iso(),'scope':scope,'auto_apply':False,'memory_updates_applied':False,'agent_sequence':[{'agent_name':name,'status':'configured'} for name in AGENT_SEQUENCE],'pipeline_steps':PIPELINE_NAMES,'batch_indexes':batch_indexes['refs'],'items':items,'batch_findings':['B architecture active: individual agent modules executed in sequence for each card.','Global style/consistency indexes are precomputed once per batch and card agents 5/6 use index lookup only.','Document output shape written under output/ and review/.','Memory update step remains skipped pending human approval.']}
    write_summaries(out, summary)
    write_run_review_files(out, args.run_id, items)
    if args.compare_prev_run:
        compare_path = QA_ROOT / 'scripts' / 'compare_qa_iterations.py'
        spec = importlib.util.spec_from_file_location('compare_qa_iterations', compare_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f'Unable to load iteration comparer: {compare_path}')
        compare_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compare_module)
        prev_run = compare_module.resolve_run(args.compare_prev_run)
        feedback_path = Path(args.human_feedback) if args.human_feedback else None
        iteration_summary = compare_module.compare_iterations(prev_run, out, feedback_path)
        learning_rows = iteration_summary.pop('_learning_rows')
        false_positive_rows = iteration_summary.pop('_false_positive_rows')
        review_dir = out / 'review'
        review_dir.mkdir(parents=True, exist_ok=True)
        (review_dir / 'iteration_summary.json').write_text(json.dumps(iteration_summary, ensure_ascii=False, indent=2), encoding='utf-8')
        (review_dir / 'iteration_summary.md').write_text(compare_module.build_md(iteration_summary), encoding='utf-8')
        compare_module.write_jsonl(review_dir / 'iteration_learning_candidates.jsonl', learning_rows)
        compare_module.write_jsonl(review_dir / 'iteration_false_positive_candidates.jsonl', false_positive_rows)
        summary['iteration_review'] = {
            'previous_run': iteration_summary['previous_run'],
            'current_run': iteration_summary['current_run'],
            'iteration_status': iteration_summary['iteration_status'],
            'safe_to_finalize': iteration_summary['safe_to_finalize'],
            'resolved_blockers': len(iteration_summary['resolved_blockers']),
            'persistent_blockers': len(iteration_summary['persistent_blockers']),
            'new_blockers': len(iteration_summary['new_blockers']),
            'learning_candidates': len(iteration_summary['learning_candidates']),
            'false_positive_candidates': len(iteration_summary['false_positive_candidates']),
        }
        write_summaries(out, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2)); print(out)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
