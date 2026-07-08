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
    def test_llm_disputes_existing_blocker_without_auto_deleting_it(self):
        run_id = 'TEST_LLM_DISPUTED_BLOCKER'
        out = RUNS_ROOT / run_id
        if out.exists():
            shutil.rmtree(out)
        fake = {
            'source-meaning-checker': {
                'source_analysis': {
                    'mode': 'llm_json',
                    'semantic_pattern': 'SAME_ACTOR_OPTIONAL_RECOVERY',
                    'source_slots': {'actor': 'that adventurer', 'modal': 'may', 'target_scope': 'same actor only'},
                    'confidence': 0.92,
                    'unknowns': [],
                    'needs_human_pattern_review': False,
                },
                'translation_slot_result': {
                    'mode': 'llm_json',
                    'ko_slots': {'actor': 'that adventurer', 'modal': 'may', 'target_scope': 'same actor only'},
                    'slot_issues': [],
                },
            },
            'rules-lawyer': {
                'issues': [],
                'issue_review': [
                    {
                        'issue_id': 'CARD_001-REG_TARGET_SCOPE_BROADENED',
                        'llm_verdict': 'false_positive_candidate',
                        'confidence': 0.91,
                        'evidence': 'LLM review says current_ko preserves the same acting adventurer in context.',
                        'recommended_action': 'downgrade_to_human_review',
                        'requires_human_approval': True,
                    }
                ],
                'rules_lawyer_result': {
                    'risk': 'Low',
                    'scope_checks': [],
                    'modal_checks': [],
                },
            },
            'korean-editor': {
                'translation_comparison': {
                    'status': 'compared',
                    'winner': 'current_ko',
                    'candidate_decision': 'tie_keep_current',
                    'meaning_delta': 'none_detected_by_llm',
                    'rule_delta': 'none_detected_by_llm',
                    'style_delta': 'none',
                    'safe_to_apply': False,
                    'requires_human_review': True,
                },
                'im_not_ai_result': {
                    'status': 'not_selected',
                    'meaning_preserved': True,
                    'meaning_structure_preserved': True,
                    'register_preserved': True,
                    'candidate_decision': 'tie_keep_current',
                    'requires_human_review': True,
                    'checks': [],
                },
            },
            'verifier': {
                'self_verification_patch': {
                    'llm_semantic_review': 'The only blocker is disputed by LLM and requires human approval.',
                    'needs_human_review': True,
                }
            },
            'qa-reviewer': {
                'qa_reviewer_patch': {
                    'llm_review_summary': 'Do not auto-pass; route disputed blocker to human review.',
                    'needs_human_review': True,
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
        issue = next(i for i in qa['issues'] if i['issue_id'] == 'CARD_001-REG_TARGET_SCOPE_BROADENED')
        self.assertTrue(issue.get('llm_disputed'))
        self.assertEqual(issue.get('review_status'), 'llm_disputed_false_positive_candidate')
        self.assertEqual(qa['verdict'], 'Human review')
        self.assertTrue(qa['requires_human_review'])
        self.assertEqual(qa['qa_reviewer_result']['final_decision_basis'], 'llm_disputed_blocker_human_review')
        self.assertIn('CARD_001-REG_TARGET_SCOPE_BROADENED', qa['qa_reviewer_result']['llm_disputed_blocking_issue_ids'])
        self.assertTrue(any(p.get('route') == 'false_positive_review' for p in qa['learning_update_proposal']))

    def test_weak_evidence_llm_blocker_is_candidate_not_final_blocker(self):
        run_id = 'TEST_WEAK_EVIDENCE_ADJUDICATION'
        out = RUNS_ROOT / run_id
        if out.exists():
            shutil.rmtree(out)
        fake = {
            'source-meaning-checker': {
                'source_analysis': {
                    'mode': 'llm_json',
                    'semantic_pattern': 'OPTIONAL_RECOVERY',
                    'source_slots': {'modal': 'may', 'target_scope': 'same actor only'},
                    'confidence': 0.88,
                    'unknowns': [],
                    'needs_human_pattern_review': False,
                },
                'translation_slot_result': {
                    'mode': 'llm_json',
                    'ko_slots': {'modal': 'may', 'target_scope': 'same actor only'},
                    'slot_issues': [],
                },
            },
            'rules-lawyer': {
                'issues': [
                    {
                        'issue_id': 'CARD_001-LLM_WEAK_SCOPE_CLAIM',
                        'issue_type': 'Target scope mismatch',
                        'severity': 'Major',
                        'evidence': 'Suspicious scope wording, but no source/KO spans or IR diff supplied.',
                        'blocks_approval': True,
                    }
                ],
                'rules_lawyer_result': {'risk': 'Medium', 'scope_checks': [], 'modal_checks': []},
                'issue_review': [
                    {
                        'issue_id': 'CARD_001-REG_TARGET_SCOPE_BROADENED',
                        'llm_verdict': 'false_positive_candidate',
                        'confidence': 0.91,
                        'evidence': 'Fixture isolates weak-evidence adjudication by disputing the pre-existing deterministic blocker.',
                        'recommended_action': 'downgrade_to_human_review',
                        'requires_human_approval': True,
                    }
                ],
            },
            'korean-editor': {'translation_comparison': {'status': 'compared', 'winner': 'current_ko', 'candidate_decision': 'tie_keep_current', 'safe_to_apply': False, 'requires_human_review': False}, 'im_not_ai_result': {'status': 'not_selected', 'meaning_structure_preserved': True}},
            'verifier': {'self_verification_patch': {'llm_semantic_review': 'Weak issue evidence must be routed to review, not treated as final blocker.'}},
            'qa-reviewer': {'qa_reviewer_patch': {'llm_review_summary': 'Weak-evidence blocker candidate requires human adjudication.'}},
        }
        env = os.environ.copy()
        env['QA_LLM_ENABLED'] = '1'
        env['QA_LLM_FAKE_RESPONSES_JSON'] = json.dumps(fake, ensure_ascii=False)
        result = subprocess.run([sys.executable, str(RUNNER), '--run-id', run_id, '--input-json', str(TEMPLATE)], cwd=str(PROJECT), text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        qa = json.loads((out / 'output' / 'qa_json' / 'CARD_001.qa.json').read_text(encoding='utf-8'))
        issue = next(i for i in qa['issues'] if i['issue_id'] == 'CARD_001-LLM_WEAK_SCOPE_CLAIM')
        self.assertEqual(issue.get('issue_status'), 'candidate')
        self.assertEqual(issue.get('evidence_quality'), 'weak')
        self.assertEqual(issue.get('review_status'), 'weak_evidence_human_review')
        self.assertEqual(qa['verdict'], 'Human review')
        self.assertEqual(qa['qa_reviewer_result']['final_decision_basis'], 'weak_evidence_blocker_human_review')
        self.assertIn('CARD_001-LLM_WEAK_SCOPE_CLAIM', qa['qa_reviewer_result']['weak_evidence_blocking_issue_ids'])

    def test_semantic_ir_is_first_class_and_llm_resolved_unresolved_is_nonblocking_review(self):
        run_id = 'TEST_SEMANTIC_IR_RESOLVES_UNRESOLVED'
        out = RUNS_ROOT / run_id
        if out.exists():
            shutil.rmtree(out)
        unresolved_input = out / 'input.json'
        out.mkdir(parents=True)
        unresolved_input.write_text(json.dumps([
            {
                'card_id': 'CARD_IR_001',
                'category': {'component_type': 'card', 'card_type': 'test', 'text_type': 'effect_text'},
                'source_text': 'Resolve the effect described by this card.',
                'current_ko': '이 카드에 설명된 효과를 해결합니다.'
            }
        ], ensure_ascii=False), encoding='utf-8')
        fake = {
            'source-meaning-checker': {
                'source_analysis': {
                    'mode': 'llm_json',
                    'semantic_pattern': 'RESOLVE_CARD_EFFECT',
                    'source_slots': {'action': 'resolve', 'target': 'effect described by this card'},
                    'confidence': 0.9,
                    'unknowns': [],
                    'needs_human_pattern_review': False,
                },
                'translation_slot_result': {
                    'mode': 'llm_json',
                    'ko_slots': {'action': 'resolve', 'target': 'effect described by this card'},
                    'slot_issues': [],
                },
            },
            'rules-lawyer': {'issues': [], 'rules_lawyer_result': {'risk': 'Low', 'scope_checks': [], 'modal_checks': []}},
            'korean-editor': {'translation_comparison': {'status': 'compared', 'winner': 'current_ko', 'candidate_decision': 'tie_keep_current', 'safe_to_apply': False, 'requires_human_review': False}, 'im_not_ai_result': {'status': 'not_selected', 'meaning_structure_preserved': True}},
            'verifier': {'self_verification_patch': {'llm_semantic_review': 'LLM IR resolved the previously unknown generic parser result.', 'needs_human_review': True}},
            'qa-reviewer': {'qa_reviewer_patch': {'llm_review_summary': 'Resolved by LLM IR; queue for review, not revision.'}},
        }
        env = os.environ.copy()
        env['QA_LLM_ENABLED'] = '1'
        env['QA_LLM_FAKE_RESPONSES_JSON'] = json.dumps(fake, ensure_ascii=False)
        result = subprocess.run([sys.executable, str(RUNNER), '--run-id', run_id, '--input-json', str(unresolved_input)], cwd=str(PROJECT), text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        qa = json.loads((out / 'output' / 'qa_json' / 'CARD_IR_001.qa.json').read_text(encoding='utf-8'))
        self.assertEqual(qa['semantic_ir']['status'], 'llm_resolved')
        self.assertEqual(qa['semantic_ir']['source_ir']['action'], 'resolve')
        unresolved = [i for i in qa['issues'] if 'UNRESOLVED' in i.get('issue_id', '') or i.get('issue_type') == 'Unresolved semantic pattern']
        self.assertTrue(unresolved, qa['issues'])
        self.assertTrue(all(i.get('review_status') == 'llm_resolved_unresolved_human_review' for i in unresolved))
        self.assertTrue(all(i.get('blocks_approval') is False for i in unresolved))
        self.assertEqual(qa['verdict'], 'Human review')
        self.assertEqual(qa['qa_reviewer_result']['final_decision_basis'], 'llm_resolved_unresolved_human_review')

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
