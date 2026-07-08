#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
QA_ROOT = PROJECT / 'qa'
SCRIPT = QA_ROOT / 'scripts' / 'compare_qa_iterations.py'


def write_qa(run_id: str, card_id: str, *, current_ko: str, issues: list[dict], proposals: list[dict] | None = None, verdict: str = 'Needs revision') -> Path:
    run = QA_ROOT / 'runs' / run_id
    qa_dir = run / 'output' / 'qa_json'
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa = {
        'card_id': card_id,
        'item_id': f'Test/{card_id}',
        'run_id': run_id,
        'source_text': 'After each time an adventurer defeats an enemy, that adventurer may recover 1 health, if possible.',
        'current_ko': current_ko,
        'final_translation': current_ko,
        'issues': issues,
        'learning_update_proposal': proposals or [],
        'self_verification': {
            'blocking_issue_ids': [i['issue_id'] for i in issues if i.get('severity') in {'Critical', 'Major'}],
            'blocking_issue_pass': not any(i.get('severity') in {'Critical', 'Major'} for i in issues),
        },
        'qa_reviewer_result': {
            'final_verdict': verdict,
            'blocking_issue_ids': [i['issue_id'] for i in issues if i.get('severity') in {'Critical', 'Major'}],
        },
        'verdict': verdict,
        'score': 82 if verdict == 'Needs revision' else 97,
        'requires_human_review': verdict != 'Pass',
    }
    path = qa_dir / f'{card_id}.qa.json'
    path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


