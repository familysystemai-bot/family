# -*- coding: utf-8 -*-
from __future__ import annotations

from config import (
    COMPLAINT_SCORE_BRANCH_WEIGHT,
    COMPLAINT_SCORE_NEGATIVE_WEIGHT,
    COMPLAINT_SCORE_PRIMARY_WEIGHT,
    COMPLAINT_SCORE_THRESHOLD,
    INTENT_SCORE_THRESHOLD_DIRECT,
)
from logic import keywords as kw

COMPLAINT_NEGATIVE_TONE_MARKERS = (
    "زعلان",
    "مو راضي",
    "موراضي",
    "خايس",
    "سيء",
    "سيئة",
    "منزعج",
    "أسوأ",
    "اسوأ",
    "ما وصل",
    "سوء",
    "غير راضي",
    "تجربة سيئة",
)


def has_primary_complaint_signal(normalized_message: str) -> bool:
    t = (normalized_message or "").strip()
    return any(k in t for k in kw.COMPLAINT_KEYWORDS) or any(
        p in t for p in kw.COMPLAINT_NATURAL_PHRASES
    )


def has_negative_complaint_tone(normalized_message: str) -> bool:
    t = (normalized_message or "").strip()
    return any(k in t for k in COMPLAINT_NEGATIVE_TONE_MARKERS)


def compute_complaint_score(
    normalized_message: str, *, has_known_branch: bool = False
) -> int:
    score = 0
    primary = has_primary_complaint_signal(normalized_message)
    negative = has_negative_complaint_tone(normalized_message)
    if primary:
        score += COMPLAINT_SCORE_PRIMARY_WEIGHT
    if negative:
        score += COMPLAINT_SCORE_NEGATIVE_WEIGHT
    # ذكر مدينة/فرع وحده استفسار موقع شائع — لا يُعد شكوى إلا مع نبرة أو عبارة شكوى
    if has_known_branch and (primary or negative):
        score += COMPLAINT_SCORE_BRANCH_WEIGHT
    return score


def complaint_score_is_direct(score: int) -> bool:
    return int(score) >= int(COMPLAINT_SCORE_THRESHOLD)


def complaint_score_to_intent_score(score: int) -> float:
    threshold = max(int(COMPLAINT_SCORE_THRESHOLD), 1)
    return round((float(INTENT_SCORE_THRESHOLD_DIRECT) / float(threshold)) * float(score), 2)

