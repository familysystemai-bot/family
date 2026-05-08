# -*- coding: utf-8 -*-
"""
مفاتيح واتساب (Cloud API) — مصدر واحد للتوكن ورقم الهاتف التجاري.

يُقرأ من system_settings ثم متغيرات البيئة؛ نفس ترتيب webhook ولوحة الإرسال
حتى لا يختلف المفتاح المحفوظ في قاعدة البيانات عن ما يُستخدَم فعلياً.
"""
from __future__ import annotations

import os


def wa_access_token() -> str:
    try:
        from logic.integrations.base import read_setting

        t = (read_setting("WA_ACCESS_TOKEN", "") or "").strip()
        if t:
            return t
    except Exception:
        pass
    return (os.environ.get("WA_ACCESS_TOKEN", "") or "").strip()


def wa_phone_number_id() -> str:
    try:
        from logic.integrations.base import read_setting

        t = (read_setting("WA_PHONE_NUMBER_ID", "") or "").strip()
        if t:
            return t
    except Exception:
        pass
    return (os.environ.get("WA_PHONE_NUMBER_ID", "") or "").strip()


def meta_graph_token_error_hint(api_error_text: str) -> str:
    """تلميح عربي عند رفض ميتا بسبب التوكن (190 / منتهي)."""
    s = (api_error_text or "")
    low = s.lower()
    if (
        "190" in s
        or "401" in s
        or "oauth" in low
        or "expired" in low
        or ("invalid" in low and "token" in low)
        or ("session has expired" in low)
    ):
        return (
            " — ميتا رفضت الطلب بسبب التوكن: غالباً **انتهت صلاحية WA_ACCESS_TOKEN** أو أُلغي. "
            "أنشئ من Developer Console توكناً صالحاً (يفضّل System User طويل الأمد) "
            "ثم احفظه في **لوحة المؤسس → التكاملات → واتساب** (أو متغير البيئة)."
        )
    return ""
