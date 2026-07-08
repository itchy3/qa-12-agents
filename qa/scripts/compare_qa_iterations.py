#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
QA_ROOT = PROJECT / 'qa'
RUNS_ROOT = PROJECT / 'qa' / 'runs'

BLOCKING_SEVERITIES = {'Critical', 'Major'}


def resolve_run(value: str) -> Path:
    path = Path(value)
    if path.exists():
        return path
    candidate = RUNS_ROOT / value
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f'Run not found: {value}')


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def load_run(run: Path) -> dict[str, dict[str, Any]]:
    qa_dir = run / 'output' / 'qa_json'
    if not qa_dir.exists():
        qa_dir = run / 'qa_json'
    if not qa_dir.exists():
        raise FileNotFoundError(f'No qa_json directory in run: {run}')
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(qa_dir.glob('*.qa.json')):
        qa = read_json(path)
        card_id = qa.get('card_id') or path.name.replace('.qa.json', '')
        out[card_id] = qa
    return out


def load_feedback(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if not path:
        return {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        card_id = row.get('card_id') or row.get('source_item_id') or ''
        issue_id = row.get('issue_id') or ''
        if card_id and issue_id:
            rows[(card_id, issue_id)] = row
    return rows


def issue_key(issue: dict[str, Any]) -> str:
    if issue.get('issue_key'):
        return str(issue['issue_key'])
    issue_id = str(issue.get('issue_id', ''))
    # Prefer durable regression/test keys over card/run prefixes.
    for pat in [r'(REG_[A-Z0-9_]+)', r'(TERM_[A-Z0-9_]+)', r'(UNRESOLVED-[0-9A-Z_]+)', r'(PATCH-[0-9A-Z_-]+)']:
        m = re.search(pat, issue_id)
        if m:
            return m.group(1)
    issue_type = re.sub(r'\s+', '_', str(issue.get('issue_type', '')).strip().lower())
    source = str(issue.get('span_source') or '')[:80]
    ko = str(issue.get('span_ko') or '')[:80]
    return '|'.join([issue_type, source, ko]).strip('|') or issue_id


def issues_by_key(qa: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {issue_key(issue): issue for issue in qa.get('issues', [])}


def is_blocking(issue: dict[str, Any] | None) -> bool:
    if not issue:
        return False
    return issue.get('severity') in BLOCKING_SEVERITIES or bool(issue.get('blocks_approval'))


def proposals_by_issue(qa: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for proposal in qa.get('learning_update_proposal', []) or []:
        issue_id = proposal.get('issue_id')
        if issue_id:
            out.setdefault(issue_id, []).append(proposal)
    return out


def make_learning_candidate(card_id: str, issue: dict[str, Any], feedback: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    proposal = proposals[0] if proposals else {}
    return {
        'candidate_type': 'regression_test' if (proposal.get('route') == 'parser_rule' or proposal.get('test_id')) else 'process_learning',
        'card_id': card_id,
        'issue_id': issue.get('issue_id'),
        'issue_key': issue_key(issue),
        'source_span': issue.get('span_source'),
        'before_span': feedback.get('before_span') or issue.get('span_ko'),
        'after_span': feedback.get('after_span'),
        'human_feedback': feedback.get('human_feedback'),
        'human_action': feedback.get('action'),
        'human_note': feedback.get('note'),
        'route': proposal.get('route') or 'human_feedback_learning',
        'test_id': proposal.get('test_id') or issue_key(issue),
        'lesson': proposal.get('proposal') or feedback.get('note') or issue.get('evidence'),
        'requires_human_approval': True,
        'promotion_policy': 'proposal_only_until_human_approval',
    }


def make_false_positive_candidate(card_id: str, issue: dict[str, Any], feedback: dict[str, Any]) -> dict[str, Any]:
    return {
        'candidate_type': 'false_positive_suppression',
        'card_id': card_id,
        'issue_id': issue.get('issue_id'),
        'issue_key': issue_key(issue),
        'issue_type': issue.get('issue_type'),
        'human_feedback': feedback.get('human_feedback'),
        'human_action': feedback.get('action'),
        'human_note': feedback.get('note'),
        'policy_candidate': feedback.get('policy_candidate'),
        'requires_human_approval': True,
        'promotion_policy': 'proposal_only_until_human_approval',
    }


def compare_card(card_id: str, prev: dict[str, Any] | None, current: dict[str, Any] | None, feedback: dict[tuple[str, str], dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    prev_issues = issues_by_key(prev or {})
    cur_issues = issues_by_key(current or {})
    prev_by_id = {issue.get('issue_id'): issue for issue in (prev or {}).get('issues', [])}
    prev_proposals = proposals_by_issue(prev or {})
    false_positive_issue_ids = {
        issue_id for (fb_card_id, issue_id), row in feedback.items()
        if fb_card_id == card_id and str(row.get('human_feedback', '')).lower() in {'false_positive', 'overruled', 'rejected'}
    }

    resolved_keys = sorted(set(prev_issues) - set(cur_issues))
    persistent_keys = sorted(set(prev_issues) & set(cur_issues))
    new_keys = sorted(set(cur_issues) - set(prev_issues))

    resolved = [prev_issues[k].get('issue_id') for k in resolved_keys if prev_issues[k].get('issue_id') not in false_positive_issue_ids]
    persistent = [cur_issues[k].get('issue_id') for k in persistent_keys if cur_issues[k].get('issue_id') not in false_positive_issue_ids]
    new = [cur_issues[k].get('issue_id') for k in new_keys]

    learning_candidates: list[dict[str, Any]] = []
    false_positive_candidates: list[dict[str, Any]] = []

    for issue_id, issue in prev_by_id.items():
        fb = feedback.get((card_id, str(issue_id)))
        if not fb:
            continue
        verdict = str(fb.get('human_feedback', '')).lower()
        if verdict in {'accepted', 'fixed', 'confirmed'} and issue_id in resolved:
            learning_candidates.append(make_learning_candidate(card_id, issue, fb, prev_proposals.get(str(issue_id), [])))
        elif verdict in {'false_positive', 'overruled', 'rejected'}:
            false_positive_candidates.append(make_false_positive_candidate(card_id, issue, fb))

    new_blockers = [issue_id for issue_id in new if is_blocking(issue_by_id(issue_id, cur_issues))]
    persistent_blockers = [issue_id for issue_id in persistent if is_blocking(issue_by_id(issue_id, cur_issues))]
    resolved_blockers = [issue_id for issue_id in resolved if is_blocking(issue_by_id(issue_id, prev_issues))]

    if new_blockers or persistent_blockers:
        status = 'needs_another_pass'
    elif current and current.get('verdict') == 'Pass':
        status = 'converged'
    elif current:
        status = 'improved_needs_review'
    else:
        status = 'missing_current_card'

    card_summary = {
        'card_id': card_id,
        'previous_verdict': (prev or {}).get('verdict'),
        'current_verdict': (current or {}).get('verdict'),
        'iteration_status': status,
        'safe_to_finalize': status == 'converged' and not false_positive_candidates,
        'resolved_issues': resolved,
        'persistent_issues': persistent,
        'new_issues': new,
        'resolved_blockers': resolved_blockers,
        'persistent_blockers': persistent_blockers,
        'new_blockers': new_blockers,
        'false_positive_candidates': [row['issue_id'] for row in false_positive_candidates],
        'learning_candidates': [row['issue_id'] for row in learning_candidates],
        'previous_final_translation': (prev or {}).get('final_translation') or (prev or {}).get('current_ko'),
        'current_final_translation': (current or {}).get('final_translation') or (current or {}).get('current_ko'),
    }
    return card_summary, learning_candidates, false_positive_candidates


def issue_by_id(issue_id: str | None, issues: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not issue_id:
        return None
    for issue in issues.values():
        if issue.get('issue_id') == issue_id:
            return issue
    return None


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def build_md(summary: dict[str, Any]) -> str:
    lines = [
        f"# QA iteration summary: {summary['previous_run']} → {summary['current_run']}",
        '',
        f"- iteration_status: {summary['iteration_status']}",
        f"- safe_to_finalize: {summary['safe_to_finalize']}",
        f"- cards: {summary['card_count']}",
        f"- resolved blockers: {len(summary['resolved_blockers'])}",
        f"- persistent blockers: {len(summary['persistent_blockers'])}",
        f"- new blockers: {len(summary['new_blockers'])}",
        f"- false positive candidates: {len(summary['false_positive_candidates'])}",
        f"- learning candidates: {len(summary['learning_candidates'])}",
        '',
    ]
    for card_id, card in summary['cards'].items():
        lines += [
            f"## {card_id}",
            f"- status: {card['iteration_status']}",
            f"- verdict: {card['previous_verdict']} → {card['current_verdict']}",
            f"- resolved issues: {', '.join(card['resolved_issues']) or 'none'}",
            f"- persistent issues: {', '.join(card['persistent_issues']) or 'none'}",
            f"- new issues: {', '.join(card['new_issues']) or 'none'}",
            f"- false positive candidates: {', '.join(card['false_positive_candidates']) or 'none'}",
            f"- learning candidates: {', '.join(card['learning_candidates']) or 'none'}",
            '',
        ]
    return '\n'.join(lines)


def compare_iterations(prev_run: Path, current_run: Path, feedback_path: Path | None = None) -> dict[str, Any]:
    prev_cards = load_run(prev_run)
    cur_cards = load_run(current_run)
    feedback = load_feedback(feedback_path)
    all_card_ids = sorted(set(prev_cards) | set(cur_cards))

    card_summaries: dict[str, Any] = {}
    learning_rows: list[dict[str, Any]] = []
    false_positive_rows: list[dict[str, Any]] = []
    for card_id in all_card_ids:
        card_summary, card_learning, card_fp = compare_card(card_id, prev_cards.get(card_id), cur_cards.get(card_id), feedback)
        card_summaries[card_id] = card_summary
        learning_rows.extend(card_learning)
        false_positive_rows.extend(card_fp)

    resolved_blockers = [issue for card in card_summaries.values() for issue in card['resolved_blockers']]
    persistent_blockers = [issue for card in card_summaries.values() for issue in card['persistent_blockers']]
    new_blockers = [issue for card in card_summaries.values() for issue in card['new_blockers']]
    if new_blockers or persistent_blockers:
        status = 'needs_another_pass'
    elif false_positive_rows or learning_rows:
        status = 'converged_with_learning_pending'
    else:
        status = 'converged'

    return {
        'previous_run': prev_run.name,
        'current_run': current_run.name,
        'iteration_status': status,
        'safe_to_finalize': status == 'converged',
        'card_count': len(all_card_ids),
        'resolved_blockers': resolved_blockers,
        'persistent_blockers': persistent_blockers,
        'new_blockers': new_blockers,
        'false_positive_candidates': [row['issue_id'] for row in false_positive_rows],
        'learning_candidates': [row['issue_id'] for row in learning_rows],
        'cards': card_summaries,
        '_learning_rows': learning_rows,
        '_false_positive_rows': false_positive_rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Compare two TES QA harness runs and summarize iteration convergence / learning candidates.')
    ap.add_argument('--prev-run', required=True, help='Previous run id or path')
    ap.add_argument('--current-run', required=True, help='Current run id or path')
    ap.add_argument('--human-feedback', help='Optional JSONL: card_id, issue_id, human_feedback, action, note, before_span, after_span')
    ap.add_argument('--out-dir', help='Output directory. Defaults to <current-run>/review')
    args = ap.parse_args(argv)

    prev_run = resolve_run(args.prev_run)
    current_run = resolve_run(args.current_run)
    feedback_path = Path(args.human_feedback) if args.human_feedback else None
    out_dir = Path(args.out_dir) if args.out_dir else current_run / 'review'
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = compare_iterations(prev_run, current_run, feedback_path)
    learning_rows = summary.pop('_learning_rows')
    false_positive_rows = summary.pop('_false_positive_rows')

    (out_dir / 'iteration_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out_dir / 'iteration_summary.md').write_text(build_md(summary), encoding='utf-8')
    write_jsonl(out_dir / 'iteration_learning_candidates.jsonl', learning_rows)
    write_jsonl(out_dir / 'iteration_false_positive_candidates.jsonl', false_positive_rows)
    print(json.dumps({
        'iteration_status': summary['iteration_status'],
        'safe_to_finalize': summary['safe_to_finalize'],
        'card_count': summary['card_count'],
        'resolved_blockers': len(summary['resolved_blockers']),
        'persistent_blockers': len(summary['persistent_blockers']),
        'new_blockers': len(summary['new_blockers']),
        'learning_candidates': len(summary['learning_candidates']),
        'false_positive_candidates': len(summary['false_positive_candidates']),
        'out_dir': str(out_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
