from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
QA_ROOT = ROOT / 'qa'
if str(QA_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_ROOT))


def load_agent(name: str):
    module_name = f'qa.agents.{name}'
    __import__(module_name)
    return sys.modules[module_name]


def base_context(card_id='CARD_X', source='Attack an enemy 3 times.', ko='적 하나를 3번 공격합니다.'):
    return {
        'code': card_id,
        'item_id': card_id,
        'source_text': source,
        'current_ko': ko,
        'facts': {
            'issues': [],
            'source_slots': {},
            'ko_slots': {},
            'semantic_pattern': 'TEST_PATTERN',
        },
        'agent_trace': [],
        'agent_results': {},
    }


class SemanticAdjudicationExpansionTests(unittest.TestCase):
    def test_terminology_classifies_context_proper_noun_vs_general_word(self):
        terminology = load_agent('terminology_manager')
        ctx = base_context(
            source='Apocrypha lets you draw a card and recover health.',
            ko='아포크리파는 카드를 뽑고 체력을 회복하게 합니다.',
        )
        ctx['term_glossary'] = [
            {'en': 'Apocrypha', 'ko': '아포크리파', 'category': 'lore_term', 'status': 'approved'},
            {'en': 'card', 'ko': '카드', 'category': 'common_word', 'status': 'approved'},
        ]
        terminology.run(ctx)
        by_policy = ctx['terminology_result']['source_terms_by_policy']
        self.assertIn('apocrypha', by_policy['proper_noun_terms'])
        self.assertIn('card', by_policy['ordinary_words_ignored'])
        self.assertNotIn('card', by_policy['locked_terms'])
        self.assertEqual(ctx['terminology_result']['term_classification']['apocrypha'], 'proper_noun_or_lore')
        self.assertEqual(ctx['terminology_result']['term_classification']['card'], 'ordinary_word')

    def test_syntax_word_order_difference_is_style_not_semantic_blocker(self):
        syntax = load_agent('syntax_pattern_controller')
        ctx = base_context(
            source='Attack an enemy 3 times.',
            ko='3번 적 하나를 공격합니다.',
        )
        ctx['input_card'] = {
            'syntax_dictionary': [
                {
                    'rule_id': 'ATTACK_N_TIMES_STYLE',
                    'source_pattern': 'Attack an enemy N times',
                    'ko_template': 'OBJ_COUNT_VERB',
                    'status': 'approved',
                    'strength': 'locked',
                    'semantic_role': 'word_order_style',
                }
            ]
        }
        syntax.run(ctx)
        check = next(c for c in ctx['syntax_pattern_result']['checks'] if c.get('rule_id') == 'ATTACK_N_TIMES_STYLE')
        self.assertEqual(check['status'], 'warn')
        self.assertEqual(check['difference_type'], 'style_word_order')
        self.assertTrue(check['meaning_equivalent'])
        self.assertEqual(check['severity'], 'StyleWarning')
        self.assertFalse(any('Syntax Pattern Consistency' in i.get('issue_type', '') for i in ctx['facts']['issues']))

    def test_cross_card_small_sample_dominant_is_review_candidate_not_blocker(self):
        cross = load_agent('cross_card_consistency_checker')
        ctx = base_context(source='Attack an enemy 3 times.', ko='3번 적 하나를 공격합니다.')
        ctx['batch_indexes'] = {
            'cross_card_consistency_index': {
                'syntax_structures': {
                    'Attack an enemy N times': {
                        'dominant_template': 'OBJ_COUNT_VERB',
                        'confidence': 0.8,
                        'total_count': 2,
                        'variants': {'OBJ_COUNT_VERB': ['A'], 'COUNT_OBJ_VERB': ['B']},
                    }
                }
            }
        }
        cross.run(ctx)
        check = next(c for c in ctx['cross_card_consistency']['checks'] if c.get('check_type') == 'syntax_structure_consistency')
        self.assertEqual(check['evidence_strength'], 'weak_observed')
        self.assertEqual(check['candidate_type'], 'review_candidate')
        self.assertFalse(check['blocks_approval'])
        self.assertEqual(check['severity'], 'StyleWarning')

    def test_rules_lawyer_ir_comparison_detects_number_condition_timing(self):
        rules = load_agent('rules_lawyer')
        ctx = base_context(
            source='After you roll a 6, if possible, attack an enemy 3 times.',
            ko='6을 굴리기 전에 적 하나를 2번 공격합니다.',
        )
        ctx['semantic_ir'] = {
            'source_ir': {
                'timing': 'after roll 6',
                'condition': 'if possible',
                'action': 'attack',
                'target': 'enemy',
                'number': '3',
            },
            'ko_ir': {
                'timing': 'before roll 6',
                'condition': 'missing',
                'action': 'attack',
                'target': 'enemy',
                'number': '2',
            },
        }
        rules.run(ctx)
        diffs = {i['semantic_diff']['field']: i for i in ctx['facts']['issues'] if i.get('issue_id', '').startswith('CARD_X-IR_')}
        self.assertIn('timing', diffs)
        self.assertIn('condition', diffs)
        self.assertIn('number', diffs)
        self.assertEqual(diffs['timing']['span_source'], 'after roll 6')
        self.assertEqual(diffs['timing']['span_ko'], 'before roll 6')
        self.assertTrue(all(i.get('blocks_approval') for i in diffs.values()))


if __name__ == '__main__':
    unittest.main()
