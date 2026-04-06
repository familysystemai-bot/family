# -*- coding: utf-8 -*-
"""
قواعد نصية للشات: السلام، الاسم، طلبات مباشرة — بدون قاعدة بيانات.
"""
from __future__ import annotations

import re
from typing import Optional

# رد السلام — منفصلان في الواجهة عبر message + followup_message
SALAM_REPLY_FIRST = "وعليكم السلام ورحمة الله وبركاته"
SALAM_REPLY_SECOND = "حياك الله، تفضل كيف أقدر أخدمك؟"
NAME_PROMPT_AFTER_SERVICE = "إذا حاب، وش تحب نناديك؟"

# كلمات تدل على استفسار وليست اسم شخص
INQUIRY_TOKENS = frozenset(
    {
        "فين",
        "وين",
        "موقع",
        "الموقع",
        "عنوان",
        "كم",
        "متى",
        "عندكم",
        "عندك",
        "أبغى",
        "ابغى",
        "ابغي",
        "أبي",
        "ابي",
        "أريد",
        "اريد",
        "وش",
        "ايش",
        "كيف",
        "ليش",
        "هل",
        "ممكن",
        "في",
        "فيه",
        "رد",
        "رقم",
        "جوال",
        "واتس",
        "شكوى",
        "شكوه",
        "مشكلة",
        "تفتحون",
        "تقفلون",
        "دوام",
        "ساعات",
        "فاتحين",
        "مفتوح",
        "السلام",  # داخل جمل أطول
        "عليكم",
    }
)

# بداية طلب مباشر (منتج / موقع / شكوى) يُذكر مع السلام
DIRECT_REQUEST_MARKERS = (
    "ابغى",
    "أبغى",
    "ابغي",
    "عندكم",
    "فين",
    "وين",
    "موقع",
    "شكوى",
    "شكوه",
    "مشكلة",
    "متى",
    "رقم",
    "كم",
    "سعر",
    "فستان",
    "قسم",
)


def _normalize_tokens(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    t = re.sub(r"[؟?!.,،]+", " ", t)
    return [x for x in t.split() if x]


def looks_like_direct_request(text: str) -> bool:
    """هل الرسالة تبدأ بطلب خدمة وليس تحية فقط؟"""
    s = (text or "").strip()
    if not s:
        return False
    sn = s.replace("ٱ", "ا").replace("ى", "ي")
    if any(m in sn for m in DIRECT_REQUEST_MARKERS):
        return True
    return False


def is_plausible_person_name(text: str) -> bool:
    """
    اسم مقبول: كلمة أو كلمتان كحد أقصى، بدون كلمات استفسار.
    """
    s = (text or "").strip()
    if len(s) < 2 or len(s) > 40:
        return False
    words = _normalize_tokens(s)
    if not words or len(words) > 2:
        return False
    low = {w.lower() for w in words}
    for w in words:
        wl = w.lower()
        if wl in INQUIRY_TOKENS or w in INQUIRY_TOKENS:
            return False
    # أرقام فقط
    if all(c.isdigit() for c in s.replace(" ", "")):
        return False
    return True


def is_islamic_salam_message(message: str) -> bool:
    """يُستورد من chat_context للتوافق — تعريف واحد هنا."""
    s = (message or "").strip()
    if not s:
        return False
    sn = s.replace("ٱ", "ا").replace("ى", "ي")
    if "السلام عليكم ورحمة الله وبركاته" in sn:
        return True
    if "السلام عليكم" in sn:
        return True
    if s in ("سلام", "سلام.", "سلام؟", "سلام عليكم"):
        return True
    return False
