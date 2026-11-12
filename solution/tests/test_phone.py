import unittest
from solution.normalizers import normalize_phone
from solution.crypto import mask_phone


class TestPhoneNormalizer(unittest.TestCase):
    def test_valid_indian_phones(self):
        valid_cases = [
            ("9876543210", "+919876543210"),
            ("6123456789", "+916123456789"),
            ("7987654321", "+917987654321"),
            ("8888888888", "+918888888888"),
            ("+91 98765 43210", "+919876543210"),
            ("09876543210", "+919876543210"),
            ("91-98765-43210", "+919876543210"),
            ("(+91) 9876543210", "+919876543210"),
            ("+919876543210", "+919876543210"),
        ]
        for raw, expected in valid_cases:
            with self.subTest(raw=raw):
                is_valid, canon = normalize_phone(raw)
                self.assertTrue(is_valid)
                self.assertEqual(canon, expected)

    def test_invalid_indian_phones(self):
        invalid_cases = [
            "987654321",        # 9 digits
            "98765432101",      # 11 digits without 0
            "5876543210",       # Starts with 5
            "022-2555-1234",    # Landline starting with 022 -> 2225551234 (starts with 2)
            "+1 415 555 0134",  # US number
            "N/A",
            "",
            "   ",
            None,
        ]
        for raw in invalid_cases:
            with self.subTest(raw=raw):
                is_valid, canon = normalize_phone(raw)
                self.assertFalse(is_valid)
                self.assertIsNone(canon)

    def test_phone_masking(self):
        self.assertEqual(mask_phone("+919876543210"), "+91******3210")
        self.assertEqual(mask_phone("+917718750036"), "+91******0036")


if __name__ == "__main__":
    unittest.main()
