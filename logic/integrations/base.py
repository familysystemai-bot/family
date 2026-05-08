# -*- coding: utf-8 -*-
"""
base — أساسيات نظام التكاملات.

يوفّر:
    - IntegrationResult: نتيجة موحّدة لكل العمليات
    - read_setting() / write_setting(): قراءة/كتابة الإعدادات من DB
    - get_secret(): واجهة موحّدة (تستخدم system_settings الآن، Secrets Vault لاحقاً)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class IntegrationResult:
    """نتيجة موحّدة لكل عمليات التكامل."""
    success: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    provider: str = ""
    raw_response: Any = None

    def __bool__(self) -> bool:
        return self.success


def read_setting(key: str, default: str = "") -> str:
    """
    يقرأ إعداداً من system_settings (لاحقاً من Secrets Vault للمفاتيح الحساسة).

    الترتيب:
        1. system_settings في DB
        2. متغيرات البيئة (UPPERCASE)
        3. القيمة الافتراضية
    """
    try:
        from logic.db_adapter import DBAdapter
        db = DBAdapter()
        row = db.fetch_one(
            "SELECT value FROM system_settings WHERE key = %s",
            (key,),
        )
        if row and (row.get("value") or "").strip():
            return str(row["value"]).strip()
    except Exception:
        pass
    val = (os.environ.get(key.upper()) or "").strip()
    return val or default


def write_setting(key: str, value: str) -> bool:
    """يكتب إعداداً في system_settings (يستخدمه UI لوحة المؤسس)."""
    try:
        from logic.db_adapter import DBAdapter
        db = DBAdapter()
        if db.db_type == "postgres":
            db.execute(
                """
                INSERT INTO system_settings (key, value) VALUES (%s, %s)
                ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
                """,
                (key, value),
            )
        else:
            existing = db.fetch_one("SELECT id FROM system_settings WHERE key = %s", (key,))
            if existing:
                db.execute("UPDATE system_settings SET value = %s WHERE key = %s", (value, key))
            else:
                db.execute("INSERT INTO system_settings (key, value) VALUES (%s, %s)", (key, value))
        return True
    except Exception:
        logger.exception("write_setting failed for key=%s", key)
        return False


def get_secret(category: str, key: str, default: str = "") -> str:
    """
    واجهة موحّدة لقراءة المفاتيح الحساسة.

    مثال:
        get_secret("payment", "moyasar_secret_key")
        → يقرأ من system_settings باسم: payment_moyasar_secret_key

    في الدفعة 5، ستُحوّل هذه الدالة لتقرأ من Secrets Vault المشفّر.
    """
    full_key = f"{category}_{key}"
    return read_setting(full_key, default)


def set_secret(category: str, key: str, value: str) -> bool:
    """نظير get_secret للكتابة."""
    full_key = f"{category}_{key}"
    return write_setting(full_key, value)