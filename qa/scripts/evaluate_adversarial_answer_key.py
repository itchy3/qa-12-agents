#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DIRECT_ISSUE_FIELDS = [
    'issue_id',
    'issue_type',
    'severity',
    'span_source',
    'span_ko',
    'evidence',
    'suggested_fix',
    'semantic_diff',
    'review_status',
    'issue_status',
]
IGNORE_TOKENS = {
    '합니다', '있는', '없는', '그리고', '또는', '자신의', '각', '하나', '개를', '개', '수', '레벨',
    '경우', '있다면', '있습니다', '없습니다', '으로', '에서', '에게', '마다', '까지', '부터', '대신',
}
KO_SUFFIXES = tuple(sorted({
    '께서는', '께서', '으로부터', '으로써', '으로서', '이라는', '라는', '이라도', '이라면', '이라고',
    '으로', '에서', '에게', '부터', '까지', '처럼', '보다', '마저', '조차',
    '이나', '나', '라도', '라면', '라고', '이며', '이고', '이다', '인',
    '이', '가', '은', '는', '을', '를', '와', '과', '로', '에', '의', '도', '만',
}, key=len, reverse=True))


def norm(text: Any) -> str:
    if text is None:
        return ''
    text = str(text).lower()
    text = re.sub(r'\[\[([^]|]+)\|([^]]+)\]\]', r'\1 \2', text)
    text = re.sub(r'\[\[([^]]+)\]\]', r'\1', text)
    text = re.sub(r'[`*_>#+\-]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def direct_issue_text(issue: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in DIRECT_ISSUE_FIELDS:
        value = issue.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            parts.append(json.dumps(value, ensure_ascii=False))
        else:
            parts.append(str(value))
    return norm(' '.join(parts))


def _strip_ko_suffix_token(tok: str) -> str:
    # Normalize Korean particle/quotative suffixes for issue matching. This lets
    # evaluator count a terminology issue whose observed term is `초노-주힐르라`
    # against an answer-key phrase like `초노-주힐르라는 ...` without admitting
    # unrelated broad context matches.
    if not re.search(r'[가-힣]', tok):
        return tok
    for suffix in KO_SUFFIXES:
        if tok.endswith(suffix) and len(tok) > len(suffix) + 1:
            return tok[:-len(suffix)]
    return tok


def tokens(text: str) -> list[str]:
    out = []
    for tok in re.split(r'[^0-9a-z가-힣]+', norm(text)):
        if len(tok) < 2:
            continue
        tok = _strip_ko_suffix_token(tok)
        if len(tok) < 2:
            continue
        if tok in IGNORE_TOKENS:
            continue
        out.append(tok)
    return out


def _token_or_prefix_in_issue(tok: str, issue_text: str) -> bool:
    if tok in issue_text:
        return True
    if re.search(r'[가-힣]', tok):
        # Korean proper-noun near misses are often reported as the term surface
        # while the answer key stores the containing phrase with particles.
        # Require a reasonably distinctive prefix to avoid broad one-syllable hits.
        for cut in range(len(tok) - 1, 3, -1):
            if tok[:cut] in issue_text:
                return True
    return False


def loose_match(expected: dict[str, Any], issue_text: str) -> tuple[bool, str]:
    span = norm(expected.get('mutated_ko_span') or '')
    if span and span in issue_text:
        return True, 'mutated_span_exact'
    source_rule = norm(expected.get('span_source_or_rule') or '')
    if source_rule and source_rule in issue_text:
        return True, 'source_or_rule_exact'

    span_tokens = tokens(span)
    if not span_tokens:
        return False, ''
    hit_tokens = [tok for tok in span_tokens if _token_or_prefix_in_issue(tok, issue_text)]
    # Require at least two content-token hits when possible, or one distinctive long token.
    if len(hit_tokens) >= min(2, len(span_tokens)):
        return True, 'mutated_span_token_overlap:' + ','.join(hit_tokens[:6])
    if any(len(tok) >= 5 for tok in hit_tokens):
        return True, 'mutated_span_distinctive_token:' + ','.join(hit_tokens[:6])
    source_tokens = tokens(source_rule)
    source_hits = [tok for tok in source_tokens if _token_or_prefix_in_issue(tok, issue_text)]
    if any(len(tok) >= 5 for tok in source_hits):
        return True, 'source_or_rule_distinctive_token:' + ','.join(source_hits[:6])
    return False, ''


def evaluate(run_dir: Path, answer_key: Path) -> dict[str, Any]:
    key = json.loads(answer_key.read_text(encoding='utf-8'))
    qa_dir = run_dir / 'output' / 'qa_json'
    expected_rows: list[dict[str, Any]] = []
    card_summaries = {}
    missing_qa = []
    for item in key['items']:
        card_id = item['card_id']
        qa_path = qa_dir / f'{card_id}.qa.json'
        if not qa_path.exists():
            missing_qa.append(card_id)
            issues = []
            qa = {}
        else:
            qa = json.loads(qa_path.read_text(encoding='utf-8'))
            issues = qa.get('issues') or []
        issue_texts = [(issue, direct_issue_text(issue)) for issue in issues]
        llm_usage = qa.get('llm_usage') or {}
        card_summaries[card_id] = {
            'verdict': qa.get('verdict'),
            'score': qa.get('score'),
            'issue_count': len(issues),
            'major_issue_count': sum(1 for i in issues if i.get('severity') in {'Major', 'Critical'}),
            'llm_agents_used': sorted(k for k, v in llm_usage.items() if v.get('used')),
            'llm_errors': {k: v.get('error') for k, v in llm_usage.items() if v.get('error')},
        }
        for err in item.get('expected_errors') or []:
            expected_status = err.get('expected_detection_status') or 'should_flag'
            strict_matches = []
            loose_matches = []
            mutated = norm(err.get('mutated_ko_span') or '')
            source_rule = norm(err.get('span_source_or_rule') or '')
            for issue, text in issue_texts:
                exact_reason = ''
                if mutated and mutated in text:
                    exact_reason = 'mutated_span_exact'
                elif source_rule and source_rule in text:
                    exact_reason = 'source_or_rule_exact'
                if exact_reason:
                    strict_matches.append({
                        'issue_id': issue.get('issue_id'),
                        'issue_type': issue.get('issue_type'),
                        'severity': issue.get('severity'),
                        'reason': exact_reason,
                    })
                ok, reason = loose_match(err, text)
                if ok:
                    loose_matches.append({
                        'issue_id': issue.get('issue_id'),
                        'issue_type': issue.get('issue_type'),
                        'severity': issue.get('severity'),
                        'reason': reason,
                    })
            expected_rows.append({
                'card_id': card_id,
                'error_id': err.get('error_id'),
                'error_type': err.get('error_type'),
                'severity': err.get('severity'),
                'expected_detection_status': expected_status,
                'mutated_ko_span': err.get('mutated_ko_span'),
                'span_source_or_rule': err.get('span_source_or_rule'),
                'strict_exact_detected': bool(strict_matches),
                'strict_loose_detected': bool(loose_matches),
                'strict_exact_matches': strict_matches,
                'strict_loose_matches': loose_matches,
                'explanation': err.get('explanation'),
            })

    should = [r for r in expected_rows if r['expected_detection_status'] == 'should_flag']
    decoys = [r for r in expected_rows if r['expected_detection_status'] != 'should_flag']
    by_type = defaultdict(lambda: {'total': 0, 'exact': 0, 'loose': 0})
    by_card = defaultdict(lambda: {'total': 0, 'exact': 0, 'loose': 0})
    for r in should:
        for bucket in [by_type[r['error_type']], by_card[r['card_id']]]:
            bucket['total'] += 1
            bucket['exact'] += int(r['strict_exact_detected'])
            bucket['loose'] += int(r['strict_loose_detected'])

    summary = {
        'run_dir': str(run_dir),
        'answer_key': str(answer_key),
        'qa_json_count': len(list(qa_dir.glob('*.qa.json'))) if qa_dir.exists() else 0,
        'missing_qa_json': missing_qa,
        'expected_total': len(expected_rows),
        'should_flag_total': len(should),
        'decoy_total': len(decoys),
        'strict_exact_detected': sum(r['strict_exact_detected'] for r in should),
        'strict_loose_detected': sum(r['strict_loose_detected'] for r in should),
        'strict_exact_rate': round(sum(r['strict_exact_detected'] for r in should) / len(should), 4) if should else None,
        'strict_loose_rate': round(sum(r['strict_loose_detected'] for r in should) / len(should), 4) if should else None,
        'decoys_flagged_exact': sum(r['strict_exact_detected'] for r in decoys),
        'decoys_flagged_loose': sum(r['strict_loose_detected'] for r in decoys),
        'card_summaries': card_summaries,
        'by_error_type': dict(sorted(by_type.items())),
        'by_card': dict(sorted(by_card.items())),
        'misses_exact': [r for r in should if not r['strict_exact_detected']],
        'misses_loose': [r for r in should if not r['strict_loose_detected']],
        'detected_rows': [r for r in expected_rows if r['strict_exact_detected'] or r['strict_loose_detected']],
    }
    return summary


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = []
    lines.append('# Adversarial Answer-Key Comparison')
    lines.append('')
    lines.append(f"Run: `{report['run_dir']}`")
    lines.append(f"Answer key: `{report['answer_key']}`")
    lines.append('')
    lines.append('## Summary')
    lines.append('')
    lines.append(f"- QA JSON files: {report['qa_json_count']}")
    lines.append(f"- Missing QA JSON: {report['missing_qa_json'] or 'none'}")
    lines.append(f"- Expected findings total: {report['expected_total']}")
    lines.append(f"- Should-flag findings: {report['should_flag_total']}")
    lines.append(f"- Decoys/non-flag findings: {report['decoy_total']}")
    lines.append(f"- Strict exact issue-only detected: {report['strict_exact_detected']}/{report['should_flag_total']} = {report['strict_exact_rate']:.1%}")
    lines.append(f"- Strict loose issue-only detected: {report['strict_loose_detected']}/{report['should_flag_total']} = {report['strict_loose_rate']:.1%}")
    lines.append(f"- Decoys flagged exact/loose: {report['decoys_flagged_exact']}/{report['decoys_flagged_loose']}")
    lines.append('')
    lines.append('Strict issue-only means only `issues[]` direct evidence fields were searched; copied full `current_ko`, source text, glossary excerpts, and markdown context were excluded.')
    lines.append('')
    lines.append('## By card')
    lines.append('')
    lines.append('| Card | Expected | Exact | Loose | Verdict | Issues | LLM used | LLM errors |')
    lines.append('|---|---:|---:|---:|---|---:|---|---|')
    for card, stats in report['by_card'].items():
        cs = report['card_summaries'].get(card, {})
        llm_used = ', '.join(cs.get('llm_agents_used') or [])
        llm_errors = '; '.join(f"{k}:{v}" for k, v in (cs.get('llm_errors') or {}).items()) or ''
        lines.append(f"| {card} | {stats['total']} | {stats['exact']} | {stats['loose']} | {cs.get('verdict')} | {cs.get('issue_count')} | {llm_used} | {llm_errors} |")
    lines.append('')
    lines.append('## By error type')
    lines.append('')
    lines.append('| Error type | Expected | Exact | Loose |')
    lines.append('|---|---:|---:|---:|')
    for typ, stats in report['by_error_type'].items():
        lines.append(f"| {typ} | {stats['total']} | {stats['exact']} | {stats['loose']} |")
    lines.append('')
    lines.append('## Strict-exact misses')
    lines.append('')
    for r in report['misses_exact']:
        lines.append(f"- `{r['error_id']}` {r['card_id']} / {r['error_type']} / {r['severity']}: `{r['mutated_ko_span']}` — {r['explanation']}")
    lines.append('')
    lines.append('## Detected rows')
    lines.append('')
    for r in report['detected_rows']:
        matches = r['strict_exact_matches'] or r['strict_loose_matches']
        mids = ', '.join(str(m.get('issue_id')) for m in matches[:5])
        lines.append(f"- `{r['error_id']}` {r['card_id']} / {r['error_type']}: exact={r['strict_exact_detected']} loose={r['strict_loose_detected']} via {mids}")
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--answer-key', required=True)
    ap.add_argument('--out-json')
    ap.add_argument('--out-md')
    args = ap.parse_args()
    report = evaluate(Path(args.run_dir), Path(args.answer_key))
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.out_md:
        write_markdown(report, Path(args.out_md))
    print(json.dumps({
        'qa_json_count': report['qa_json_count'],
        'expected_total': report['expected_total'],
        'should_flag_total': report['should_flag_total'],
        'strict_exact_detected': report['strict_exact_detected'],
        'strict_exact_rate': report['strict_exact_rate'],
        'strict_loose_detected': report['strict_loose_detected'],
        'strict_loose_rate': report['strict_loose_rate'],
        'decoys_flagged_exact': report['decoys_flagged_exact'],
        'decoys_flagged_loose': report['decoys_flagged_loose'],
        'missing_qa_json': report['missing_qa_json'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
