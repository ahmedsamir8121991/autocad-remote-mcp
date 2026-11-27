"""End-to-end encryption for AutoCAD Remote MCP requests (shared by both PCs).

Every request body and every reply is encrypted with AES-256-GCM. The encryption key is derived from the
password-derived access key, so nothing new has to be set up, and nobody on the network can read the drawing
data. Each request is bound to its signature (timestamp + one-time nonce) and each reply to its request, so
encrypted messages can't be swapped, edited or replayed.
"""

import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENC_HEADER = "x-acad-enc"
ENC_VERSION = "v1"


class DecryptError(Exception):
    """A message that was not encrypted with this pairing's key, or was changed on the way."""


def _cipher(access_key: str) -> AESGCM:
    k = hmac.new(access_key.encode(), b"acadmcp-enc-v1", hashlib.sha256).digest()  # separate key for encryption
    return AESGCM(k)


def request_aad(ts: str, nonce: str) -> bytes:
    return f"req|{ts}|{nonce}".encode()


def reply_aad(nonce: str) -> bytes:
    return f"resp|{nonce}".encode()


def seal(access_key: str, plaintext: bytes, aad: bytes) -> bytes:
    iv = os.urandom(12)
    return iv + _cipher(access_key).encrypt(iv, plaintext, aad)


def open_sealed(access_key: str, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < 12 + 16:
        raise DecryptError("message too short")
    try:
        return _cipher(access_key).decrypt(blob[:12], blob[12:], aad)
    except InvalidTag as e:
        raise DecryptError("message could not be decrypted (wrong key or changed on the way)") from e


def key_check(access_key: str) -> str:
    """A value that confirms a password without being usable as the key (stored instead of the key)."""
    return hmac.new(access_key.encode(), b"acadmcp-check-v1", hashlib.sha256).hexdigest()
