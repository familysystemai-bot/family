# -*- coding: utf-8 -*-
"""
طبقة OpenAI (اختيارية): (1) تحليل استخراج JSON قبل بحث المنتجات — بدون قرار نهائي أو اختراع مخزون؛
(2) رد احتياطي نصي عند النية unknown عند الحاجة.
التعطيل: إزالة المفتاح أو OPENAI_PRESEARCH=false / AI_CHAT_FALLBACK=false حسب الوظيفة.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

from config import OPENAI_API_KEY, OPENAI_MODEL
from logic.chat_semantic_expand import all_product_search_needles

# حدّ استجابة النموذج (تقريباً كما طُلب)
_MAX_COMPLETION_TOKENS = 300
# حدّ حجم سياق JSON المُرسَل في البرومبت
_MAX_CONTEXT_CHARS = 7500

_SYSTEM_PROMPT = """أنت موظف خدمة عملاء في متجر سعودي. تكلم بلهجة بسيطة وطبيعية، ودود بدون مبالغة رسمية، وكأنك تكلّم العميل وجهًا لوجه — مو روبوت ولا نص جاف.

المصدر الوحيد للحقيقة هو JSON المرفوع:
- database_context.branches = أسماء الفروع
- database_context.products = منتجات (name, price, note)
- database_context.sections = أقسام وأقسام رئيسية مرتبطة بفرع
ولا تذكر أرقام أسعار أو تفاصيل إلا إذا وردت صراحة في هذه البيانات. ممنوع اختراع مواعيد، عناوين، توفر، أو أسعار من عندك.

إن كانت رسالة العميل عامة (مثل مناسبة: زواج، سهرة، طلعة)، حاول تربطها بلطف بما يظهر في البيانات — مثل ملابس أو فساتين أو أقسام قريبة من المعنى — بدون التزام إن لم يكن هناك ما يدعم ذلك.

طول الرد: من سطرين إلى أربعة أسطر كحد أقصى. واضح، مختصر، وفيه اقتراح مفيد (مثلاً: يذكر قسمًا أو نوع منتج من البيانات، أو يقترح يوضح الطلب).

إذا ما فيه تطابق واضح في البيانات:
- لا تعتذر بشكل فارغ ولا تقل «ما فهمت» مباشرة ولا «ممكن توضح أكثر؟» ولا «لم يتم العثور على نتائج» ولا صياغة تقنية.
- قل بلطف إن الشيء المحدد غير ظاهر في المعلومات المتوفرة عندك الآن، واقترح بديلًا إن وجد في نفس البيانات (فرع، قسم، أو صنف قريب).
- يمكنك صياغات مثل: «حاليًا المتوفر عندنا من البيانات…»، «ممكن يناسبك…»، «إذا حاب أبحث لك بشكل أدق قل لي…» مع الإبقاء على أسلوب بشري."""

_FALLBACK_ENV = "AI_CHAT_FALLBACK"
# تحليل استعلام البحث قبل قاعدة البيانات (استخراج فقط — لا قرار نهائي ولا رد للمستخدم)
_PRESEARCH_ENV = "OPENAI_PRESEARCH"
_MAX_PRESEARCH_USER_CHARS = 2000
_MAX_PRESEARCH_COMPLETION_TOKENS = 400

_SEARCH_ANALYSIS_SYSTEM = """أنت مستخرج معلومات فقط من رسائل عملاء متجر (عربي).
المطلوب: تحليل النص واستخراج حقول للبحث في قاعدة بيانات لاحقاً — أنت لا تتصل بقاعدة بيانات ولا تعرف المخزون.

قواعد إلزامية:
- لا تخترع أسماء منتجات أو ألوان أو أصناف لم يذكرها المستخدم صراحة أو بمعنى واضح في النص.
- لا تُجب العميل ولا تقدّم رداً نهائياً ولا تسويقاً.
- كل قيمة نصية يجب أن تستند إلى ما ورد في الرسالة؛ إن لم يُذكر شيء اترك الحقل فارغاً "" أو مصفوفة keywords فارغة.
- cleaned_message: إعادة صياغة قصيرة بلغة عربية واضحة باستخدام كلمات المستخدم فقط (لا إضافة أصناف جديدة).

