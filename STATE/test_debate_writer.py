import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from debate_writer import build_debate_json_from_state, validate_debate_payload


class DebateWriterTests(unittest.TestCase):
    def test_generated_payload_passes_validation(self):
        payload = build_debate_json_from_state({}, sequence=123)
        self.assertEqual([], validate_debate_payload(payload))

    def test_validation_reports_missing_sections(self):
        errors = validate_debate_payload({"meta": {}})
        self.assertTrue(any("'agents'" in err for err in errors))
        self.assertTrue(any("'context'" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
