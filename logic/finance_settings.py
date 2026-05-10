# -*- coding: utf-8 -*-
"""قراءة/كتابة إعدادات المركز المالي مع التشفير للأسرار."""
from __future__ import annotations

from typing import Any, Dict

from logic import finance_constants as FC
from logic.finance_crypto import decrypt_secret, encrypt_secret


def _ek(app_secret: str, raw: Any) -> str:
    s = raw if isinstance(raw, str) else ""
    return encrypt_secret(s, app_secret) if s.strip() else ""


def vault_configured(db) -> bool:
    return str(db.get_system_setting(FC.KEY_SETUP_COMPLETE) or "").strip() == "1"


def save_vault_credentials(
    db,
    *,
    app_secret: str,
    amazon_base_url: str,
    amazon_api_key: str,
    amazon_secret: str,
    ai_provider: str,
    ai_model: str,
    ai_api_key: str,
) -> None:
    db.set_system_setting(FC.KEY_AMAZON_BASE_CIPHER, _ek(app_secret, amazon_base_url))
    db.set_system_setting(FC.KEY_AMAZON_API_KEY_CIPHER, _ek(app_secret, amazon_api_key))
    db.set_system_setting(FC.KEY_AMAZON_SECRET_CIPHER, _ek(app_secret, amazon_secret))
    db.set_system_setting(FC.KEY_AI_PROVIDER, (ai_provider or "openai").strip().lower()[:32])
    db.set_system_setting(FC.KEY_AI_MODEL, (ai_model or "").strip()[:160])
    db.set_system_setting(FC.KEY_AI_API_KEY_CIPHER, _ek(app_secret, ai_api_key))


def load_amazon_credentials(db, app_secret: str) -> Dict[str, str]:
    return {
        "base_url": decrypt_secret(db.get_system_setting(FC.KEY_AMAZON_BASE_CIPHER), app_secret),
        "api_key": decrypt_secret(db.get_system_setting(FC.KEY_AMAZON_API_KEY_CIPHER), app_secret),
        "secret": decrypt_secret(db.get_system_setting(FC.KEY_AMAZON_SECRET_CIPHER), app_secret),
    }


def load_ai_credentials(db, app_secret: str) -> Dict[str, str]:
    return {
        "provider": (db.get_system_setting(FC.KEY_AI_PROVIDER) or "openai").strip(),
        "model": (db.get_system_setting(FC.KEY_AI_MODEL) or "").strip(),
        "api_key": decrypt_secret(db.get_system_setting(FC.KEY_AI_API_KEY_CIPHER), app_secret),
    }


def save_recovery_email_encrypted(db, app_secret: str, email_plain: str) -> None:
    db.set_system_setting(
        FC.KEY_RECOVERY_EMAIL_CIPHER,
        encrypt_secret((email_plain or "").strip(), app_secret),
    )


def load_recovery_email(db, app_secret: str) -> str:
    return decrypt_secret(db.get_system_setting(FC.KEY_RECOVERY_EMAIL_CIPHER), app_secret)
