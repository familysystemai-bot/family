# -*- coding: utf-8 -*-
"""
تشفير AES-256-GCM لمفاتيح الربط والبريد الاحتياطي (الخزنة المالية).
يقرأ المفتاح من SECRET_KEY + بادئة ثابتة — لا يُخزَّن المفتاح في DB.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def derive_finance_aes_key(secret_key: str) -> bytes:
    """يطلق مفتاح 32 بايت لتشغيل AES-256."""
    seed = ("ALMANAKH_FINANCE_AES256|" + (secret_key or "")).encode("utf-8")
    return hashlib.sha256(seed).digest()


def encrypt_secret(plaintext: str, secret_key: str) -> str:
    """نصاً عادياً → base64(urlsafe) لـ nonce|ciphertext/tag."""
    pt = plaintext or ""
    key = derive_finance_aes_key(secret_key)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aes = AESGCM(key)
        nonce = os.urandom(12)
        ct = aes.encrypt(nonce, pt.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    except ImportError:
        logger.warning(
            "cryptography غير متاح — لا يمكن تشفير الخزنة. ثبّت: pip install cryptography"
        )
        raise RuntimeError(
            "مكتبة cryptography مطلوبة لتشغيل الخزنة المالية (AES-256-GCM)."
        ) from None


def decrypt_secret(ciphertext_b64: Optional[str], secret_key: str) -> str:
    """يعيد النص الواضح أو سلسلة فارغة إن فشل/فارغ."""
    raw = (ciphertext_b64 or "").strip()
    if not raw:
        return ""
    key = derive_finance_aes_key(secret_key)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        bundle = base64.urlsafe_b64decode(raw.encode("ascii"))
        if len(bundle) < 13:
            return ""
        nonce, ct = bundle[:12], bundle[12:]
        aes = AESGCM(key)
        out = aes.decrypt(nonce, ct, None)
        return out.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("decrypt_secret failed: %s", e)
        return ""
