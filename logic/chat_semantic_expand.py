# -*- coding: utf-8 -*-
"""
توسيع مفردات البحث في الشات (مناسبات ومرادفات) دون تعديل قاعدة البيانات.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# كلمة مناسبة / عامة → كلمات إضافية للبحث عن أقسام ومنتجات قريبة
OCCASION_EXTRA_TERMS: Dict[str, Tuple[str, ...]] = {
    "زواج": ("فستان", "فساتين", "سواريه", "ملابس", "نسائي", "اكسسوار", "اكسسوارات"),
    "زفاف": ("فستان", "فساتين", "سواريه", "اكسسوار", "اكسسوارات", "طقم"),
    "خطوبة": ("فستان", "فساتين", "سواريه", "ملابس", "نسائي"),
    "مناسبة": ("فستان", "فساتين", "ملابس", "نسائي", "اكسسوارات", "شنط", "حقائب"),
    "سهرة": ("فستان", "فساتين", "سواريه", "كلاسيك", "ملابس"),
    "طلعة": ("ملابس", "عباية", "كاجوال", "تشكيلة"),
    "حفلة": ("فساتين", "فستان", "سواريه", "ملابس"),
    "عرس": ("فستان", "فساتين", "سواريه", "ملابس"),
}


def _strip_ar_word(w: str) -> str:
    x = (w or "").strip().strip("؟?،,.")
    if x.startswith("ال") and len(x) > 3:
        return x[2:]
    return x


def occasion_expansion_tokens(message: str) -> List[str]:
    """كلمات إضافية مستنتجة من مناسبات عامة في الرسالة."""
    msg = (message or "").strip()
    if not msg:
        return []
    extra: List[str] = []
    seen = set()
    for raw in msg.split():
        for key in (_strip_ar_word(raw), raw.strip().strip("؟?،,.")):
            if key in OCCASION_EXTRA_TERMS:
                for t in OCCASION_EXTRA_TERMS[key]:
                    if t not in seen:
                        seen.add(t)
                        extra.append(t)
    return extra


def all_product_search_needles(needle: str, message: str) -> List[str]:
    """
    قائمة استعلامات بالترتيب: الإبرة المعتادة، الرسالة كاملة، ثم توسيع المناسبات.
    """
    out: List[str] = []
    seen = set()

    def add(s: str) -> None:
        s = (s or "").strip()
        if len(s) >= 2 and s not in seen:
            seen.add(s)
            out.append(s)

    add(needle)
    add(message)
    for t in occasion_expansion_tokens(message):
        add(t)
    return out


def section_search_variants(message: str, base_tokens: List[str]) -> List[str]:
    """دمج رموز مستخرجة من الرسالة مع توسيع المناسبات، بدون تكرار."""
    seen = set()
    out: List[str] = []
    for t in base_tokens:
        t = (t or "").strip()
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            out.append(t)
    for t in occasion_expansion_tokens(message):
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            out.append(t)
    return out
