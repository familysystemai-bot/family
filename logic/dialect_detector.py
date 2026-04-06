# -*- coding: utf-8 -*-
"""
كشف لهجة تقريبي للرسائل النصية — قواعد كلمات مفتاحية فقط (بدون AI).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# ترتيب الفحص عند التعادل في عدد التطابقات (أولوية أعلى = أولاً في القائمة)
_DIALECT_PRIORITY: Tuple[str, ...] = (
    "hijazi",
    "najdi",
    "janoubi",
    "sharqi",
    "shamali",
    "yemeni",
    "masri",
    "jordani",
    "iraqi",
)

DIALECT_KEYWORDS: Dict[str, List[str]] = {
    "hijazi": ["إيش", "فين", "مرة"],
    "najdi": ["وش", "وين", "حيل"],
    "janoubi": ["وينك ذا", "أبغا", "ذا"],
    "sharqi": ["شلونك", "زين"],
    "shamali": ["علومك", "وشلونك"],
    "yemeni": ["ايش", "فينك", "ذا"],
    "masri": ["ايه", "فين", "عايز"],
    "jordani": ["شو", "وين", "بدّي", "بدي"],
    "iraqi": ["شلون", "وين", "اريد"],
}


def detect_dialect(message: str) -> str:
    """
    يعيد مفتاح اللهجة إذا وُجدت كلمات مفتاحية، وإلا "default".
    عند تعادل عدد الكلمات المطابقة يُختار حسب ترتيب الأولوية أعلاه.
    """
    text = (message or "").strip()
    if not text:
        return "default"

    variants = {text}
    if "إيش" in text and "ايش" not in text:
        variants.add(text.replace("إيش", "ايش"))
    if "ايش" in text and "إيش" not in text:
        variants.add(text.replace("ايش", "إيش"))

    def score_for_dialect(dialect_key: str) -> int:
        words = DIALECT_KEYWORDS.get(dialect_key) or []
        total = 0
        for blob in variants:
            for w in words:
                if w and w in blob:
                    total += 1
        return total

    best: str = "default"
    best_score = 0
    for key in _DIALECT_PRIORITY:
        s = score_for_dialect(key)
        if s > best_score:
            best_score = s
            best = key

    return best if best_score > 0 else "default"
