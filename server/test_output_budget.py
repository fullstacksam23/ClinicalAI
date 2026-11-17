"""Offline checks for answer evidence size limits."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server.output import _bounded_json


class AnswerEvidenceBudgetTests(unittest.TestCase):
    def test_large_ocr_text_is_clipped_as_valid_json(self):
        encoded = _bounded_json({"lab_results": [], "text": "clinical data " * 10000}, 1200)

        self.assertLessEqual(len(encoded), 1200)
        parsed = json.loads(encoded)
        self.assertIn("clinical data", parsed["text"])
        self.assertTrue(parsed["text"].endswith("[truncated]"))

    def test_large_result_inventory_stays_within_budget(self):
        evidence = {"lab_results": [{"investigation": f"test-{i}", "value": i} for i in range(1000)]}
        encoded = _bounded_json(evidence, 900)

        self.assertLessEqual(len(encoded), 900)
        parsed = json.loads(encoded)
        self.assertGreater(len(parsed["lab_results"]), 0)
        self.assertLess(len(parsed["lab_results"]), 1000)


if __name__ == "__main__":
    unittest.main()
