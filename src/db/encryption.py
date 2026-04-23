"""AES-256-GCM encryption TypeDecorator for SQLAlchemy.

Used to encrypt sensitive string columns (e.g. video_storage_uri) at the
application layer before writing to PostgreSQL.

Encryption key is loaded from the ENCRYPTION_KEY environment variable
(32-byte hex-encoded string, i.e. 64 hex characters).

Usage:
    from src.db.encryption import EncryptedString

    class MyModel(Base):
        sensitive_field: Mapped[str] = mapped_column(EncryptedString(1000))

Notes:
    - Requires 'cryptography' package: pip install cryptography
    - If ENCRYPTION_KEY is not set, values are stored as plaintext (dev mode).
    - The stored format is base64(nonce[12] + ciphertext + tag[16]).
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from sqlalchemy import String, TypeDecorator

logger = logging.getLogger(__name__)

_ENCRYPTION_KEY_ENV = "ENCRYPTION_KEY"
_NONCE_SIZE = 12
_TAG_SIZE = 16
_KEY_SIZE = 32  # AES-256


def _get_key() -> Optional[bytes]:
    """Load the 32-byte AES key from environment variable.

    Returns None if not configured (dev/test mode — no encryption applied).
    """
    hex_key = os.environ.get(_ENCRYPTION_KEY_ENV)
    if not hex_key:
        return None
    try:
        key = bytes.fromhex(hex_key)
        if len(key) != _KEY_SIZE:
            logger.warning(
                "ENCRYPTION_KEY must be 64 hex chars (32 bytes); "
                "got %d bytes — encryption disabled",
                len(key),
            )
            return None
        return key
    except ValueError:
        logger.warning("ENCRYPTION_KEY is not valid hex — encryption disabled")
        return None


def _encrypt(plaintext: str) -> str:
    """Encrypt plaintext using AES-256-GCM.

    Returns base64-encoded string: base64(nonce + ciphertext + tag).
    Falls back to plaintext if no key is configured.
    """
    key = _get_key()
    if key is None:
        return plaintext  # no-op in dev mode

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = AESGCM(key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        combined = nonce + ciphertext_with_tag
        return base64.b64encode(combined).decode("ascii")
    except ImportError:
        logger.warning("cryptography package not installed — storing URI as plaintext")
        return plaintext
    except Exception as exc:
        logger.error("Encryption failed: %s — storing as plaintext", exc)
        return plaintext


def _decrypt(stored: str) -> str:
    """Decrypt an AES-256-GCM ciphertext.

    Accepts base64-encoded string or raw plaintext (legacy / dev mode).
    Falls back gracefully to returning the stored value as-is.
    """
    key = _get_key()
    if key is None:
        return stored  # dev mode, no-op

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        combined = base64.b64decode(stored)
        nonce = combined[:_NONCE_SIZE]
        ciphertext_with_tag = combined[_NONCE_SIZE:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        return plaintext.decode("utf-8")
    except ImportError:
        return stored
    except Exception:
        # Gracefully handle unencrypted legacy values
        return stored


class EncryptedString(TypeDecorator):
    """SQLAlchemy TypeDecorator that transparently encrypts/decrypts a string column.

    The underlying DB column type is String(length).
    When ENCRYPTION_KEY is set, values are AES-256-GCM encrypted.
    When not set (dev/test), values are stored as-is.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:  # type: ignore[override]
        if value is None:
            return None
        return _encrypt(value)

    def process_result_value(self, value: Optional[str], dialect) -> Optional[str]:  # type: ignore[override]
        if value is None:
            return None
        return _decrypt(value)
