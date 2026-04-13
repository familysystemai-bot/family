# -*- coding: utf-8 -*-
"""
كشف لهجة تقريبي للرسائل النصية — قواعد كلمات مفتاحية فقط (بدون AI).

كل لهجة لها كلمتان: كلمات "مميزة" (وزن 2) وكلمات "عامة" (وزن 1).
الكلمات المميزة هي ما لا يظهر تقريباً في باقي اللهجات.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# ترتيب الفحص عند التعادل في عدد النقاط (أولوية أعلى = أولاً)
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

# كلمات مميزة لكل لهجة — وزنها 2 نقطة (نادرة في اللهجات الأخرى)
_DIALECT_STRONG: Dict[str, List[str]] = {
    "hijazi":  ["إيش", "مرة حلو", "مرة زين", "مرة كويس", "عشان كده", "مرة غالي"],
    "najdi":   ["وش", "حيل", "ويش", "ماحيل", "هذاك", "ذيك", "كذا وش"],
    "janoubi": ["وينك ذا", "أبغا ذا", "وش ذا"],
    "sharqi":  ["شلونك", "يبه", "هالحين", "عيل شلون"],
    "shamali": ["علومك", "وشلونك", "شلونك ياخوي"],
    "yemeni":  ["وقتاش", "بجيك", "علاش", "فيش", "ماش"],
    "masri":   ["إيه ده", "ايه ده", "عايز إيه", "عايز ايه", "كده بقى", "ازيك", "إزيك",
                "بتاع", "بتاعت", "أيوه بس", "ايوه بس"],
    "jordani": ["هيك", "هلق", "شو بدك", "مشان", "كتير منيح"],
    "iraqi":   ["هواية", "ولك", "منو", "هواية زين", "چ"],
}

# كلمات عامة — وزنها 1 نقطة (تظهر في عدة لهجات لكن تُرجّح عند التعادل)
DIALECT_KEYWORDS: Dict[str, List[str]] = {
    "hijazi":  ["إيش", "فين", "مرة", "عشان"],
    "najdi":   ["وش", "وين", "حيل", "زين"],
    "janoubi": ["أبغا", "ذا", "وينك"],
    "sharqi":  ["شلونك", "زين", "الحين"],
    "shamali": ["علومك", "وشلونك"],
    "yemeni":  ["ايش", "فينك", "ذا"],
    "masri":   ["ايه", "فين", "عايز", "كده", "مش", "بقى", "أيوه", "ايوه", "مين"],
    "jordani": ["شو", "وين", "بدّي", "بدي", "هيك", "هلق", "كتير"],
    "iraqi":   ["شلون", "وين", "هواية", "منو"],
}


def detect_dialect(message: str) -> str:
    """
    يعيد مفتاح اللهجة إذا وُجدت كلمات مفتاحية، وإلا "default".
    الكلمات المميزة (_DIALECT_STRONG) تُعطى وزناً أعلى (2) من الكلمات العامة (1).
    عند تعادل النقاط يُختار حسب ترتيب الأولوية أعلاه.
    """
    text = (message or "").strip()
    if not text:
        return "default"

    # نُنشئ نسختين من النص لمعالجة اختلاف الهمزة في "ايش / إيش"
    variants = {text}
    if "إيش" in text and "ايش" not in text:
        variants.add(text.replace("إيش", "ايش"))
    if "ايش" in text and "إيش" not in text:
        variants.add(text.replace("ايش", "إيش"))

    def score_for_dialect(dialect_key: str) -> int:
        total = 0
        strong_words = _DIALECT_STRONG.get(dialect_key) or []
        common_words = DIALECT_KEYWORDS.get(dialect_key) or []
        for blob in variants:
            for w in strong_words:
                if w and w in blob:
                    total += 2
            for w in common_words:
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
