#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
if '.hermes' in HERE.parts:
    PROJECT = HERE.parents[3]
else:
    PROJECT = HERE.parents[2]
RUNNER = PROJECT / 'qa' / 'scripts' / 'run_agent_pipeline.py'
if not RUNNER.exists():
    RUNNER = PROJECT / '.hermes' / 'qa' / 'scripts' / 'run_agent_pipeline.py'
TEMPLATE = PROJECT / 'templates' / 'sample_cards.json'
if not TEMPLATE.exists():
    TEMPLATE = PROJECT / 'TES-QA-Harness-Share' / 'templates' / 'sample_cards.json'
RUNS_ROOT = PROJECT / 'qa' / 'runs'
if not RUNS_ROOT.exists() and '.hermes' in RUNNER.parts:
    RUNS_ROOT = PROJECT / '.hermes' / 'qa' / 'runs'


class LlmAgentHybridTests(unittest.TestCase):
    def test_core_agents_use_compact_llm_json_when_enabled(self):
        run_id = 'TEST_LLM_AGENT_HYBRID'
        out = RUNS_ROOT / run_id
        if out.exists():
            shutil.rmtree(out)
        fake = {
            'source-meaning-checker': {
                'source_analysis': {
                    'mode': 'llm_json',
                    'semantic_pattern': 'SAME_ACTOR_OPTIONAL_RECOVERY',
                    'source_slots': {
                        'trigger': 'after defeating an enemy',
                        'actor': 'that adventurer',
                        'action': 'recover',
                        'amount': '1 health',
                        'modal': 'may',
                        'target_scope': 'same actor only',
                    },
                    'confidence': 0.93,
                    'unknowns': [],
                    'needs_human_pattern_review': False,
                },
                'translation_slot_result': {
                    'mode': 'llm_json',
                    'ko_slots': {
                        'actor': 'each adventurer',
                        'action': 'recover',
                        'amount': '체력 1',
                        'modal': 'may',
                        'target_scope': 'all adventurers',
                    },
                    'slot_issues': [],
                },
            },
            'rules-lawyer': {
                'issues': [
                    {
                        'issue_id': 'CARD_001-LLM_TARGET_SCOPE_BROADENED',
                        'issue_type': 'Target scope mismatch',
                        'severity': 'Major',
                        'span_source': 'that adventurer',
                        'span_ko': '각 모험가',
                        'evidence': 'LLM rules-lawyer: source limits recovery to the defeating adventurer, but KO broadens it to each adventurer.',
                        'suggested_fix': '그 모험가/해당 모험가로 동일 행위자 범위를 보존합니다.',
                        'confidence': 0.94,
                        'blocks_approval': True,
                    }
                ],
                'rules_lawyer_result': {
                    'risk': 'Medium',
                    'scope_checks': [
                        {
                            'case_id': 'LLM_TARGET_SCOPE_BROADENED',
                            'scope_preserved': False,
                            'decision': 'target_scope_broadened_blocks_approval',
                        }
                    ],
                    'modal_checks': [],
                },
            },
            'korean-editor': {
                'translation_comparison': {
                    'status': 'compared',
                    'winner': 'polished_ko',
                    'candidate_decision': 'accept_polished_candidate',
                    'meaning_delta': 'scope repaired from each adventurer to same actor',
                    'rule_delta': 'safe',
                    'style_delta': 'clearer same-actor reference',
                    'safe_to_apply': False,
                    'requires_human_review': True,
                },
                'im_not_ai_result': {
                    'status': 'accepted',
                    'meaning_preserved': True,
                    'meaning_structure_preserved': True,
                    'register_preserved': True,
                    'candidate_decision': 'accept_polished_candidate',
                    'requires_human_review': True,
                    'checks': [],
                },
                'suggested_ko': '모험가가 적을 처치한 후, 그 모험가는 체력 1을 회복할 수 있습니다.',
            },
            'verifier': {
                'self_verification_patch': {
                    'llm_semantic_review': 'scope mismatch correctly detected; polished candidate repairs it but still needs human approval',
                    'meaning_preserved': False,
                }
            },
            'qa-reviewer': {
                'qa_reviewer_patch': {
                    'llm_review_summary': 'Needs revision until human accepts the scope fix.',
                    'final_decision_basis': 'llm_detected_blocking_scope_issue',
                }
            },
        }
        env = os.environ.copy()
        env['QA_LLM_ENABLED'] = '1'
        env['QA_LLM_FAKE_RESPONSES_JSON'] = json.dumps(fake, ensure_ascii=False)
        result = subprocess.run(
            [sys.executable, str(RUNNER), '--run-id', run_id, '--input-json', str(TEMPLATE)],
            cwd=str(PROJECT),
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        qa = json.loads((out / 'output' / 'qa_json' / 'CARD_001.qa.json').read_text(encoding='utf-8'))
        self.assertEqual(qa['source_analysis']['mode'], 'llm_json')
        self.assertEqual(qa['source_analysis']['source_slots']['target_scope'], 'same actor only')
        self.assertTrue(any(i['issue_id'] == 'CARD_001-LLM_TARGET_SCOPE_BROADENED' for i in qa['issues']))
        self.assertEqual(qa['translation_comparison']['candidate_decision'], 'accept_polished_candidate')
        self.assertEqual(qa['qa_reviewer_result']['final_decision_basis'], 'llm_detected_blocking_scope_issue')
        usage = qa.get('llm_usage', {})
        for agent in ['source-meaning-checker', 'rules-lawyer', 'korean-editor', 'verifier', 'qa-reviewer']:
            self.assertTrue(usage.get(agent, {}).get('used'), f'{agent} did not record LLM usage')
            self.assertLessEqual(usage[agent].get('input_chars', 999999), 6000)


if __name__ == '__main__':
    unittest.main()
