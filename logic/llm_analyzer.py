# -*- coding: utf-8 -*-
"""
محلل رسائل عبر LLM — إخراج JSON فقط (تصنيف + إعادة صياغة).
لا يردّ على العميل ولا يُدخل معلومات تشغيلية من عند النموذج.

ملاحظة: دعم Ollama أُزيل من المشروع. عند LLM_ENABLED لا يُستدعى أي مزوّد هنا حتى
يُعاد ربط المحلل بمزوّد مدعوم (مثل OpenAI) إن لزم.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from config import LLM_ENABLED

logger = logging.getLogger(__name__)

ALLOWED_INTENTS = frozenset({"complaint", "product", "branch", "location", "unknown"})


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL | re.IGNORECASE)
    if m:
        t = m.group(1).strip()
    i = t.find("{")
    j = t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i : j + 1])
    except json.JSONDecodeError:
        return None


def _normalize_branch(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s or s in ("null", "none", "undefined", "-"):
        return None
    mapping = {
        "جدة": "جدة",
        "جده": "جدة",
        "مكة": "مكة",
        "مكه": "مكة",
        "المدينة": "المدينة",
        "المدينه": "المدينة",
        "المدينة المنورة": "المدينة",
        "خميس مشيط": "خميس مشيط",
        "خميس": "خميس مشيط",
        "قلوة": "قلوة",
        "قلوه": "قلوة",
    }
    if s in mapping:
        return mapping[s]
    for k, val in mapping.items():
        if k in s.replace("فرع", "").strip():
            return val
    return None


def _normalize_confidence(v: Any) -> float:
    """يحوّل ثقة النموذج إلى [0,1]؛ القيم غير الصالحة → 0.0"""
    if v is None:
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    if x != x:  # NaN
        return 0.0
    return max(0.0, min(1.0, x))


def _normalize_keywords(v: Any) -> List[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, list):
        out: List[str] = []
        for x in v[:8]:
            t = str(x).strip()
            if t and t not in out:
                out.append(t)
        return out
    return []


def normalize_llm_result(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not raw or not isinstance(raw, dict):
        return None
    intent = str(raw.get("intent", "unknown")).strip().lower()
    if intent not in ALLOWED_INTENTS:
        intent = "unknown"
    cleaned = str(raw.get("cleaned_message", "")).strip()
    branch = _normalize_branch(raw.get("branch"))
    keywords = _normalize_keywords(raw.get("keywords"))
    confidence = _normalize_confidence(raw.get("confidence"))
    return {
        "intent": intent,
        "cleaned_message": cleaned,
        "branch": branch,
        "keywords": keywords,
        "confidence": confidence,
    }


def _call_provider(user_text: str) -> str:
    """لا مزوّد مربوط — طبقة Ollama أُزيلت من المشروع."""
    if LLM_ENABLED:
        logger.debug("llm_analyzer: LLM_ENABLED لكن لا يوجد مزوّد محلل مفعّل (Ollama أُزيل)")
    return ""


def analyze_user_message(user_text: str) -> Optional[Dict[str, Any]]:
    """
    يحلل النص ويُرجع dict موحّد أو None عند التعطيل/الفشل.
    """
    if not LLM_ENABLED:
        return None
    t = (user_text or "").strip()
    if len(t) < 2:
        return None
    raw_content = _call_provider(t)
    parsed = _extract_json_object(raw_content)
    return normalize_llm_result(parsed)
