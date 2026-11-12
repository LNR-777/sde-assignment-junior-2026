from __future__ import annotations

import base64
import hashlib
import hmac
import os
from solution.config import PII_SECRET_KEY


def hash_phone(canonical_phone: str, key: bytes = PII_SECRET_KEY) -> str:
    """HMAC-SHA256 blind index for phone lookup without exposing plaintext."""
    return hmac.new(key, canonical_phone.encode("utf-8"), hashlib.sha256).hexdigest()


def mask_phone(canonical_phone: str) -> str:
    """Masks phone to '+91******' followed by the last 4 digits."""
    cleaned = canonical_phone.strip()
    digits = "".join(c for c in cleaned if c.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else digits
    return f"+91******{last4}"


def encrypt_pii(plaintext: str, key: bytes = PII_SECRET_KEY) -> str:
    """
    Symmetric encryption using HMAC-SHA256 in counter mode with integrity tag.
    Format: base64(nonce_16 || tag_32 || ciphertext)
    """
    if not plaintext:
        return ""
    data = plaintext.encode("utf-8")
    nonce = os.urandom(16)

    enc_key = hmac.new(key, b"enc_key", hashlib.sha256).digest()
    mac_key = hmac.new(key, b"mac_key", hashlib.sha256).digest()

    keystream = bytearray()
    counter = 0
    while len(keystream) < len(data):
        block = hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        keystream.extend(block)
        counter += 1

    ciphertext = bytes(a ^ b for a, b in zip(data, keystream[: len(data)]))
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return base64.b64encode(nonce + tag + ciphertext).decode("ascii")


def decrypt_pii(token: str, key: bytes = PII_SECRET_KEY) -> str:
    """Decrypts token and validates HMAC tag."""
    if not token:
        return ""
    raw = base64.b64decode(token.encode("ascii"))
    if len(raw) < 48:
        raise ValueError("Invalid encrypted payload length")
    nonce = raw[:16]
    tag = raw[16:48]
    ciphertext = raw[48:]

    enc_key = hmac.new(key, b"enc_key", hashlib.sha256).digest()
    mac_key = hmac.new(key, b"mac_key", hashlib.sha256).digest()

    expected_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Decryption failed: integrity check mismatch")

    keystream = bytearray()
    counter = 0
    while len(keystream) < len(ciphertext):
        block = hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        keystream.extend(block)
        counter += 1

    plaintext = bytes(a ^ b for a, b in zip(ciphertext, keystream[: len(ciphertext)]))
    return plaintext.decode("utf-8")
