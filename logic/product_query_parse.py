# -*- coding: utf-8 -*-
"""
تحليل استعلامات المنتجات للشات: تطبيع مرادفات، استخراج فلاتر (جنس/لون)،
وملخص نية قبل التوجيه — بدون استدعاء نماذج لغوية للبحث.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# مرادفات شائعة → مصطلح أقرب لما في قاعدة البيانات / البحث
_SYNONYM_REPLACEMENTS: Tuple[Tuple[str, str], ...] = (
    ("تيشيرت", "تشيرت"),
    ("تيشرت", "تشيرت"),
    ("تي شيرت", "تشيرت"),
    ("بنطلونات", "سروال"),
    ("بنطلون", "سروال"),
    ("طلعات", "سهرة"),
    ("طلعة", "سهرة"),
)

# علامات الجنس في النص (للفلترة اللاحقة على نتائج البحث)
_MALE_MARKERS: tuple[str, ...] = (
    "رجالي",
    "رجال",
    "ولادي",
    "اولادي",
    "رج.",
)
_FEMALE_MARKERS: tuple[str, ...] = (
    "نسائي",
    "نساء",
    "نسوان",
    "سيدات",
    "بناتي",
    "حريمي",
    "نسا",
)


def normalize_product_vocabulary(text: str) -> str:
    """استبدال مرادفات بصيغة موحّدة قبل استخراج كلمات البحث."""
    t = (text or "").strip()
    if not t:
        return t
    for src, dst in _SYNONYM_REPLACEMENTS:
        if src in t:
            t = t.replace(src, dst)
    return t


def blob_matches_gender_filter(blob: str, gender: Optional[str]) -> bool:
    """
    يتحقق إن كان وصف/اسم المنتج لا يتعارض مع طلب الجنس (رجالي/نسائي).
    إن لم يُذكر جنس في البيانات يُقبل (لا نستبعد لغموض).
    """
    if not gender:
        return True
    b = blob or ""
    has_m = any(m in b for m in _MALE_MARKERS)
    has_f = any(m in b for m in _FEMALE_MARKERS)
    if gender == "male":
        if has_f and not has_m:
            return False
    elif gender == "female":
        if has_m and not has_f:
            return False
    return True


def extract_gender_filter(text: str) -> Optional[str]:
    """
    يعيد 'male' | 'female' | None إن وُجدت علامة واضحة في الرسالة.
    """
    t = (text or "").strip()
    if not t:
        return None
    has_m = any(m in t for m in _MALE_MARKERS)
    has_f = any(m in t for m in _FEMALE_MARKERS)
    if has_m and not has_f:
        return "male"
    if has_f and not has_m:
        return "female"
    return None


def normalize_for_product_search(message: str) -> str:
    """رسالة جاهزة لسلسلة البحث (_build_products_response)."""
    return normalize_product_vocabulary(message)


def analyze_shopping_intent(message: str) -> Dict[str, Any]:
    """
    ملخص خفيف يُخزَّن في الجلسة للتشخيص — لا يستبدل detect_chat_intent.
    """
    raw = (message or "").strip()
    norm = normalize_for_product_search(raw)
    return {
        "normalized_message": norm,
        "gender_guess": extract_gender_filter(norm),
        "had_synonym_normalization": norm != raw,
    }
