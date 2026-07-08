#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
QA_ROOT = PROJECT / 'qa'
SCRIPT = QA_ROOT / 'scripts' / 'build_rulebook_index.py'


class RulebookPreflightIndexTests(unittest.TestCase):
    def test_builds_rule_bank_from_text_fixture_with_meaning_units_and_sqlite_fts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'mini_rulebook.txt'
            source.write_text(
                '''Game Overview\n'''
                '''Each adventurer has a health, stamina, and magicka stat.\n'''
                '''\n'''
                '''Battle Rounds\n'''
                '''During a battle round, each adventurer must take one turn before enemies activate.\n'''
                '''After each time an adventurer defeats an enemy, that adventurer may recover 1 health.\n'''
                '''Regardless of location, effects that target each adventurer affect all adventurers.\n'''
                '''\n'''
                '''Line of Sight\n'''
                '''An adventurer has sight to a target if a straight line can be traced without crossing an impassable edge.\n''',
                encoding='utf-8',
            )
            out = root / 'rule_index'
            result = subprocess.run(
                [sys.executable, str(SCRIPT), '--source', str(source), '--out-dir', str(out)],
                cwd=PROJECT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            bank_jsonl = out / 'rule_bank.jsonl'
            bank_db = out / 'rule_bank.sqlite'
            report = out / 'rulebook_preflight_report.md'
            self.assertTrue(bank_jsonl.exists(), 'rule_bank.jsonl should be produced by preflight')
            self.assertTrue(bank_db.exists(), 'rule_bank.sqlite should be produced by preflight')
            self.assertTrue(report.exists(), 'preflight report should be produced')

            rows = [json.loads(line) for line in bank_jsonl.read_text(encoding='utf-8').splitlines() if line.strip()]
            self.assertGreaterEqual(len(rows), 5)
            target_row = next(row for row in rows if 'that adventurer may recover' in row['raw_text'])
            self.assertEqual(target_row['section'], 'Battle Rounds')
            self.assertIn('that adventurer', target_row['triggers'])
            self.assertIn('target_scope', target_row['rule_axes'])
            self.assertIn('may', target_row['modality'])
            self.assertIn('defeats', target_row['timing'])
            self.assertTrue(target_row['summary'])

            con = sqlite3.connect(bank_db)
            con.row_factory = sqlite3.Row
            hits = con.execute(
                "SELECT rule_id, section, raw_text FROM rule_units_fts WHERE rule_units_fts MATCH ? LIMIT 5",
                ('"that adventurer"',),
            ).fetchall()
            con.close()
            self.assertTrue(any('that adventurer may recover' in hit['raw_text'] for hit in hits), hits)

            report_text = report.read_text(encoding='utf-8')
            self.assertIn('rule units', report_text)
            self.assertIn('Battle Rounds', report_text)
            self.assertIn('source_format: text', report_text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