أعد JSON فقط بالمفاتيح:
{
  "intent": "shopping" | "inquiry_other" | "unclear",
  "product_type": "",
  "color": "",
  "gender": "",
  "cleaned_message": "",
  "keywords": []
}
intent: تقدير خفيف لنوع الرسالة (للتتبع فقط — لا يُستخدم كقرار تشغيل من طرفك).
gender: واحد من: رجالي، نسائي، ولادي، أو سلسلة فارغة إن لم يُذكر.
keywords: من 0 إلى 8 كلمات عربية من النص تساعد البحث (نوع، لون، رجالي/نسائي، مقاس…)."""


def is_ai_fallback_enabled() -> bool:
    if not (OPENAI_API_KEY or "").strip():
        return False
    return os.getenv(_FALLBACK_ENV, "true").lower() in ("1", "true", "yes")


def is_ai_fallback_allowed(message: str, intent: str) -> bool:
    """
    لا يُستخدم الـ AI إلا لـ unknown أو product بلا نتائج،
    وبعد استبعاد أسئلة موقع/دوام/رقم/ساعة واضحة.
    """
    if intent not in ("unknown", "product"):
        return False
    from logic.chat_handlers.time_handler import enhanced_location_reply_kind

    k = enhanced_location_reply_kind(message or "")
    if k in (
        "phone",
        "hours",
        "when_open",
        "open_now",
        "clock_now",
        "opening_clock_explain",
        "location_link",
    ):
        return False
    return True


def is_openai_presearch_enabled() -> bool:
    """تحليل ما قبل البحث عبر OpenAI — يعتمد على OPENAI_API_KEY و OPENAI_PRESEARCH."""
    if not (OPENAI_API_KEY or "").strip():
        return False
    return os.getenv(_PRESEARCH_ENV, "true").lower() in ("1", "true", "yes")


def _text_has_product_hint(text: str) -> bool:
    from logic import keywords as kw

    t = (text or "").strip()
    for h in kw.PRODUCT_HINTS:
        hs = (h or "").strip()
        if len(hs) >= 2 and hs in t:
            return True
    return False


def should_run_presearch_analysis(message: str, intent: str) -> bool:
    """
    متى نستدعي تحليل OpenAI قبل البحث: رسائل غامضة/قصيرة/منتج — دون انتظار unknown فقط.
    """
    if not is_openai_presearch_enabled():
        return False
    m = (message or "").strip()
    if len(m) < 2 or len(m) > _MAX_PRESEARCH_USER_CHARS:
        return False
    from logic.chat_handlers.time_handler import enhanced_location_reply_kind

    k = enhanced_location_reply_kind(m)
    if k in (
        "phone",
        "hours",
        "when_open",
        "open_now",
        "clock_now",
        "opening_clock_explain",
        "location_link",
    ):
        return False
    if intent in ("unknown", "product"):
        return True
    if intent == "general":
        from logic import keywords as kw

        has_req = any(w in m for w in kw.PRODUCT_REQUEST_WORDS)
        # قصيرة + طلب تسوق، أو تلميح منتج — لا نستدعي النموذج على «كيفك» العامة فقط
        return _text_has_product_hint(m) or (len(m) < 52 and has_req)
    return False


def _merge_search_analysis_into_query(original: str, data: Dict[str, Any]) -> str:
    """يدمج الحقول المستخرجة مع التحقق من الظهور في نص المستخدم (لا اختراع)."""
    from logic.product_query_parse import normalize_for_product_search
    from logic.product_repository import _normalize_arabic_for_search as _norm_ar
    from logic.product_service import _keywords_grounded_in_user_text

    orig = (original or "").strip()
    ob = _norm_ar(normalize_for_product_search(orig))
    parts: List[str] = []

    cm = str(data.get("cleaned_message") or "").strip()
    if cm:
        nc = _norm_ar(normalize_for_product_search(cm))
        if len(nc) >= 2 and nc in ob:
            parts.append(cm)

    for field in ("product_type", "color", "gender"):
        v = str(data.get(field) or "").strip()
        if len(v) < 2:
            continue
        nv = _norm_ar(normalize_for_product_search(v))
        if len(nv) >= 2 and nv in ob and v not in parts:
            parts.append(v)

    raw_kw = data.get("keywords")
    if isinstance(raw_kw, str):
        raw_kw = [raw_kw] if raw_kw.strip() else []
    if not isinstance(raw_kw, list):
        raw_kw = []
    for k in _keywords_grounded_in_user_text(raw_kw, orig):
        if k and k not in " ".join(parts):
            parts.append(k)

    out = " ".join(parts).strip()
    if len(out) >= 2:
        return out
    return orig


def extract_search_analysis_openai(message: str) -> Optional[Dict[str, Any]]:
    """
    يستدعي OpenAI لاستخراج حقول بحث فقط. يعيد dict أو None عند الفشل.
    لا يُستخدم للرد على العميل.
    """
    if not is_openai_presearch_enabled():
        return None
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    user_text = (message or "").strip()[:_MAX_PRESEARCH_USER_CHARS]
    if len(user_text) < 2:
        return None

    model = (OPENAI_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"
    try:
        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=_MAX_PRESEARCH_COMPLETION_TOKENS,
            temperature=0.15,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SEARCH_ANALYSIS_SYSTEM},
                {"role": "user", "content": user_text},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        logger.exception("OpenAI pre-search analysis failed")
        return None


def enhance_search_query_with_openai(message: str, intent: str) -> str:
    """
    يحاول تحسين نص البحث قبل استدعاء قاعدة البيانات.
    عند الفشل أو التعطيل يُعيد الرسالة الأصلية دون تغيير.
    """
    orig = (message or "").strip()
    if not should_run_presearch_analysis(orig, intent):
        return orig
    parsed = extract_search_analysis_openai(orig)
    if not parsed:
        return orig
    merged = _merge_search_analysis_into_query(orig, parsed)
    return merged if merged else orig


def build_fallback_db_data(db: Any, message: str) -> Dict[str, Any]:
    """
    يجمع عيّنة صغيرة من الفروع + منتجات مطابقة لكلمات الرسالة + أقسام ذات صلة.
    لا يُحمّل كامل قاعدة البيانات.
    """
    msg = (message or "").strip()
    out: Dict[str, Any] = {"branches": [], "products": [], "sections": []}

    try:
        for b in db.get_all_branches() or []:
            cn = (b.get("city_name") or "").strip()
            if cn:
                out["branches"].append(cn)
    except Exception:
        pass

    raw = re.sub(r"[؟?!.,،]+", " ", msg)
    tokens = [t for t in raw.split() if len(t) >= 2][:8]
    if not tokens and msg:
        tokens = [msg[:48]]
    search_needles: List[str] = []
    seen_n = set()
    for t in tokens:
        for n in all_product_search_needles(t, msg):
            if len(n) >= 2 and n not in seen_n:
                seen_n.add(n)
                search_needles.append(n)
    if not search_needles:
        search_needles = [msg[:48]] if len(msg) >= 2 else []

    seen_ids: Set[Any] = set()
    for tok in search_needles[:12]:
        try:
            rows = db.search_products(tok, limit=6) or []
        except Exception:
            rows = []
        for r in rows:
            pid = r.get("product_id") or r.get("id")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            name = (r.get("product_name") or r.get("name") or "").strip()
            if not name:
                continue
            note = ((r.get("description") or "") or "")[:120]
            out["products"].append(
                {
                    "id": pid,
                    "name": name,
                    "price": r.get("price"),
                    "note": note,
                }
            )
            if len(out["products"]) >= 15:
                break
        if len(out["products"]) >= 15:
            break

    try:
        secs = db.get_sections_by_name(msg) or []
        for s in secs[:10]:
            out["sections"].append(
                {
                    "section": s.get("section_name"),
                    "category": s.get("category_name"),
                    "branch": s.get("branch_city_name"),
                }
            )
    except Exception:
        pass

    return out


def generate_ai_response(message: str, db_data: Dict[str, Any]) -> Optional[str]:
    """
    يستدعي OpenAI مرة واحدة ويعيد نص الرد أو None عند التعذر.
    """
    if not is_ai_fallback_enabled():
        return None
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    model = (OPENAI_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"
    payload = {
        "customer_message": (message or "").strip(),
        "database_context": db_data or {},
    }
    user_text = json.dumps(payload, ensure_ascii=False)
    if len(user_text) > _MAX_CONTEXT_CHARS:
        user_text = user_text[: _MAX_CONTEXT_CHARS] + "…"

    try:
        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=_MAX_COMPLETION_TOKENS,
            temperature=0.35,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": user_text,
                },
            ],
        )
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        return content or None
    except Exception:
        logger.exception("OpenAI chat fallback failed")
        return None
