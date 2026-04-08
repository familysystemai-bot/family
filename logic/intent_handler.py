# -*- coding: utf-8 -*-
"""
تحليل النية: قواعد أولية + Score-based intent (product / branch / complaint) + عتبة للتوجيه.
الكلمات: logic/keywords.py — الأوزان قابلة للتعديل عبر المتغيرات أو الكود.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from logic import keywords as kw
from logic.chat_service import normalize_message_for_branch_search

# إعادة تصدير لبقية المشروع
PRODUCT_HINTS = kw.PRODUCT_HINTS


# ─── عتبة القرار المباشر (بدون OpenAI كمنسّق رئيسي) ─────────────────
def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip().replace(",", "."))
    except ValueError:
        return default


INTENT_SCORE_THRESHOLD_DIRECT = _env_float("CHAT_INTENT_SCORE_THRESHOLD", 60.0)
# فرق دنيا بين الأول والثاني لاعتبار النية «واضحة» (رسائل مركبة → OpenAI)
INTENT_SCORE_MARGIN_AMBIGUOUS = _env_float("CHAT_INTENT_SCORE_MARGIN", 12.0)


@dataclass
class IntentScoreWeights:
    """أوزان قابلة للتعديل (يمكن تعيينها من البيئة INTENT_W_*)."""

    product_hint: float = field(default_factory=lambda: _env_float("INTENT_W_PRODUCT_HINT", 38.0))
    product_request: float = field(default_factory=lambda: _env_float("INTENT_W_PRODUCT_REQUEST", 42.0))
    product_context_occasion: float = field(
        default_factory=lambda: _env_float("INTENT_W_PRODUCT_CONTEXT", 36.0)
    )
    product_price: float = field(default_factory=lambda: _env_float("INTENT_W_PRODUCT_PRICE", 34.0))
    product_stock: float = field(default_factory=lambda: _env_float("INTENT_W_PRODUCT_STOCK", 28.0))
    branch_location_phrase: float = field(
        default_factory=lambda: _env_float("INTENT_W_BRANCH_LOCATION_PHRASE", 44.0)
    )
    branch_hours: float = field(default_factory=lambda: _env_float("INTENT_W_BRANCH_HOURS", 40.0))
    branch_phone: float = field(default_factory=lambda: _env_float("INTENT_W_BRANCH_PHONE", 40.0))
    branch_generic: float = field(
        default_factory=lambda: _env_float("INTENT_W_BRANCH_GENERIC", 18.0)
    )  # «فرع»، «أقرب» — منخفض لتجنب overfitting
    branch_where_weak: float = field(
        default_factory=lambda: _env_float("INTENT_W_BRANCH_WHERE_WEAK", 8.0)
    )  # فين/وين فقط بلا سياق فرع
    complaint_keyword: float = field(
        default_factory=lambda: _env_float("INTENT_W_COMPLAINT_KW", 48.0)
    )
    complaint_phrase: float = field(
        default_factory=lambda: _env_float("INTENT_W_COMPLAINT_PHRASE", 52.0)
    )


_WEIGHTS = IntentScoreWeights()


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
    """صياغات تدل على عدم وجود شكوى."""
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


def _has_branch_context_for_where(t: str) -> bool:
    """«فين» و«وين» لا ترفع branch بلا سياق فرع/موقع/خريطة…"""
    markers = (
        "فرع",
        "موقع",
        "موقعكم",
        "موقعك",
        "عنوان",
        "خريطة",
        "خرائط",
        "قوقل",
        "جوال",
        "رقم",
        "تواصل",
        "دوام",
        "ساعات",
        "مفتوح",
        "فاتح",
        "قفل",
        "تفتح",
        "مدينة",
        "مكة",
        "جدة",
        "المدينة",
    )
    return any(m in t for m in markers)


def score_message_intents(message: str) -> Dict[str, Any]:
    """
    يحسب نقاط product / branch / complaint ويعيد كلماتاً مطابقة وترتيباً للنوايا المحتملة.
    """
    raw = (message or "").strip()
    t = normalize_message_for_branch_search(raw)
    tl = t.lower()
    W = _WEIGHTS

    scores: Dict[str, float] = {"product": 0.0, "branch": 0.0, "complaint": 0.0}
    detected: Dict[str, List[str]] = {"product": [], "branch": [], "complaint": []}

    # منتج — تلميحات وطلبات
    for h in kw.PRODUCT_HINTS:
        h = (h or "").strip()
        if len(h) >= 2 and h in t:
            scores["product"] += W.product_hint
            detected["product"].append(h)
            break
    for w in kw.PRODUCT_REQUEST_WORDS:
        if w in t:
            scores["product"] += W.product_request
            detected["product"].append(w)
            break
    for w in kw.PRODUCT_CONTEXT_WORDS:
        if w in t:
            scores["product"] += W.product_context_occasion
            detected["product"].append(w)
            break
    if "سعر" in t or "كم سعر" in t or "بكم" in tl:
        scores["product"] += W.product_price
        detected["product"].append("سعر/بكم")
    if "متوفر" in t or "يوجد" in t or "فيه عندكم" in t or "عندكم" in t:
        scores["product"] += W.product_stock
        detected["product"].append("توفر/عندكم")

    # فرع — عبارات كاملة أولاً
    for phrase in kw.BRANCH_LOCATION_KEYWORDS:
        if phrase in t:
            scores["branch"] += W.branch_location_phrase
            detected["branch"].append(phrase)
            break
    for phrase in kw.BRANCH_HOURS_KEYWORDS:
        if phrase in t:
            scores["branch"] += W.branch_hours
            detected["branch"].append(phrase)
            break
    for phrase in kw.BRANCH_PHONE_CONTACT_TRIGGERS:
        if phrase in t and any(x in t for x in ("رقم", "جوال", "تواصل", "اتصل", "واتس", "فرع")):
            scores["branch"] += W.branch_phone
            detected["branch"].append(phrase)
            break
    if user_wants_open_now(t):
        scores["branch"] += W.branch_hours
        detected["branch"].append("مفتوح/فاتح")

    for w in ("أقرب", "اقرب", "فرع", "فروع"):
        if w in t:
            scores["branch"] += W.branch_generic
            detected["branch"].append(w)
            break

    if any(x in t for x in ("فين", "وين", "فينكم", "وينكم")):
        if _has_branch_context_for_where(t):
            scores["branch"] += W.branch_location_phrase * 0.55
            detected["branch"].append("فين/وين+سياق")
        else:
            scores["branch"] += W.branch_where_weak

    # شكوى
    if not _complaint_signals_negated(t):
        for k in kw.COMPLAINT_KEYWORDS:
            if k in t:
                scores["complaint"] += W.complaint_keyword
                detected["complaint"].append(k)
                break
        for p in kw.COMPLAINT_NATURAL_PHRASES:
            if p in t:
                scores["complaint"] += W.complaint_phrase
                detected["complaint"].append(p[:40])
                break

    # إزالة تكرار في القوائم
    for k in detected:
        detected[k] = list(dict.fromkeys(detected[k]))[:24]

    ranked = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    top = ranked[0] if ranked else "product"
    second = ranked[1] if len(ranked) > 1 else top
    top_score = scores[top]
    second_score = scores[second]

    return {
        "scores": dict(scores),
        "detected_keywords": detected,
        "top_intent": top,
        "top_score": round(top_score, 2),
        "second_score": round(second_score, 2),
        "score_gap": round(top_score - second_score, 2),
        "possible_intents": ranked,
    }


def _legacy_early_intent_fixed(
    t: str, tl: str, resolve_branch: Callable[[str], Optional[str]]
) -> Optional[str]:
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
    return None


def get_intent_routing_decision(
    message: str, resolve_branch: Callable[[str], Optional[str]]
) -> Dict[str, Any]:
    """
    يقرر: rule_based (نية قديمة سريعة) | score_direct (product/branch/complaint) | needs_openai.

    - score_direct عندما top_score >= INTENT_SCORE_THRESHOLD_DIRECT والفجوة مع الثاني >= INTENT_SCORE_MARGIN_AMBIGUOUS
      (أو عندما الثاني ضعيف جداً).
    """
    raw = (message or "").strip()
    t = normalize_message_for_branch_search(raw)
    tl = t.lower()

    early = _legacy_early_intent_fixed(t, tl, resolve_branch)
    if early is not None:
        return {
            "route": "rule_based",
            "legacy_intent": early,
            "score_intent": None,
            "scores": {"product": 0.0, "branch": 0.0, "complaint": 0.0},
            "detected_keywords": {"product": [], "branch": [], "complaint": []},
            "possible_intents": [],
            "top_score": 0.0,
            "needs_clarification": False,
        }

    snap = score_message_intents(raw)
    scores: Dict[str, float] = snap["scores"]
    top = snap["top_intent"]
    top_score = float(snap["top_score"])
    second_score = float(snap["second_score"])
    gap = float(snap["score_gap"])

    # إقرار مباشر بالمنتج (قواعد قديمة مختصرة)
    has_request = any(w in t for w in kw.PRODUCT_REQUEST_WORDS)
    has_context_word = any(w in t for w in kw.PRODUCT_CONTEXT_WORDS)
    has_product_word = any(
        (h or "").strip() in t and len((h or "").strip()) >= 2 for h in kw.PRODUCT_HINTS
    )
    if has_request and has_context_word:
        return {
            "route": "score_direct",
            "legacy_intent": "product",
            "score_intent": "product",
            "scores": scores,
            "detected_keywords": snap["detected_keywords"],
            "possible_intents": snap["possible_intents"],
            "top_score": top_score,
            "needs_clarification": False,
            "score_snapshot": snap,
        }
    if has_request and has_product_word:
        return {
            "route": "score_direct",
            "legacy_intent": "product",
            "score_intent": "product",
            "scores": scores,
            "detected_keywords": snap["detected_keywords"],
            "possible_intents": snap["possible_intents"],
            "top_score": top_score,
            "needs_clarification": False,
            "score_snapshot": snap,
        }
    if has_product_word and not _complaint_signals_negated(t):
        return {
            "route": "score_direct",
            "legacy_intent": "product",
            "score_intent": "product",
            "scores": scores,
            "detected_keywords": snap["detected_keywords"],
            "possible_intents": snap["possible_intents"],
            "top_score": top_score,
            "needs_clarification": False,
            "score_snapshot": snap,
        }

    ambiguous = gap < INTENT_SCORE_MARGIN_AMBIGUOUS and second_score >= 35.0
    strong = top_score >= INTENT_SCORE_THRESHOLD_DIRECT and not ambiguous

    if strong and top in ("product", "branch", "complaint"):
        if top == "complaint" and _complaint_signals_negated(t):
            pass
        else:
            return {
                "route": "score_direct",
                "legacy_intent": top if top != "branch" else "location",
                "score_intent": top,
                "scores": scores,
                "detected_keywords": snap["detected_keywords"],
                "possible_intents": snap["possible_intents"],
                "top_score": top_score,
                "needs_clarification": False,
                "score_snapshot": snap,
            }

    # ACK قصير
    if tl in kw.ACK_GENERAL or (len(t) <= 4 and t in kw.ACK_GENERAL):
        return {
            "route": "rule_based",
            "legacy_intent": "general",
            "score_intent": None,
            "scores": scores,
            "detected_keywords": snap["detected_keywords"],
            "possible_intents": snap["possible_intents"],
            "top_score": top_score,
            "needs_clarification": False,
            "score_snapshot": snap,
        }

    if len(t) < 2:
        return {
            "route": "needs_openai",
            "legacy_intent": "unknown",
            "score_intent": top,
            "scores": scores,
            "detected_keywords": snap["detected_keywords"],
            "possible_intents": snap["possible_intents"],
            "top_score": top_score,
            "needs_clarification": True,
            "score_snapshot": snap,
        }

    # شكوى بالقواعد القديمة بعد المنتج
    if not _complaint_signals_negated(t) and (
        any(k in t for k in kw.COMPLAINT_KEYWORDS)
        or any(p in t for p in kw.COMPLAINT_NATURAL_PHRASES)
    ):
        return {
            "route": "score_direct",
            "legacy_intent": "complaint",
            "score_intent": "complaint",
            "scores": scores,
            "detected_keywords": snap["detected_keywords"],
            "possible_intents": snap["possible_intents"],
            "top_score": max(top_score, INTENT_SCORE_THRESHOLD_DIRECT),
            "needs_clarification": False,
            "score_snapshot": snap,
        }

    br = resolve_branch(t)
    if br and len(t) < 36 and not has_product_word and not has_request:
        return {
            "route": "rule_based",
            "legacy_intent": "location_pick",
            "score_intent": "branch",
            "scores": scores,
            "detected_keywords": snap["detected_keywords"],
            "possible_intents": snap["possible_intents"],
            "top_score": top_score,
            "needs_clarification": False,
            "score_snapshot": snap,
        }

    return {
        "route": "needs_openai",
        "legacy_intent": "unknown",
        "score_intent": top,
        "scores": scores,
        "detected_keywords": snap["detected_keywords"],
        "possible_intents": snap["possible_intents"],
        "top_score": top_score,
        "needs_clarification": ambiguous or top_score < INTENT_SCORE_THRESHOLD_DIRECT,
        "score_snapshot": snap,
    }


def detect_chat_intent_from_decision(d: Dict[str, Any], message: str) -> str:
    """يحوّل قرار التوجيه إلى نية السلسلة القديمة (نص واحد)."""
    if d.get("route") == "rule_based":
        return str(d.get("legacy_intent") or "unknown")
    if d.get("route") == "score_direct":
        li = d.get("legacy_intent")
        if li:
            return str(li)
        m = {"product": "product", "branch": "location", "complaint": "complaint"}
        si = d.get("score_intent")
        return m.get(str(si), "unknown")
    snap = d.get("score_snapshot") or {}
    scores = snap.get("scores") or d.get("scores") or {}
    msg = (message or "").strip()
    if scores.get("complaint", 0) > scores.get("product", 0) and scores.get(
        "complaint", 0
    ) > scores.get("branch", 0):
        if not _complaint_signals_negated(msg):
            return "complaint"
    if scores.get("product", 0) >= scores.get("branch", 0) and scores.get("product", 0) >= scores.get(
        "complaint", 0
    ):
        if any((h or "").strip() in msg for h in kw.PRODUCT_HINTS if len((h or "").strip()) >= 2):
            return "product"
    return "unknown"


def detect_chat_intent(message: str, resolve_branch: Callable[[str], Optional[str]]) -> str:
    """
    متوافق مع الشيفرة السابقة: يستنتج نية واحدة للمسارات التي تعتمد عليها (مثل شكاوى).
    """
    raw = (message or "").strip()
    d = get_intent_routing_decision(raw, resolve_branch)
    return detect_chat_intent_from_decision(d, raw)


def pre_route_intent_snapshot(
    message: str, resolve_branch: Callable[[str], Optional[str]]
) -> Dict[str, Any]:
    """
    لقطة قبل التوجيه: نية + نقاط + تصنيف فرعي للمنتج.
    """
    from logic.product_query_parse import normalize_for_product_search
    from logic.product_service import _looks_like_next_product_request

    raw = (message or "").strip()
    d = get_intent_routing_decision(raw, resolve_branch)
    primary = detect_chat_intent_from_decision(d, raw)
    product_sub: Optional[str] = None
    if primary == "product":
        product_sub = (
            "product_followup"
            if _looks_like_next_product_request(message)
            else "product_search"
        )
    ss = d.get("score_snapshot") or score_message_intents(raw)
    return {
        "primary_intent": primary,
        "product_sub_intent": product_sub,
        "normalized_for_search": normalize_for_product_search(message),
        "intent_scores": ss.get("scores"),
        "intent_top": ss.get("top_intent"),
        "intent_routing_route": d.get("route"),
    }
