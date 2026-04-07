# -*- coding: utf-8 -*-
"""
رد احتياطي عبر OpenAI عند فشل النظام القائم على القواعد في فهم الرسالة.
لا يُستدعى إلا من مسار الشات عند النية unknown أو عدم وجود منتجات مطابقة.
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
