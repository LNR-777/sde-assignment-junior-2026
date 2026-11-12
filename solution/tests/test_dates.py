import unittest
from solution.normalizers import normalize_date


class TestDateNormalizer(unittest.TestCase):
    def test_valid_date_formats(self):
        cases = [
            ("2026-10-15", "2026-10-15"),       # YYYY-MM-DD
            ("15/10/2026", "2026-10-15"),       # DD/MM/YYYY
            ("01/05/2026", "2026-05-01"),       # Day-first
            ("15-10-2026", "2026-10-15"),       # DD-MM-YYYY
            ("01-Dec-2026", "2026-12-01"),      # DD-Mon-YYYY
            ("01-dec-2026", "2026-12-01"),      # case-insensitive
            ("29-DEC-2026", "2026-12-29"),      # upper case
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                is_valid, parsed = normalize_date(raw)
                self.assertTrue(is_valid)
                self.assertEqual(parsed, expected)

    def test_invalid_and_impossible_dates(self):
        invalid_cases = [
            "31/02/2026",    # Feb 31st
            "2026-13-01",    # Month 13
            "32-10-2026",    # Day 32
            "soon",
            "TBD",
            "N/A",
            "",
            None,
        ]
        for raw in invalid_cases:
            with self.subTest(raw=raw):
                is_valid, parsed = normalize_date(raw)
                self.assertFalse(is_valid)
                self.assertIsNone(parsed)


if __name__ == "__main__":
    unittest.main()
