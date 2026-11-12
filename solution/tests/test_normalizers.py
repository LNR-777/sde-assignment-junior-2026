import unittest
from datetime import datetime
from solution.config import IST
from solution.normalizers import (
    normalize_decimal,
    normalize_enum,
    normalize_header,
    parse_event_timestamp,
)


class TestNormalizers(unittest.TestCase):
    def test_normalize_header(self):
        self.assertEqual(normalize_header("  Mobile_No  "), "mobile no")
        self.assertEqual(normalize_header("Insured Name"), "insured name")
        self.assertEqual(normalize_header("order___date"), "order date")
        self.assertEqual(normalize_header("PREMIUM"), "premium")

    def test_normalize_decimal(self):
        valid_cases = [
            ("₹27,554", 27554.0),
            ("Rs. 31629", 31629.0),
            ("INR 25301.4", 25301.4),
            ("4985.75", 4985.75),
            ("0", 0.0),
            ("1,234,567.89", 1234567.89),
        ]
        for raw, expected in valid_cases:
            with self.subTest(raw=raw):
                is_valid, parsed = normalize_decimal(raw)
                self.assertTrue(is_valid)
                self.assertEqual(parsed, expected)

        invalid_cases = ["-100", "N/A", "-", "twelve thousand", ""]
        for raw in invalid_cases:
            with self.subTest(raw=raw):
                is_valid, parsed = normalize_decimal(raw)
                self.assertFalse(is_valid)

    def test_normalize_enum(self):
        allowed = ["en", "hi", "ta", "kn", "te", "mr"]
        self.assertEqual(normalize_enum("hi", allowed), (True, "hi"))
        self.assertEqual(normalize_enum("HI", allowed), (True, "hi"))
        self.assertEqual(normalize_enum(" EN ", allowed), (True, "en"))
        self.assertEqual(normalize_enum("Hindi", allowed), (False, None))
        self.assertEqual(normalize_enum("english", allowed), (False, None))

    def test_parse_event_timestamp(self):
        # UTC with Z
        dt_utc = parse_event_timestamp("2026-10-01T05:26:10Z")
        self.assertEqual(dt_utc.tzinfo, IST)
        self.assertEqual(dt_utc.strftime("%Y-%m-%dT%H:%M:%S+05:30"), "2026-10-01T10:56:10+05:30")

        # Offset +05:30
        dt_offset = parse_event_timestamp("2026-10-01T10:40:19+05:30")
        self.assertEqual(dt_offset.strftime("%Y-%m-%dT%H:%M:%S+05:30"), "2026-10-01T10:40:19+05:30")

        # Naive space format -> naive means IST per spec
        dt_naive = parse_event_timestamp("2026-10-01 09:15:55")
        self.assertEqual(dt_naive.strftime("%Y-%m-%dT%H:%M:%S+05:30"), "2026-10-01T09:15:55+05:30")


if __name__ == "__main__":
    unittest.main()
