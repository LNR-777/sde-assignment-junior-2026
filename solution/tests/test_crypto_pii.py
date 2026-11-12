import unittest
from solution.crypto import decrypt_pii, encrypt_pii, hash_phone, mask_phone


class TestCryptoPII(unittest.TestCase):
    def test_pii_encryption_roundtrip(self):
        names = ["Aarav Sharma", "Priya Nair", "Mohammed Khan", "Lakshmi Srinivasan"]
        for name in names:
            with self.subTest(name=name):
                enc = encrypt_pii(name)
                self.assertNotEqual(enc, name)
                self.assertGreater(len(enc), 40)
                dec = decrypt_pii(enc)
                self.assertEqual(dec, name)

    def test_tamper_detection(self):
        enc = encrypt_pii("Sensitive Name")
        tampered = enc[:-4] + "AAAA"
        with self.assertRaises(Exception):
            decrypt_pii(tampered)

    def test_phone_hashing_blind_index(self):
        phone1 = "+919876543210"
        phone2 = "+919876543211"
        h1 = hash_phone(phone1)
        h2 = hash_phone(phone2)
        self.assertNotEqual(h1, phone1)
        self.assertNotEqual(h1, h2)
        # Deterministic
        self.assertEqual(h1, hash_phone(phone1))


if __name__ == "__main__":
    unittest.main()
