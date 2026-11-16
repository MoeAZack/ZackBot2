import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ReportCompletenessTests(unittest.TestCase):
    def run_partial(self, extra):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        source = root / 'jobs.json'
        source.write_text(json.dumps([dict(job_id='r6_regime_flip_4h:7:path|cost15',
            strategy='r6_regime_flip_4h', cost_bp=15, scenario='path', order=7,
            n=1, status='COMPLETE_MODELLED_CLOCK')]))
        output = root / 'out'
        result = subprocess.run([sys.executable, 'tools/research/lane2_funding_report.py',
            '--jobs', str(source), '--out', str(output), *extra], capture_output=True, text=True)
        return result, output

    def test_partial_without_order_zero_is_explicit_not_complete(self):
        result, out = self.run_partial(['--allow-partial'])
        self.assertEqual(result.returncode, 0, result.stderr)
        with (out / 'lead_summary.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]['status'], 'PARTIAL_DIAGNOSTIC')
        self.assertEqual(rows[0]['orders'], '1')
        self.assertFalse(json.loads((out / 'matrix_inventory.json').read_text())['complete'])

    def test_default_requires_full_matrix(self):
        result, out = self.run_partial([])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('incomplete matrix', result.stderr)
        self.assertFalse((out / 'lead_summary.csv').exists())


if __name__ == '__main__': unittest.main()
