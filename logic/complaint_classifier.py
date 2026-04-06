# -*- coding: utf-8 -*-
"""
تصنيف تلقائي لنص الشكوى (قواعد كلمات مفتاحية — بدون نماذج خارجية).
القيم المخزّنة: replacement_return | staff_conduct | delay | product_issue | unspecified
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# ترتيب فضّل عند التعادل (أولوية للأكثر تحديداً للتحليل التشغيلي)
_TIE_BREAK: Tuple[str, ...] = (
    "product_issue",
    "replacement_return",
    "delay",
    "staff_conduct",
)

_LABELS_AR: Dict[str, str] = {
    "replacement_return": "استبدال / استرجاع",
    "staff_conduct": "تعامل موظف",
    "delay": "تأخير",
    "product_issue": "مشكلة منتج",
    "unspecified": "غير مصنّف",
}

# كلمات وعبارات لكل فئة (يمكن توسيعها دون تغيير المخطط)
_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "replacement_return": (
        "استبدال",
        "استرجاع",
        "استرجع",
        "ارجاع",
        "أرجع",
        "ارجع",
        "ما استبدلوا",
        "ما استبدل",
        "ما بدلوا",
        "ما بدل",
        "بدلوني",
        "أبدل",
        "ابدل",
        "تعويض",
        "استرداد",
        "استرد",
        "فلوسي",
        "الفلوس",
        "استرجاع المبلغ",
        "رجعوا لي",
        "أرجعوا",
    ),
    "staff_conduct": (
        "تعامل",
        "تعامل سيء",
        "تعامل سئ",
        "تعامل مو حلو",
        "موظف",
        "موظفة",
        "موظفين",
        "سوء معاملة",
        "معاملة سيئة",
        "معاملة مو",
        "سلوك",
        "سلوك مو لائق",
        "وقاحة",
        "فظ",
        "إهمال",
        "تجاهل",
        "تجاهلوني",
        "ما حد رد",
        "ما رد",
        "ما ردوا",
        "ما يردون",
        "خدمة سيئة",
        "سيئة الخدمة",
        "زعلان",
        "منزعج",
        "تهميش",
        "تطنيش",
    ),
    "delay": (
        "تأخير",
        "تأخر",
        "تأخروا",
        "تأخر الطلب",
        "تأخرت",
        "ما وصل",
        "ما وصلني",
        "ما وصلني الطلب",
        "لسه ما وصل",
        "لحد الحين ما وصل",
        "التوصيل",
        "توصيل",
        "شحن",
        "الطلب متأخر",
        "متأخر",
        "تأخر الموعد",
        "الموعد",
        "انتظار",
        "طول الانتظار",
        "تأخروا يردون",
    ),
    "product_issue": (
        "منتج",
        "مقاس",
        "مقاس غلط",
        "اللون",
        "لون غلط",
        "لون مو",
        "عيب",
        "عيوب",
        "تلف",
        "مكسور",
        "مكسورة",
        "خياطة",
        "قماش",
        "جودة",
        "جودة سيئة",
        "مو مطابق",
        "غير مطابق",
        "نقص",
        "نقصوا",
        "قطعة",
        "قطع",
        "بهت",
        "بهتان",
        "غلط بالفاتورة",
        "خطأ بالفاتورة",
        "مو ظابط",
        "مو زابط",
        "عطل",
        "فستان",
        "عباية",
        "فصال",
        "الفصال",
        "قص",
        "قصة",
    ),
}


def complaint_type_label_ar(complaint_type: str) -> str:
    return _LABELS_AR.get((complaint_type or "").strip(), _LABELS_AR["unspecified"])


def classify_complaint_issue(text: str) -> str:
    """
    يحلل نص الشكوى ويُرجع مفتاح التصنيف.
    """
    if not text or len(text.strip()) < 2:
        return "unspecified"

    t = text.strip()
    scores: Dict[str, int] = {k: 0 for k in _KEYWORDS}

    for cat, kws in _KEYWORDS.items():
        for kw in kws:
            if kw in t:
                scores[cat] += 1

    best = max(scores.values())
    if best <= 0:
        return "unspecified"

    winners = [c for c, s in scores.items() if s == best]
    if len(winners) == 1:
        return winners[0]

    for pref in _TIE_BREAK:
        if pref in winners:
            return pref
    return winners[0]


def all_complaint_type_keys() -> List[str]:
    return list(_KEYWORDS.keys()) + ["unspecified"]
