# -*- coding: utf-8 -*-
"""
قواعد نصية للشات: السلام، الاسم، طلبات مباشرة — بدون قاعدة بيانات.
"""
from __future__ import annotations

import random
import re
from typing import Optional

from logic import keywords as kw

# رد السلام — منفصلان في الواجهة عبر message + followup_message
SALAM_REPLY_FIRST = "وعليكم السلام ورحمة الله وبركاته"
SALAM_REPLY_SECOND = "حياك الله، بوش تامرني؟"
NAME_PROMPT_AFTER_SERVICE = "إذا حاب، وش تحب نناديك؟"


def build_logged_in_islamic_salam_reply(name: str, *, returning_visitor: bool) -> str:
    """رد سلام موحّد لزائر مسجّل الدخول بالبريد/الجوال (اسم من الجلسة)."""
    nm = (name or "").strip()
    if returning_visitor:
        return f"نورتنا من جديد يا {nm}، بوش تامرني؟"
    return (
        f"وعليكم السلام ورحمة الله وبركاته، حياك الله يا {nm}، بوش تامرني؟"
    )


def build_logged_in_casual_greeting_reply(name: str, *, returning_visitor: bool) -> str:
    """تحية عامة (هلا، مرحبا، …) لزائر مسجّل الدخول دون سطر السلام الإسلامي."""
    nm = (name or "").strip()
    if returning_visitor:
        return f"نورتنا من جديد يا {nm}، بوش تامرني؟"
    return f"حياك الله يا {nm}، بوش تامرني؟"


_SMALL_TALK_REPLIES = (
    "بخير والحمد لله، وأنت كيف حالك؟ بوش تامرني؟",
    "الحمد لله بخير، وش أخبارك؟ بوش تامرني؟",
    "تمام والحمد لله — ونسأل عنك؟ تفضل، وش تحتاج؟",
)


def pick_small_talk_reply() -> str:
    return random.choice(_SMALL_TALK_REPLIES)


def build_personalized_salam_followup(display_name: str, *, prior_salam_count: int) -> str:
    """
    متابعة بعد السلام عند وجود اسم حقيقي في الجلسة.
    prior_salam_count: عدد مرات سابقة حصل فيها المستخدم على متابعة سلام مُسماة في هذه الجلسة.
    """
    dn = (display_name or "").strip()
    if not dn or dn in ("أخوي", "حضرتك"):
        return SALAM_REPLY_SECOND
    if prior_salam_count <= 0:
        return f"حياك الله يا {dn}"
    return f"نورتنا من جديد يا {dn}"

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
        "أبغي",
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
# كلمات/جُزُئيات تدل على منتج أو وصف تسوق — لا تُحفَظ كاسم شخص
_PRODUCT_OR_SHOPPING_SUBSTRINGS = (
    "تشيرت",
    "تيشيرت",
    "بنطلون",
    "فستان",
    "فساتين",
    "شنطة",
    "شنط",
    "حقيبة",
    "حقائب",
    "حذاء",
    "جزمة",
    "جزم",
    "عباية",
    "عبايات",
    "ملابس",
    "مقاس",
    "لون",
    "سواريه",
    "سهرة",
    "رجالي",
    "نسائي",
    "اطفال",
    "أطفال",
    "كعب",
    "كاجوال",
    "زواج",
    "زفاف",
    "اكسسوار",
    "اكسسوارات",
)

# أفعال/صيغ طلب — لا تُحفظ كاسم شخص حتى لو كانت كلمتين
_REQUEST_VERB_MARKERS = frozenset(
    {
        "ابغى",
        "ابغي",
        "أبغى",
        "أبغي",
        "أبي",
        "ابي",
        "أريد",
        "اريد",
        "عندكم",
        "عندك",
        "دور",
        "دوري",
        "فين",
        "وين",
        "عرض",
        "بكم",
        "كم",
    }
)

# مدن/فروع شائعة في النص — ليست أسماء أشخاص
_BRANCH_OR_CITY_FRAGMENTS = (
    "مكة",
    "مكه",
    "جدة",
    "جده",
    "المدينة",
    "المدينه",
    "خميس",
    "مشيط",
    "قلوة",
    "قلوه",
    "الرياض",
    "الدمام",
    "فرع",
    "فروع",
    "موقع",
    "عنوان",
    "خرائط",
)

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


def is_small_talk_wellbeing_message(message: str) -> bool:
    """سؤال عن الحال أو تهامل قصير — رد محلي فقط."""
    raw = (message or "").strip()
    if not raw or len(raw) > 120:
        return False
    tl = raw.lower().strip()
    if tl in ("hi", "hello", "hey"):
        return True
    t = (
        raw.replace("ٱ", "ا")
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
    )
    for frag in _PRODUCT_OR_SHOPPING_SUBSTRINGS:
        if frag in t:
            return False
    for frag in _BRANCH_OR_CITY_FRAGMENTS:
        if frag in t:
            return False
    for phrase in kw.SMALL_TALK_WELLBEING_PHRASES:
        if phrase in t:
            return True
    return False


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
    اسم مقبول: كلمة أو كلمتان فقط، بدون طلب/منتج/مدينة/فرع.
    """
    s = (text or "").strip()
    if len(s) < 2 or len(s) > 36:
        return False
    words = _normalize_tokens(s)
    if not words or len(words) > 2:
        return False
    sn = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")
    for frag in _BRANCH_OR_CITY_FRAGMENTS:
        if frag in sn or frag in s:
            return False
    if "فرع" in s or "موقع" in s:
        return False
    for w in words:
        wl = w.lower()
        if wl in INQUIRY_TOKENS or w in INQUIRY_TOKENS:
            return False
        if w in _REQUEST_VERB_MARKERS or wl in _REQUEST_VERB_MARKERS:
            return False
    # أرقام فقط
    if all(c.isdigit() for c in s.replace(" ", "")):
        return False
    return True


def is_acceptable_display_name(text: str) -> bool:
    """
    فلتر قبل حفظ الاسم في الجلسة: ليس طلب منتج ولا وصف تسوق ولا يحتوي أرقاماً.
    """
    if not is_plausible_person_name(text):
        return False
    s = (text or "").strip()
    if re.search(r"\d", s):
        return False
    sn = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")
    for frag in _PRODUCT_OR_SHOPPING_SUBSTRINGS:
        if frag in sn or frag in s:
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
