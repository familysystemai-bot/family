# -*- coding: utf-8 -*-
"""
تحليل النية القائم على القواعد والكلمات المفتاحية للشات.
الكلمات: logic/keywords.py فقط.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from logic import keywords as kw

# إعادة تصدير لبقية المشروع (مثلاً chat_service)
PRODUCT_HINTS = kw.PRODUCT_HINTS


def user_wants_open_now(t: str) -> bool:
    """سؤال عن حالة الفرع حالياً (بدون الخلط مع وصف منتج «مفتوح»)."""
    if not t:
        return False
    tl = t.replace("؟", " ").replace("?", " ")
    if "مفتوح" not in tl and "فاتح" not in tl:
        return False
    return any(x in tl for x in kw.OPEN_NOW_CONTEXT_MARKERS)


def location_reply_kind(user_message: str) -> str:
    """
    نوع رد الفرع: phone | open_now | location_link | when_open | hours | default
    """
    t = (user_message or "").strip()
    if not t:
        return "default"
    tnorm = t.replace("؟", " ").replace("?", " ")

    if any(p in tnorm for p in kw.BRANCH_PHONE_PHRASES):
        return "phone"
    if user_wants_open_now(tnorm) or any(k in tnorm for k in kw.OPEN_NOW_EXPLICIT_PHRASES):
        return "open_now"
    if any(p in tnorm for p in kw.LOCATION_LINK_PHRASES):
        return "location_link"
    if any(p in tnorm for p in kw.WHEN_OPEN_PHRASES):
        return "when_open"
    if any(p in tnorm for p in kw.HOURS_ONLY_PHRASES):
        return "hours"
    if any(k in tnorm for k in kw.BRANCH_HOURS_KEYWORDS):
        return "hours"
    if any(k in tnorm for k in kw.BRANCH_LOCATION_KEYWORDS):
        return "location_link"
    return "default"


def _complaint_signals_negated(text: str) -> bool:
    """صياغات تدل على عدم وجود شكوى — لا نصنّف كـ complaint عندها."""
    t = (text or "").strip()
    tl = t.lower()
    if "ما عندي مشكلة" in t or "ما عندك مشكلة" in t:
        return True
    if "ما في مشكلة" in t or "ما فيه مشكلة" in t or "مافي مشكلة" in tl:
        return True
    if "بدون مشكلة" in t:
        return True
    if "لا مشكلة" in t or "ولا مشكلة" in t:
        return True
    if "مو مشكلة" in tl or "مش مشكلة" in t:
        return True
    return False


def detect_chat_intent(message: str, resolve_branch: Callable[[str], Optional[str]]) -> str:
    """
    ترتيب النية: ترحيب → سياسة استرجاع → موقع/دوام/مفتوح الآن → شكر/وداع → قسم → توصية → منتج → عام → شكوى → فرع مختصر → unknown.

    الشكوى بعد إشارات المنتج حتى لا تُلتقط استفسارات التسوق كشكوى بسبب كلمات عامة (مثل «مشكلة»).

    القيم المُرجعة (ثابتة مع المسارات الحالية): greeting | complaint | return_policy | branch_phone | location | thanks | goodbye | section | recommendation | product | general | location_pick | unknown
    """

    def _partial_product_hint_match(text: str) -> bool:
        """contains أو تطابق جزئي (ثلاثية أحرف) لكلمات PRODUCT_HINTS."""
        for h in kw.PRODUCT_HINTS:
            h = (h or "").strip()
            if len(h) < 2:
                continue
            if h in text:
                return True
            if len(h) >= 4:
                for i in range(len(h) - 2):
                    if h[i : i + 3] in text:
                        return True
        return False

    t = (message or "").strip()
    tl = t.lower()
    if not tl:
        return "unknown"

    if any(k in t for k in kw.GREETING_KEYWORDS):
        return "greeting"

    if any(k in t for k in kw.RETURN_POLICY_KEYWORDS):
        return "return_policy"

    if any(x in t for x in kw.BRANCH_PHONE_CONTACT_TRIGGERS):
        return "branch_phone"

    if (
        any(k in t for k in kw.BRANCH_LOCATION_KEYWORDS)
        or any(k in t for k in kw.BRANCH_HOURS_KEYWORDS)
        or user_wants_open_now(t)
    ):
        return "location"

    if any(k in t for k in kw.THANKS_KEYWORDS):
        return "thanks"
    if any(k in tl for k in kw.GOODBYE_KEYWORDS):
        return "goodbye"

    if any(k in t for k in kw.SECTION_KEYWORDS):
        return "section"

    if any(k in t for k in kw.RECOMMENDATION_PHRASES):
        return "recommendation"

    has_request = any(w in t for w in kw.PRODUCT_REQUEST_WORDS)
    has_context_word = any(w in t for w in kw.PRODUCT_CONTEXT_WORDS)
    has_product_word = _partial_product_hint_match(t)

    if has_request and has_context_word:
        return "product"
    if has_request and has_product_word:
        return "product"
    if has_product_word:
        return "product"

    if tl in kw.ACK_GENERAL or (len(t) <= 4 and t in kw.ACK_GENERAL):
        return "general"

    if len(t) < 2:
        return "unknown"

    if not _complaint_signals_negated(t) and (
        any(k in t for k in kw.COMPLAINT_KEYWORDS)
        or any(p in t for p in kw.COMPLAINT_NATURAL_PHRASES)
    ):
        return "complaint"

    br = resolve_branch(t)
    if br and len(t) < 36 and not has_product_word and not has_request:
        return "location_pick"

    return "unknown"


def pre_route_intent_snapshot(
    message: str, resolve_branch: Callable[[str], Optional[str]]
) -> Dict[str, Any]:
    """
    لقطة قبل التوجيه: نية رئيسية + تصنيف فرعي لمسار المنتج (بحث جديد / متابعة عرض).
    يُستدعى مرة واحدة لكل رسالة من مسار الشات.
    """
    from logic.product_query_parse import normalize_for_product_search
    from logic.product_service import _looks_like_next_product_request

    primary = detect_chat_intent(message, resolve_branch)
    product_sub: Optional[str] = None
    if primary == "product":
        product_sub = (
            "product_followup"
            if _looks_like_next_product_request(message)
            else "product_search"
        )
    return {
        "primary_intent": primary,
        "product_sub_intent": product_sub,
        "normalized_for_search": normalize_for_product_search(message),
    }
