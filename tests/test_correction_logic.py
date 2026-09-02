import unittest

import pandas as pd

from app import build_error_key, parse_csv_content, prepare_corrections_dataframe


class CorrectionLogicTests(unittest.TestCase):
    def test_build_error_key_handles_missing_values_and_whitespace(self):
        row = pd.Series({"unique_id": 42, "variable": "  age  "})

        key = build_error_key("constraint", row, "unique_id")

        self.assertEqual(key, "constraint_42_age")

    def test_prepare_corrections_dataframe_handles_mixed_timestamp_values(self):
        df = pd.DataFrame(
            {
                "correction_timestamp": ["2024-01-02T00:00:00", 12.5, None, "2024-01-01T00:00:00"],
                "corrected_by": ["alice", "bob", "carol", "dave"],
                "outside_range": [False, True, False, True],
            }
        )

        cleaned = prepare_corrections_dataframe(df)
        sorted_cleaned = cleaned.sort_values("correction_timestamp", ascending=False, kind="mergesort")

        self.assertEqual(cleaned.iloc[0]["corrected_by"], "alice")
        self.assertEqual(cleaned.iloc[-1]["corrected_by"], "dave")
        self.assertEqual(cleaned["outside_range"].dtype, bool)
        self.assertEqual(len(sorted_cleaned), 4)

    def test_parse_csv_content_handles_empty_input(self):
        self.assertIsNone(parse_csv_content(""))
        self.assertIsNone(parse_csv_content("   "))


if __name__ == "__main__":
    unittest.main()
