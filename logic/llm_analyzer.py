# -*- coding: utf-8 -*-
"""
محلل رسائل عبر LLM — إخراج JSON فقط (تصنيف + إعادة صياغة).
لا يردّ على العميل ولا يُدخل معلومات تشغيلية من عند النموذج.
يمكن إضافة مزودين (مثل OpenAI) بنفس الواجهة دون تغيير app.py.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import (
    LLM_ENABLED,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_REQUEST_TIMEOUT,
    OLLAMA_BASE_URL,
)

logger = logging.getLogger(__name__)

ALLOWED_INTENTS = frozenset({"complaint", "product", "branch", "location", "unknown"})

# دون هذا الحد لا يُستخدم مخرج المحلل في التوجيه (سلوك القواعد فقط).
LLM_MIN_USABLE_CONFIDENCE = 0.6

SYSTEM_PROMPT = """أنت مصنّف واستخراج مفردات فقط — لا تبحث في قاعدة بيانات ولا تقترح منتجات أو أقسام.
دورك: فهم نية الرسالة واستخراج كلمات من النص نفسه (نوع المنتج، لون، رجالي/نسائي إن وُجدت صراحة).
قواعد إلزامية:
- لا تجب المستخدم ولا تقدّم ساعات دوام أو عناوين أو أسعار أو توفراً أو فروعاً من عندك.
- لا تخترع أسماء منتجات أو أقسام؛ اقتصر على ما يفهم من صياغة المستخدم.
- لا تُستخدم مخرجاتك لاتخاذ قرار عرض منتج — العرض يتم لاحقاً من النظام وقاعدة البيانات فقط.
- أخرج JSON خام فقط، بدون Markdown وبدون ``` وبدون شرح.
- الحقول المطلوبة بالضبط:
  "intent": واحد من: complaint | product | branch | location | unknown
    (branch = استفسار عن فرع/فروع بلا تفاصيل موقع؛ location = عنوان/خرائط/وين الفرع)
  "cleaned_message": جملة عربية واضحة تعيد صياغة طلب المستخدم فقط (بدون إضافات)
  "branch": null أو أحد الأسماء: جدة | مكة | المدينة | خميس مشيط | قلوة (بلا كلمة فرع)
  "keywords": مصفوفة 0–8 كلمات عربية من النص: نوع المنتج، لون، رجالي/نسائي، مناسبة… (لا تخترع أصنافاً)
  "confidence": رقم عشري من 0 إلى 1 يعبّر عن ثقتك في التصنيف والكلمات (1 = متأكد، 0 = غير متأكد).
    إن كنت غير متأكد أو النص غامضاً استخدم قيمة منخفضة (مثل 0.3–0.5).

مثال صالح:
{"intent":"product","confidence":0.85,"cleaned_message":"أبحث عن تشيرت رجالي أسود","branch":null,"keywords":["تشيرت","رجالي","أسود"]}
"""


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


def _ollama_chat_http(messages: List[Dict[str, str]]) -> str:
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = json.dumps(
        {"model": LLM_MODEL, "messages": messages, "stream": False}
    ).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=LLM_REQUEST_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("message") or {}).get("content") or ""


def _ollama_chat_lib(messages: List[Dict[str, str]]) -> str:
    import ollama  # type: ignore

    r = ollama.chat(model=LLM_MODEL, messages=messages)
    return (r.get("message") or {}).get("content") or ""


def _call_provider(user_text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (user_text or "").strip()[:4000]},
    ]
    if LLM_PROVIDER != "ollama":
        logger.warning("LLM provider %s غير مدعوم بعد — استخدم ollama", LLM_PROVIDER)
        return ""
    try:
        try:
            return _ollama_chat_lib(messages)
        except ImportError:
            return _ollama_chat_http(messages)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        logger.warning("Ollama request failed: %s", e)
        return ""
    except Exception as e:
        logger.warning("Ollama unexpected error: %s", e)
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
