#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
RUNNER = PROJECT / 'qa' / 'scripts' / 'run_agent_pipeline.py'
COMPARE = PROJECT / 'qa' / 'scripts' / 'compare_qa_iterations.py'

class PublicPackageSmokeTest(unittest.TestCase):
    def test_input_json_two_pass_and_iteration_review(self):
        runs = PROJECT / 'qa' / 'runs'
        for run_id in ['PUBLIC_SMOKE_PASS1', 'PUBLIC_SMOKE_PASS2']:
            path = runs / run_id
            if path.exists():
                shutil.rmtree(path)
        result1 = subprocess.run([sys.executable, str(RUNNER), '--run-id', 'PUBLIC_SMOKE_PASS1', '--input-json', str(PROJECT / 'templates' / 'sample_cards.json')], cwd=str(PROJECT), text=True, capture_output=True)
        self.assertEqual(result1.returncode, 0, result1.stdout + result1.stderr)
        result2 = subprocess.run([sys.executable, str(RUNNER), '--run-id', 'PUBLIC_SMOKE_PASS2', '--input-json', str(PROJECT / 'templates' / 'sample_cards_after_edit.json')], cwd=str(PROJECT), text=True, capture_output=True)
        self.assertEqual(result2.returncode, 0, result2.stdout + result2.stderr)
        out_dir = runs / 'PUBLIC_SMOKE_PASS2' / 'review'
        result3 = subprocess.run([
            sys.executable, str(COMPARE),
            '--prev-run', str(runs / 'PUBLIC_SMOKE_PASS1'),
            '--current-run', str(runs / 'PUBLIC_SMOKE_PASS2'),
            '--human-feedback', str(PROJECT / 'templates' / 'sample_human_feedback.jsonl'),
            '--out-dir', str(out_dir),
        ], cwd=str(PROJECT), text=True, capture_output=True)
        self.assertEqual(result3.returncode, 0, result3.stdout + result3.stderr)
        summary = json.loads((out_dir / 'iteration_summary.json').read_text(encoding='utf-8'))
        self.assertIn('iteration_status', summary)
        self.assertTrue((runs / 'PUBLIC_SMOKE_PASS1' / 'output' / 'qa_json' / 'CARD_001.qa.json').exists())
        self.assertTrue((out_dir / 'iteration_summary.md').exists())

if __name__ == '__main__':
    unittest.main()