class IterationReviewerTests(unittest.TestCase):
    def test_compares_runs_and_turns_human_feedback_into_learning_and_false_positive_candidates(self):
        prev_run = 'TEST_ITER_REVIEW_PREV'
        current_run = 'TEST_ITER_REVIEW_CURRENT'
        for run_id in [prev_run, current_run]:
            run = QA_ROOT / 'runs' / run_id
            if run.exists():
                subprocess.run(['rm', '-rf', str(run)], check=True)

        card_id = 'ITER_SCOPE_CARD'
        write_qa(
            prev_run,
            card_id,
            current_ko='모험가가 적을 처치할 때마다, 가능하다면 각 모험가는 체력 1을 회복할 수 있습니다.',
            issues=[
                {
                    'issue_id': f'{card_id}-REG_TARGET_SCOPE_BROADENED',
                    'issue_type': 'Target scope mismatch',
                    'severity': 'Major',
                    'span_source': 'that adventurer',
                    'span_ko': '각 모험가',
                    'evidence': 'same actor reference broadened to each actor',
                    'suggested_fix': '그 모험가',
                    'blocks_approval': True,
                },
                {
                    'issue_id': f'{card_id}-BATTLE_OBJECTIVE_POSITION_WARN',
                    'issue_type': 'Formatting/location warning',
                    'severity': 'Note',
                    'span_source': 'battleObjective',
                    'span_ko': '전투 목표',
                    'evidence': 'battleObjective appears in a different allowed section',
                    'suggested_fix': '프로젝트 포맷 확인',
                    'blocks_approval': False,
                },
                {
                    'issue_id': f'{card_id}-TERM_TENACITY_MISMATCH',
                    'issue_type': 'Terminology consistency',
                    'severity': 'Major',
                    'span_source': 'tenacity',
                    'span_ko': '인내',
                    'evidence': 'locked glossary mismatch',
                    'suggested_fix': '끈기',
                    'blocks_approval': True,
                },
            ],
            proposals=[
                {
                    'type': 'parser_rule',
                    'route': 'parser_rule',
                    'test_id': 'REG_TARGET_SCOPE_BROADENED',
                    'issue_id': f'{card_id}-REG_TARGET_SCOPE_BROADENED',
                    'proposal': 'Detect same actor references broadened into each/all targets.',
                    'requires_human_approval': True,
                }
            ],
        )
        write_qa(
            current_run,
            card_id,
            current_ko='모험가가 적을 처치할 때마다, 그 모험가는 체력 1을 회복할 수 있습니다.',
            issues=[
                {
                    'issue_id': f'{card_id}-TERM_TENACITY_MISMATCH',
                    'issue_type': 'Terminology consistency',
                    'severity': 'Major',
                    'span_source': 'tenacity',
                    'span_ko': '인내',
                    'evidence': 'locked glossary mismatch still present',
                    'suggested_fix': '끈기',
                    'blocks_approval': True,
                },
                {
                    'issue_id': f'{card_id}-REG_IF_POSSIBLE_OMITTED',
                    'issue_type': 'Scope qualifier omission',
                    'severity': 'Major',
                    'span_source': 'if possible',
                    'span_ko': '',
                    'evidence': 'condition disappeared after human edit',
                    'suggested_fix': '가능하다면',
                    'blocks_approval': True,
                },
            ],
        )
        feedback_path = QA_ROOT / 'tests' / 'fixtures' / 'TEST_ITER_REVIEW_FEEDBACK.jsonl'
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_rows = [
            {
                'card_id': card_id,
                'issue_id': f'{card_id}-REG_TARGET_SCOPE_BROADENED',
                'human_feedback': 'accepted',
                'action': 'changed_translation',
                'before_span': '각 모험가',
                'after_span': '그 모험가',
                'note': 'that adventurer는 처치한 동일 행위자라서 각 모험가가 아니라 그 모험가가 맞음',
            },
            {
                'card_id': card_id,
                'issue_id': f'{card_id}-BATTLE_OBJECTIVE_POSITION_WARN',
                'human_feedback': 'false_positive',
                'action': 'kept_translation',
                'note': '프로젝트 포맷상 battleObjective 위치 차이는 scope가 명확하면 허용',
                'policy_candidate': 'description_or_battleObjective_allowed_if_scope_clear',
            },
        ]
        feedback_path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in feedback_rows) + '\n', encoding='utf-8')

        result = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                '--prev-run', prev_run,
                '--current-run', current_run,
                '--human-feedback', str(feedback_path),
            ],
            cwd=str(PROJECT), text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        review_dir = QA_ROOT / 'runs' / current_run / 'review'
        summary = json.loads((review_dir / 'iteration_summary.json').read_text(encoding='utf-8'))
        card = summary['cards'][card_id]
        self.assertEqual(summary['iteration_status'], 'needs_another_pass')
        self.assertFalse(summary['safe_to_finalize'])
        self.assertIn(f'{card_id}-REG_TARGET_SCOPE_BROADENED', card['resolved_issues'])
        self.assertNotIn(f'{card_id}-BATTLE_OBJECTIVE_POSITION_WARN', card['resolved_issues'])
        self.assertIn(f'{card_id}-TERM_TENACITY_MISMATCH', card['persistent_issues'])
        self.assertIn(f'{card_id}-REG_IF_POSSIBLE_OMITTED', card['new_issues'])
        self.assertIn(f'{card_id}-BATTLE_OBJECTIVE_POSITION_WARN', card['false_positive_candidates'])
        self.assertEqual(card['iteration_status'], 'needs_another_pass')

        learning_rows = [json.loads(line) for line in (review_dir / 'iteration_learning_candidates.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertTrue(any(row['issue_id'] == f'{card_id}-REG_TARGET_SCOPE_BROADENED' and row['candidate_type'] == 'regression_test' for row in learning_rows), learning_rows)
        fp_rows = [json.loads(line) for line in (review_dir / 'iteration_false_positive_candidates.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertEqual(fp_rows[0]['issue_id'], f'{card_id}-BATTLE_OBJECTIVE_POSITION_WARN')

        md = (review_dir / 'iteration_summary.md').read_text(encoding='utf-8')
        self.assertIn('resolved blockers', md)
        self.assertIn('new blockers', md)
        self.assertIn('false positive candidates', md)

    def test_runner_can_compare_against_previous_run_after_current_pass(self):
        prev_run = 'TEST_ITER_RUNNER_PREV'
        current_run = 'TEST_ITER_RUNNER_CURRENT'
        for run_id in [prev_run, current_run]:
            run = QA_ROOT / 'runs' / run_id
            if run.exists():
                subprocess.run(['rm', '-rf', str(run)], check=True)

        card_id = 'ITER_RUNNER_CARD'
        write_qa(
            prev_run,
            card_id,
            current_ko='각 모험가는 아이템 1개를 버릴 수 있습니다.',
            issues=[{
                'issue_id': f'{card_id}-REG_MODAL_MUST_TO_MAY',
                'issue_type': 'Modal force mismatch',
                'severity': 'Major',
                'span_source': 'must discard 1 item',
                'span_ko': '버릴 수 있습니다',
                'evidence': 'must weakened to may',
                'suggested_fix': '버려야 합니다',
                'blocks_approval': True,
            }],
            proposals=[{
                'type': 'parser_rule', 'route': 'parser_rule', 'test_id': 'REG_MODAL_MUST_TO_MAY',
                'issue_id': f'{card_id}-REG_MODAL_MUST_TO_MAY',
                'proposal': 'Detect must weakened to may.',
                'requires_human_approval': True,
            }],
        )
        input_path = QA_ROOT / 'tests' / 'fixtures' / 'TEST_ITER_RUNNER_CURRENT.json'
        input_path.write_text(json.dumps([{
            'card_id': card_id,
            'category': {'component_type': 'card', 'card_type': 'effect_card', 'text_type': 'effect_text'},
            'source_text': 'Each adventurer must discard 1 item.',
            'current_ko': '각 모험가는 아이템 1개를 버려야 합니다.',
            'term_glossary': [], 'syntax_dictionary': [], 'prior_translations': [], 'approved_qa_logs': []
        }], ensure_ascii=False, indent=2), encoding='utf-8')
        feedback_path = QA_ROOT / 'tests' / 'fixtures' / 'TEST_ITER_RUNNER_FEEDBACK.jsonl'
        feedback_path.write_text(json.dumps({
            'card_id': card_id,
            'issue_id': f'{card_id}-REG_MODAL_MUST_TO_MAY',
            'human_feedback': 'accepted',
            'action': 'changed_translation',
            'before_span': '버릴 수 있습니다',
            'after_span': '버려야 합니다',
            'note': 'must는 선택 가능이 아니라 의무라서 버려야 합니다가 맞음',
        }, ensure_ascii=False) + '\n', encoding='utf-8')

        result = subprocess.run([
            sys.executable, str(QA_ROOT / 'scripts' / 'run_agent_pipeline.py'),
            '--run-id', current_run,
            '--input-json', str(input_path),
            '--compare-prev-run', prev_run,
            '--human-feedback', str(feedback_path),
        ], cwd=str(PROJECT), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        review_dir = QA_ROOT / 'runs' / current_run / 'review'
        summary = json.loads((review_dir / 'iteration_summary.json').read_text(encoding='utf-8'))
        self.assertIn(f'{card_id}-REG_MODAL_MUST_TO_MAY', summary['cards'][card_id]['resolved_issues'])
        self.assertEqual(summary['cards'][card_id]['iteration_status'], 'converged')
        learning_rows = [json.loads(line) for line in (review_dir / 'iteration_learning_candidates.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertTrue(any(row['issue_id'] == f'{card_id}-REG_MODAL_MUST_TO_MAY' for row in learning_rows), learning_rows)


if __name__ == '__main__':
    unittest.main(verbosity=2)
