# -*- coding: utf-8 -*-
"""
حماية تنبيهات البريد/الفرع من الرسائل قصيرة أو غير الموضوعية.

يُستخدم عند فشل بحث منتج لتجنّب إزعاج المؤسس بإيميلات عند كل رسالة فارغة أو اعتراض.
"""
from __future__ import annotations

import re
from typing import Set

_SIMPLE_OBJECTION_RE = re.compile(
    r"^\s*(لا|لأ|وما ابي|ما ابي|ما أبي|ولا ابي|ولا أبي|لاشكرا|لا شكرا|لاتشكرني|خلاص"
    r"|مشكور|متشكرين|كانسل|مسحته|انسحاب|خلاص لا|طبعاً لا|طبعا لا|اي طبعا لا"
    r"|كله تمام|الحمد لله|طيب خلاص)(\s|$|[،,.])",
    re.IGNORECASE,
)

_CHAT_FILLERS: Set[str] = {
    "هههه",
    "ههه",
    "اي",
    "ايه",
    "تمام",
    "زين",
    "طيب",
    "وبس",
    "يعني",
    "وش",
}


def should_send_product_miss_notification(message: str) -> bool:
    """
    يعيد True فقط إذا كانت الرسالة تبدو طلب منتج أو سؤال تسوق يستحق تنبيه الفريق.

    لا نرسل تنبيهاً عند: رفض مختصر، نص طوله جداً قليل بدون محتوى تسوق، تعبيرات فارغة.
    """
    raw = (message or "").strip()
    if len(raw) < 5:
        return False
    if _SIMPLE_OBJECTION_RE.match(raw):
        return False
    nf = "".join(raw.split())
    if len(nf) < 4:
        return False

    tl = raw.lower().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")

    objection_phrases = (
        "ما ابغي",
        "ما أبغي",
        "مابغي",
        "مابي",
        "لا ابغي",
        "لا أبغي",
        "ما راح اطلب",
        "ما بطلب",
        "مو راضي عالسعر",
        "غالي",
        "ما عجبني",
        "سيء جداً",
        "سيء جدا",
        "توقفوا",
        "كفاكم",
        "مو زين",
        "مزعجين",
        "مزعجة",
        "ما ابيكم",
        "ما أبيكم",
    )
    for p in objection_phrases:
        if p in tl:
            return False

    tokens = [
        t.strip("،.؟!…")
        for t in raw.replace(",", " ").split()
        if len(t.strip("،.؟!…")) >= 2
    ]
    if not tokens:
        return False

    substantive = sum(1 for t in tokens if t.lower() not in _CHAT_FILLERS)
    if substantive < 1 and len(tokens) < 3:
        return False

    if len(tokens) <= 2 and substantive == 0:
        return False

    shopping_touch = False
    for t in tl.split():
        if len(t) >= 4:
            shopping_touch = True
            break

    hints = ("ابغي", "أبغي", "في عندكم", "عندكم", "سعر", "كم", "وين", "متوفر", "قسم")
    if any(h in tl for h in hints):
        shopping_touch = True

    if not shopping_touch and len(tokens) < 5:
        return False

    return True
