"""
Parses Matrix/Element E2E room key export files.

The file format is defined at:
https://spec.matrix.org/v1.12/client-server-api/#key-exports

Binary layout after base64 decoding:
  [1 byte]     version (must be 0x01)
  [16 bytes]   salt S
  [16 bytes]   IV
  [4 bytes]    round count N (big-endian u32)
  [variable]   AES-CTR-256 ciphertext
  [32 bytes]   HMAC-SHA-256

The passphrase is used with PBKDF2-HMAC-SHA-512 to derive a 512-bit key.
  K  = first 256 bits  (AES key)
  K' = last  256 bits  (HMAC key)
"""

import base64
import hashlib
import hmac
import json
import struct

from Crypto.Cipher import AES


HEADER = "-----BEGIN MEGOLM SESSION DATA-----"
FOOTER = "-----END MEGOLM SESSION DATA-----"


def decrypt_key_export(file_path: str, passphrase: str) -> list[dict]:
    """Decrypt an Element key export file and return the list of session dicts.

    Each dict has keys: algorithm, room_id, session_id, session_key,
    sender_key, sender_claimed_keys, forwarding_curve25519_key_chain.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if not text.startswith(HEADER) or not text.endswith(FOOTER):
        raise ValueError("File does not look like a Megolm session export.")

    b64_data = text[len(HEADER):-len(FOOTER)].strip()
    raw = base64.b64decode(b64_data)

    if len(raw) < 1 + 16 + 16 + 4 + 32:
        raise ValueError("Export data is too short.")

    version = raw[0]
    if version != 1:
        raise ValueError(f"Unsupported export version: {version}")

    salt = raw[1:17]
    iv = raw[17:33]
    rounds = struct.unpack(">I", raw[33:37])[0]
    ciphertext = raw[37:-32]
    file_hmac = raw[-32:]

    # Derive 512 bits via PBKDF2-HMAC-SHA-512
    derived = hashlib.pbkdf2_hmac("sha512", passphrase.encode("utf-8"), salt, rounds, dklen=64)
    aes_key = derived[:32]    # K
    hmac_key = derived[32:]   # K'

    # Verify HMAC-SHA-256 over everything except the HMAC itself
    mac = hmac.new(hmac_key, raw[:-32], hashlib.sha256).digest()
    if not hmac.compare_digest(mac, file_hmac):
        raise ValueError("HMAC verification failed – wrong passphrase or corrupted file.")

    # Decrypt with AES-CTR-256.  Bit 63 of the 128-bit IV is set to zero per spec.
    # Bit 63 (counting from LSB=0) is the MSB of byte 8 in big-endian layout.
    iv_bytes = bytearray(iv)
    iv_bytes[8] &= 0x7F  # clear bit 63
    cipher = AES.new(aes_key, AES.MODE_CTR, initial_value=bytes(iv_bytes), nonce=b"")
    plaintext = cipher.decrypt(ciphertext)

    sessions = json.loads(plaintext.decode("utf-8"))
    if not isinstance(sessions, list):
        raise ValueError("Decrypted data is not a JSON array.")
    return sessions


def build_session_map(sessions: list[dict]):
    """Return a dict mapping (room_id, session_id) -> session_key (base64 str)."""
    mapping = {}
    for s in sessions:
        room_id = s.get("room_id")
        session_id = s.get("session_id")
        session_key = s.get("session_key")
        if room_id and session_id and session_key:
            mapping[(room_id, session_id)] = session_key
    return mapping
