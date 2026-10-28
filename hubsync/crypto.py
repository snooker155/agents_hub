"""
End-to-end encryption of synced records: what only the person's key opens.

The remote hub stores what a client pushes; with encryption on, it stores an
envelope it cannot read. The same envelope is opened in two places: a hub of
the person's own (Python, this module) and a browser on the remote's site
(WebCrypto, dashboard/frontend/src/lib/syncCrypto.js), so every primitive here
is one WebCrypto has natively and nothing more:

* **The key** is derived from a passphrase with PBKDF2-HMAC-SHA256
  (:data:`ITERATIONS`, a random 16 byte salt). Argon2 or scrypt would be
  stronger against guessing, but no browser offers them without shipping a
  WASM build, and the point of viewing data on the remote is that the key
  never leaves the browser. A generated passphrase (:func:`generate_passphrase`,
  130 random bits) makes guessing moot; one a person types is only as good
  as they make it.
* **A record** is AES-256-GCM with a random 96 bit nonce over the gzipped
  JSON document. The additional data binds the ciphertext to its space, kind
  and id, so a server that swaps two envelopes, or replays one space's
  envelope in another space sealed with the same key, gets a failed tag, not
  the wrong record shown under the right name.
* **The verifier** is a known constant sealed with the key. It is stored on
  the remote beside the KDF parameters, so a second device or a browser can
  say "wrong passphrase" before trying a single record.
* **The key id** is a hash of the key under its own label: it tells which
  key sealed a record without telling anything about the key.

The format is versioned (``v``) so a later scheme can sit beside this one.
Only version 2 is written and read. A version 1 envelope bound a record to
its kind and id but not its space, so opening one would let a server replay
a record of one space in another; hubsync was never released with it, so no
such envelope exists to keep readable, and one that arrives is refused.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import secrets
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

VERSION = 2
ALG = "A256GCM"
KDF = "PBKDF2-SHA256"
#: OWASP's 2023 figure for PBKDF2-HMAC-SHA256. A browser derives once per
#: unlock, so the cost is paid once, not per record.
ITERATIONS = 600_000
VERIFIER_PLAINTEXT = b"agents-hub sync key check v1"
VERIFIER_AAD = "agents-hub/v1|verifier"
_KID_LABEL = b"agents-hub/v1|key-id"
#: Base32 without padding, grouped for reading aloud and typing back.
_PASSPHRASE_BYTES = 17


class CryptoError(Exception):
    """A record or a key that does not open: wrong key, or damaged data."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def new_salt() -> str:
    return _b64(os.urandom(16))


def generate_passphrase() -> str:
    """130 random bits as base32, in groups of five: strong enough that the
    PBKDF2 choice above stops mattering, short enough to write down."""
    raw = base64.b32encode(secrets.token_bytes(_PASSPHRASE_BYTES)).decode("ascii").rstrip("=")
    return "-".join(raw[i:i + 5] for i in range(0, len(raw), 5))


def derive_key(passphrase: str, salt: str, iterations: Optional[int] = None) -> bytes:
    if not passphrase:
        raise CryptoError("empty passphrase")
    rounds = int(iterations) if iterations else ITERATIONS
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_unb64(salt), iterations=rounds)
    return kdf.derive(passphrase.encode("utf-8"))


def key_id(key: bytes) -> str:
    return hashlib.sha256(_KID_LABEL + key).hexdigest()[:16]


def record_aad(kind: str, record_id: str, space: Optional[str] = None) -> str:
    """The additional data a record is sealed under. It names the space: one
    person's key seals every space they sync, so without it an envelope from
    one space would open as a record of another."""
    if space is None:
        raise CryptoError("sealing or opening a record needs its space")
    return f"agents-hub/v2|{space}|{kind}|{record_id}"


def seal(key: bytes, plaintext: bytes, aad: str) -> Dict[str, Any]:
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad.encode("utf-8"))
    return {"v": VERSION, "alg": ALG, "kid": key_id(key), "n": _b64(nonce), "c": _b64(ciphertext)}


def open_(key: bytes, envelope: Dict[str, Any], aad: str) -> bytes:
    if not isinstance(envelope, dict) or envelope.get("v") != VERSION or envelope.get("alg") != ALG:
        raise CryptoError("unknown envelope format")
    try:
        return AESGCM(key).decrypt(_unb64(envelope["n"]), _unb64(envelope["c"]), aad.encode("utf-8"))
    except (InvalidTag, KeyError, ValueError) as exc:
        raise CryptoError("the key does not open this record") from exc


def make_verifier(key: bytes) -> str:
    return json.dumps(seal(key, VERIFIER_PLAINTEXT, VERIFIER_AAD), separators=(",", ":"))


def check_verifier(key: bytes, verifier: str) -> bool:
    try:
        return open_(key, json.loads(verifier), VERIFIER_AAD) == VERIFIER_PLAINTEXT
    except (CryptoError, ValueError, TypeError):
        return False


# ── records ──────────────────────────────────────────────────────────────────
# A record's payload, as stored in sync_records.payload: base64 of gzipped
# JSON when plain; the JSON text of an envelope over the gzipped JSON when
# encrypted. Gzip first, since ciphertext does not compress.

def _pack(doc: Dict[str, Any]) -> bytes:
    raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    # mtime=0 keeps equal documents byte-equal, which the tests rely on.
    return gzip.compress(raw, mtime=0)


def _unpack(data: bytes) -> Dict[str, Any]:
    return json.loads(gzip.decompress(data).decode("utf-8"))


def encode_record(doc: Dict[str, Any], *, kind: str, record_id: str,
                  key: Optional[bytes] = None, space: Optional[str] = None) -> str:
    packed = _pack(doc)
    if key is None:
        return _b64(packed)
    return json.dumps(seal(key, packed, record_aad(kind, record_id, space)), separators=(",", ":"))


def decode_record(payload: str, *, kind: str, record_id: str, encrypted: bool,
                  key: Optional[bytes] = None, space: Optional[str] = None) -> Dict[str, Any]:
    if not encrypted:
        return _unpack(_unb64(payload))
    if key is None:
        raise CryptoError("the record is encrypted and no key is unlocked")
    try:
        envelope = json.loads(payload)
    except ValueError as exc:
        raise CryptoError("damaged envelope") from exc
    return _unpack(open_(key, envelope, record_aad(kind, record_id, space)))


__all__ = ["ALG", "CryptoError", "ITERATIONS", "KDF", "VERSION", "check_verifier", "decode_record",
           "derive_key", "encode_record", "generate_passphrase", "key_id", "make_verifier", "new_salt",
           "open_", "record_aad", "seal"]
