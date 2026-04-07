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

_INTENT_CLASSIFIER_ENV = "OPENAI_INTENT_CLASSIFIER"
_MAX_INTENT_CLASSIFY_TOKENS = 120

_UNKNOWN_ROUTE_SYSTEM = """أنت مصنّف رسائل فقط لمتجر تجزئة (عربي). أعد JSON فقط بهذا الشكل:
{"route":"product"|"location"|"complaint"|"unclear"}

معنى route:
- product: طلب عرض أو شراء أو سعر أو توفر لصنف (ملابس، حقيبة، لون، مقاس، …).
- location: عنوان فرع، موقع، خرائط، وين الفرع، في أي مدينة، دوام/ساعات الفرع.
- complaint: شكوى، سوء خدمة، خطأ بالطلب، رفض استرجاع، تعويض.
- unclear: ترحيب عام فقط، أو نص لا يُصنَّف بوضوح.

لا تخترع معلومات؛ اعتمد على صياغة المستخدم فقط."""

ORCHESTRATOR_ENV = "OPENAI_CHAT_ORCHESTRATOR"
_MAX_ORCHESTRATOR_TOKENS = 900

_ORCHESTRATOR_SYSTEM = """أنت منسّق محادثة متجر أزياء في السعودية. تتكلم بلهجة سعودية بسيطة وطبيعية (مثل موظف مبيعات)، بعيد عن الصياغة الروبوتية.
استخدم أحياناً: «أنصحك بـ…»، «ممكن يناسبك…»، «جرّب تطّلع على…» — بدون مبالغة.

المصدر الوحيد لأسماء الأقسام والمنتجات: database_context — ممنوع اقتراح قسم أو منتج غير موجود في العينة.
لا تخترع أسعاراً ولا توفراً.

أعد JSON فقط بهذا الشكل:
{
  "action": "product_search" | "category_suggestion" | "branch_request" | "general_response" | "complaint" | "return_policy",
  "filters": {
    "search_query": "نص عربي للبحث — من كلمات العميل فقط",
    "gender": "male" | "female" | "",
    "suggested_categories": ["أسماء من database_context.categories_sample فقط، أو []"]
  },
  "context": {
    "occasion": "wedding" | "gift" | "daily" | "formal" | null,
    "target": "self" | "other" | null,
    "style": "casual" | "formal" | "luxury" | null
  },
  "message": "رد قصير وواضح وعملي؛ سؤال واحد في النهاية إن لزم.",
  "needs_branch": true أو false,
  "needs_details": true أو false
}

عند product_search ووجود مناسبة في الرسالة: اجعل message ترشيحياً (لماذا يناسب العميل)، لا وصفاً تقنياً للقائمة.
database_context.extracted_user_context: سياق مستخرج قواعدياً — التزم به ولا تتجاهله.

أولوية الإجراء (إلزامية):
- إذا كان واضحاً أن العميل يتسوّق (يبغى/أبغى/عرض/سعر/متوفر/هدية/ملابس/مقاس/لون…): action يجب أن يكون product_search وليس general_response.
- category_suggestion فقط عندما لا يمكن استنتاج بحث منتج معقول، أو بعد افتراض أن البحث لن يجد شيئاً — ولا تقترح أقساماً خارج العينة أو خارج سياق الرسالة (مثلاً لا تقترح مفروشات إذا طلب ملابس).
- general_response: ترحيب صافٍ، شكر، أو استفسار لا علاقة له بالتسوق.

الجنس (filters.gender):
- إذا قال العميل إن المنتج له (لي، لنفسي، أنا…) وليس هناك دليل أنثوي صريح → غالباً male.
- كلمات مثل: زوجتي، أختي، بنتي، أمي، هدية لها → female.
- إن وُجد رجالي/نسائي صريح في الرسالة فاتبعها.

الشكوى (complaint):
- إذا action = complaint: اضبط needs_branch = true إذا لم يُذكر فرع واضح؛ needs_details = true إذا النص قصير جداً أو ناقص.
- message يطلب الفرع وتفاصيل المشكلة بلطف، ويستخدم اسم العميل إن وُجد في السياق (إن وُجد في الرسالة).

الفروع:
- branch_request فقط لطلب موقع/مدينة/تواصل بدون طلب منتج.

اقتراح الأقسام:
- suggested_categories: فقط من categories_sample ويجب أن يكون لها صلة بكلمات العميل أو الجنس المستنتج — لا تملأ بأقسام عشوائية."""


def infer_gender_from_message(message: str) -> Optional[str]:
    """
    استنتاج خفيف للجنس دون تعديل product_service: male | female | None.
    قواعد المستخدم: لي/لنفسي/أنا → رجال افتراضياً؛ زوجتي/أختي/… → نسائي.
    """
    from logic.product_query_parse import extract_gender_filter

    t = (message or "").strip()
    if not t:
        return None
    explicit = extract_gender_filter(t)
    if explicit:
        return explicit
    tl = t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    tl_l = tl.lower()

    female_for_others = (
        "زوجتي",
        "زوجي",
        "اخت",
        "أخت",
        "اختي",
        "بنتي",
        "بنت",
        "امي",
        "أمي",
        "امها",
        "أمها",
        "خواتي",
        "اخواتي",
        "أخواتي",
        "بناتي",
        "لها",
        "هديه لها",
        "هدية لها",
        "هديه لزوجتي",
        "هدية لزوجتي",
    )
    for ph in female_for_others:
        if ph in t or ph in tl:
            return "female"

    female_explicit = ("نسائي", "نساء", "حريمي", "سيدات", "بناتي")
    if any(x in t for x in female_explicit):
        return "female"

    male_self_markers = (
        "لنفسي",
        "لنفسى",
        "نفسي",
        "نفسى",
        "ملابس لي",
        "ابغى ملابس لي",
        "أبغى ملابس لي",
        "ابغي ملابس لي",
        "لي ",
        " لي",
        "لي،",
        "لي؟",
        "ليا",
    )
    if any(x in t or x in tl for x in male_self_markers):
        return "male"
    if re.search(r"(^|[\s،])لي([\s،؟!]|$)", t):
        return "male"
    if tl_l.startswith("انا ") or tl_l.startswith("أنا ") or " انا " in f" {tl_l} ":
        if not any(x in t for x in female_explicit):
            return "male"

    return None


def extract_user_context(message: str) -> Dict[str, Optional[str]]:
    """
    سياق تسوق: مناسبة، لمن، أسلوب — يُستخدم في البحث والرسائل دون تعديل المستودعات.
    """
    out: Dict[str, Optional[str]] = {
        "occasion": None,
        "target": None,
        "style": None,
    }
    t = (message or "").strip()
    if not t:
        return out
    tl = t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    tl_l = tl.lower()

    other_markers = (
        "لزوجتي",
        "لزوجي",
        "لأمي",
        "لامي",
        "لأختي",
        "لاختي",
        "لبنتي",
        "لبنت",
        "هدية ل",
        "هديه ل",
        "أهدي",
        "اهدي",
        "لها",
        "لأبي",
        "لابي",
        "لأخوي",
        "لاخوي",
    )
    self_markers = (
        "لنفسي",
        "لنفسى",
        "نفسي",
        "نفسى",
        "ملابس لي",
        "ابغى ملابس لي",
        "أبغى ملابس لي",
        "ابغي ملابس لي",
    )
    if any(x in t for x in self_markers) or re.search(r"(^|[\s،])لي([\s،؟!]|$)", t):
        out["target"] = "self"
    elif any(x in t for x in other_markers):
        out["target"] = "other"
    elif tl_l.startswith("انا ") or tl_l.startswith("أنا ") or " انا " in f" {tl_l} ":
        out["target"] = "self"

    if any(x in t for x in ("زواج", "لزواج", "عرس", "مناسبة زواج")):
        out["occasion"] = "wedding"
    elif any(x in t for x in ("هدية", "هديه", "هدايا", "أهدي", "اهدي")):
        out["occasion"] = "gift"
    elif any(x in t for x in ("يومي", "يومية", "يوميه", "استخدام يومي", "لبس يومي")):
        out["occasion"] = "daily"
    elif any(x in t for x in ("مناسبة رسمية", "اجتماع رسمي", "شغل رسمي")):
        out["occasion"] = "formal"

    if any(x in t for x in ("فخم", "فاخر", "فخامة", "لكس", "لوكس")):
        out["style"] = "luxury"
    elif any(x in t for x in ("رسمي", "أنيق", "مناسب رسمي")):
        out["style"] = "formal"
    elif any(x in t for x in ("كاجوال", "كاجوال ", "طلعة", "طلعه", "رياضي", "شيك عادي")):
        out["style"] = "casual"
    elif any(x in t for x in ("يومي", "يومية")) and out["style"] is None:
        out["style"] = "casual"

    if out["occasion"] == "daily" and out["style"] is None:
        out["style"] = "casual"
    if out["occasion"] == "wedding" and out["style"] is None:
        out["style"] = "formal"
    if out["occasion"] == "formal" and out["style"] is None:
        out["style"] = "formal"

    return out


def merge_user_context_into_plan(message: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """يدمج extract_user_context مع أي context يعيده النموذج — يُفضَّل المستخرج قواعدياً ثم النموذج."""
    extracted = extract_user_context(message)
    model_ctx = plan.get("context") if isinstance(plan.get("context"), dict) else {}

    def _pick(key: str, valid: Set[str]) -> Optional[str]:
        a = extracted.get(key)
        b = model_ctx.get(key)
        if a in valid:
            return str(a)
        if b in valid:
            return str(b)
        return None

    merged = {
        "occasion": _pick("occasion", {"wedding", "gift", "daily", "formal"}),
        "target": _pick("target", {"self", "other"}),
        "style": _pick("style", {"casual", "formal", "luxury"}),
    }
    out = dict(plan)
    out["context"] = merged
    return out


def _message_tokens(message: str) -> Set[str]:
    raw = re.sub(r"[؟?!.,،]+", " ", (message or "").strip())
    return {x for x in raw.split() if len(x) >= 2}


def _category_name_matches_message(cat_name: str, message: str) -> bool:
    cn = (cat_name or "").strip()
    if not cn:
        return False
    mt = _message_tokens(message)
    ct = _message_tokens(cn)
    if mt & ct:
        return True
    for m in mt:
        if len(m) >= 3 and m in cn:
            return True
    return False


def _category_gender_mismatch(cat_name: str, gender: Optional[str]) -> bool:
    """True = يجب استبعاد القسم (تعارض واضح مع الجنس المطلوب)."""
    if gender not in ("male", "female"):
        return False
    c = (cat_name or "").strip()
    if not c:
        return False
    has_m = any(x in c for x in ("رجالي", "رجال", "ولادي", "اولادي"))
    has_f = any(x in c for x in ("نسائي", "نساء", "سيدات", "حريمي", "بناتي"))
    if gender == "male":
        return bool(has_f and not has_m)
    return bool(has_m and not has_f)


def _category_names_from_product_hits(db: Any, message: str, limit: int = 14) -> List[str]:
    msg = (message or "").strip()
    if len(msg) < 2:
        return []
    raw = re.sub(r"[؟?!.,،]+", " ", msg)
    tokens = [t for t in raw.split() if len(t) >= 2][:5]
    if not tokens:
        tokens = [msg[:40]]
    seen: Set[str] = set()
    out: List[str] = []
    for tok in tokens:
        try:
            rows = db.search_products(tok, limit=10) or []
        except Exception:
            rows = []
        for r in rows:
            c = (r.get("category_name") or "").strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
                if len(out) >= limit:
                    return out
    return out


def _category_context_score(cat_name: str, user_ctx: Optional[Dict[str, Any]]) -> int:
    """رفع أقسام تلائم مناسبة/أسلوب العميل (بدون تغيير قاعدة البيانات)."""
    if not user_ctx:
        return 0
    n = cat_name or ""
    s = 0
    occ = user_ctx.get("occasion")
    sty = user_ctx.get("style")
    if occ == "wedding" or sty in ("formal", "luxury"):
        s += sum(1 for h in ("رسمي", "سهرة", "فساتين", "كلاسيك", "أنيق", "سهر") if h in n)
    if occ == "gift":
        s += sum(1 for h in ("هد", "حقيب", "إكسسو", "عطر", "مستحضر") if h in n)
    if occ == "daily" or sty == "casual":
        s += sum(1 for h in ("يومي", "كاجوال", "قطن", "رياضي", "تشيرت") if h in n)
    if sty == "luxury":
        s += sum(1 for h in ("فخم", "فاخر", "تركي") if h in n)
    return s


def filter_categories_for_context(
    all_names: List[str],
    message: str,
    gender: Optional[str],
    product_category_hits: List[str],
    user_ctx: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    يقيّد أسماء الأقسام المرسلة للنموذج: صلة بالرسالة أو الجنس أو نتائج بحث المنتجات.
    """
    ph_set = set(product_category_hits or [])
    out: List[str] = []
    seen: Set[str] = set()
    for raw in product_category_hits:
        n = (raw or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    for name in all_names:
        n = (name or "").strip()
        if not n or n in seen:
            continue
        if _category_gender_mismatch(n, gender):
            continue
        if n in ph_set:
            out.append(n)
            seen.add(n)
            continue
        if _category_name_matches_message(n, message):
            out.append(n)
            seen.add(n)
            continue
        if gender == "male" and any(x in n for x in ("رجالي", "رجال", "ولادي")):
            out.append(n)
            seen.add(n)
            continue
        if gender == "female" and any(x in n for x in ("نسائي", "نساء", "سيدات", "حريمي")):
            out.append(n)
            seen.add(n)
            continue
    if user_ctx and out:
        out = sorted(
            out,
            key=lambda x: (-_category_context_score(x, user_ctx), x),
        )
    return out[:28]


def apply_gender_to_search_query(text: str, gender: Optional[str]) -> str:
    """يضيف رجالي/نسائي ليتعرّف عليها extract_gender_filter في product_service دون تعديله."""
    from logic.product_query_parse import extract_gender_filter

    t = (text or "").strip()
    if not t or gender not in ("male", "female"):
        return t
    if extract_gender_filter(t):
        return t
    suffix = "رجالي" if gender == "male" else "نسائي"
    return f"{t} {suffix}".strip()


def apply_shopping_context_to_search_query(
    text: str,
    gender: Optional[str],
    user_ctx: Optional[Dict[str, Any]],
) -> str:
    """
    يوسّع نص البحث بالجنس + مناسبة/أسلوب (رسمي، كاجوال، هدايا…) دون تغيير المستودعات.
    """
    t = apply_gender_to_search_query(text, gender)
    if not user_ctx:
        return t
    occ = user_ctx.get("occasion")
    sty = user_ctx.get("style")

    def _add(term: str) -> None:
        nonlocal t
        if term and term not in t:
            t = f"{t} {term}".strip()

    if occ == "gift":
        _add("هدايا")
    if occ in ("wedding", "formal") or sty == "formal":
        _add("رسمي")
    if sty == "casual" or occ == "daily":
        _add("كاجوال")
    if sty == "luxury":
        _add("فخم")
    return t.strip()


def coerce_shopping_to_product_search(plan: Dict[str, Any], message: str) -> Dict[str, Any]:
    """إذا وُجدت إشارات تسوق واضحة لا تُرجع general_response."""
    from logic import keywords as kw

    action = str(plan.get("action") or "").strip().lower()
    if action not in ("general_response", "category_suggestion"):
        return plan
    m = (message or "").strip()
    if len(m) < 2:
        return plan
    has_req = any(w in m for w in kw.PRODUCT_REQUEST_WORDS)
    has_hint = any(
        (h or "").strip() and (h or "").strip() in m for h in kw.PRODUCT_HINTS if len((h or "").strip()) >= 2
    )
    has_rec = any(p in m for p in kw.RECOMMENDATION_PHRASES)
    if not (has_req or has_hint or has_rec):
        return plan
    out = dict(plan)
    out["action"] = "product_search"
    fl = dict(out.get("filters") or {})
    sq = str(fl.get("search_query") or "").strip()
    if len(sq) < 2:
        fl["search_query"] = m
    out["filters"] = fl
    return out


def merge_gender_into_plan(message: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """يدمج الجنس من الرسالة + مخرجات النموذج."""
    fl = dict(plan.get("filters") or {}) if isinstance(plan.get("filters"), dict) else {}
    ai_g = str(fl.get("gender") or "").strip().lower()
    hint = infer_gender_from_message(message)
    if ai_g in ("male", "female"):
        final = ai_g
    elif hint in ("male", "female"):
        final = hint
    else:
        final = None
    if final:
        fl["gender"] = final
    elif "gender" in fl:
        del fl["gender"]
    out = dict(plan)
    out["filters"] = fl
    return out


_RECOMMEND_MSG_ENV = "OPENAI_RECOMMENDATION_MSG"
_MAX_REC_MSG_TOKENS = 320
_RECOMMEND_MSG_SYSTEM = """أنت موظف مبيعات في متجر أزياء سعودي. العميل وجد نتائج منتجات حقيقية في القائمة.

اكتب فقرة واحدة قصيرة (3–5 جمل) بلهجة سعودية طبيعية:
- اربط الترشيح بسياق العميل (مناسبة زواج، هدية، يومي، رسمي، كاجوال، فخم) إن وُجد في user_context.
- اذكر سبباً واحداً على الأقل يجعل الخيار مناسباً للسياق (مظهر رسمي، مناسب كهدية، مريح لليومي…).
- لا تخترع أسماء منتجات جديدة؛ يمكنك الإشارة إلى «التصاميم الظاهرة» أو استخدام أسماء المنتجات المُمرَّرة فقط.
- تجنّب الصياغة الروبوتية مثل «تم العثور على نتائج».
أعد النص فقط دون عنوان ولا نقاط تقنية."""


def _template_recommendation_lines(
    user_context: Dict[str, Optional[str]],
    product_titles: List[str],
) -> List[str]:
    lines: List[str] = []
    occ = user_context.get("occasion")
    sty = user_context.get("style")
    tgt = user_context.get("target")
    if occ == "wedding":
        lines.append(
            "لمناسبة زواج، أنصحك تطّلع على التصاميم الظاهرة لأنها غالباً تعطي طابعاً رسمياً وأنيقاً يناسب الفعاليات."
        )
    elif occ == "gift":
        lines.append(
            "كهدية، اختر شيء يبان مرتب ومناسب للتقديم — التصاميم المعروضة غالباً تساعدك توصل الفكرة."
        )
    elif occ == "daily":
        lines.append("للبس اليومي، ركّز على الخيارات البسيطة والمريحة من المعروض.")
    elif occ == "formal" or sty == "formal":
        lines.append("للطابع الرسمي، الخيارات الظاهرة تساعدك تطلع بمظهر مرتب ومنسّق.")
    if sty == "casual":
        lines.append("ولو تبحث شيء كاجوال أو للطلعات الخفيفة، المناسب غالباً يكون بسيط ومريح.")
    if sty == "luxury":
        lines.append("ولو تدور شيء بمظهر فخم، ركّز على القصّة والخامة اللي تعجبك من القائمة.")
    if tgt == "other" and not lines:
        lines.append("لأن الطلب لشخص ثاني، تأكد من المقاس والستايل اللي يفضّله قبل التثبيت.")
    if product_titles[:2] and not lines:
        lines.append("اطّلع على: " + "، ".join(product_titles[:2]) + ".")
    return lines


def _template_recommendation_fallback(
    user_message: str,
    user_context: Dict[str, Optional[str]],
    product_titles: List[str],
    draft_message: str,
) -> str:
    parts = [draft_message.strip()] if draft_message and draft_message.strip() else []
    parts.extend(_template_recommendation_lines(user_context, product_titles))
    return "\n\n".join(p for p in parts if p).strip()


def enrich_product_recommendation_message(
    user_message: str,
    user_context: Dict[str, Optional[str]],
    draft_message: str,
    product_titles: List[str],
) -> str:
    """يرشّح بلهجة بشرية مع ذكر السياق — يعتمد على OpenAI مع قالب احتياطي."""
    uc = user_context or {}
    titles = [str(x).strip() for x in (product_titles or []) if x and str(x).strip()][:6]
    has_ctx = any(uc.get(k) for k in ("occasion", "target", "style"))
    fallback = _template_recommendation_fallback(
        user_message, uc, titles, draft_message or ""
    )
    if not has_ctx and not titles:
        return (draft_message or "").strip() or fallback

    if os.getenv(_RECOMMEND_MSG_ENV, "true").lower() not in ("1", "true", "yes"):
        return fallback or draft_message

    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return fallback or draft_message
    try:
        from openai import OpenAI
    except ImportError:
        return fallback or draft_message

    model = (OPENAI_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"
    payload = {
        "user_message": (user_message or "").strip()[:2000],
        "user_context": uc,
        "draft_assistant_message": (draft_message or "").strip()[:1200],
        "product_names_shown": titles,
    }
    user_txt = json.dumps(payload, ensure_ascii=False)
    try:
        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=_MAX_REC_MSG_TOKENS,
            temperature=0.38,
            messages=[
                {"role": "system", "content": _RECOMMEND_MSG_SYSTEM},
                {"role": "user", "content": user_txt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if len(text) < 12:
            return fallback or draft_message
        return text
    except Exception:
        logger.exception("OpenAI recommendation message enrichment failed")
        return fallback or draft_message


def contextualize_no_product_message(
    ai_msg: str,
    user_message: str,
    user_context: Dict[str, Optional[str]],
    category_names: List[str],
) -> str:
    """يضيف تلميحاً يتناسب مع المناسبة عند عدم وجود منتجات."""
    base = (ai_msg or "").strip()
    uc = user_context or {}
    if not any(uc.get(k) for k in ("occasion", "target", "style")):
        return base
    extra_lines = _template_recommendation_lines(uc, [])
    cat_hint = ""
    if category_names:
        cat_hint = "جرب تتفرّج على: " + "، ".join(category_names[:6]) + "."
    tail_parts = extra_lines + ([cat_hint] if cat_hint else [])
    tail = "\n\n".join(tail_parts).strip()
    if not tail:
        return base
    return f"{base}\n\n{tail}".strip() if base else tail


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


def is_chat_orchestrator_enabled() -> bool:
    if not (OPENAI_API_KEY or "").strip():
        return False
    return os.getenv(ORCHESTRATOR_ENV, "true").lower() in ("1", "true", "yes")


def build_orchestrator_context(
    db: Any, message: str, dialect: Optional[str] = None
) -> Dict[str, Any]:
    """عيّنة فروع + أقسام مُقيّدة بالسياق + منتجات — بدون سرد كل أقسام قاعدة البيانات."""
    base = build_fallback_db_data(db, message)
    uctx = extract_user_context(message)
    gender_hint = infer_gender_from_message(message)
    cats_all: List[str] = []
    try:
        for m in db.get_main_categories() or []:
            n = (m.get("name") or "").strip()
            if n and n not in cats_all:
                cats_all.append(n)
    except Exception:
        pass
    prod_cats = _category_names_from_product_hits(db, message)
    filtered = filter_categories_for_context(
        cats_all, message, gender_hint, prod_cats, uctx
    )
    allowed = list(dict.fromkeys(filtered))
    if not allowed:
        allowed = list(dict.fromkeys(prod_cats))[:20]
    for s in base.get("sections") or []:
        c = (s.get("category") or "").strip()
        if c and c not in allowed and len(allowed) < 30:
            allowed.append(c)
    allowed = list(dict.fromkeys(allowed))[:28]

    return {
        "branches": base.get("branches") or [],
        "categories_sample": allowed,
        "categories_allowed": list(dict.fromkeys(allowed)),
        "product_category_hits": list(dict.fromkeys(prod_cats)),
        "gender_hint": gender_hint,
        "extracted_user_context": uctx,
        "user_dialect": dialect or "default",
        "products_sample": base.get("products") or [],
        "sections_sample": base.get("sections") or [],
    }


def _category_acceptable_for_suggestion(
    cname: str,
    message: str,
    gender: Optional[str],
    hits: Set[str],
    allowed: Set[str],
) -> bool:
    c = (cname or "").strip()
    if c not in allowed:
        return False
    if c in hits:
        return True
    if _category_gender_mismatch(c, gender):
        return False
    if _category_name_matches_message(c, message):
        return True
    if gender == "male" and any(x in c for x in ("رجالي", "رجال", "ولادي", "اولادي")):
        return True
    if gender == "female" and any(
        x in c for x in ("نسائي", "نساء", "سيدات", "بناتي", "حريمي")
    ):
        return True
    return False


def pick_fallback_categories(
    context: Dict[str, Any], message: str, gender: Optional[str]
) -> List[str]:
    """أقسام احتياطية عند عدم وجود منتجات — من السياق المُصفّى فقط."""
    allowed = set(context.get("categories_allowed") or context.get("categories_sample") or [])
    hits = set(context.get("product_category_hits") or [])
    uctx = context.get("extracted_user_context") or {}
    cand = [
        c
        for c in allowed
        if _category_acceptable_for_suggestion(c, message, gender, hits, allowed)
    ]
    if uctx and cand:
        cand = sorted(
            cand,
            key=lambda x: (-_category_context_score(x, uctx), x),
        )
    return cand[:8]


def normalize_orchestrator_plan(
    plan: Dict[str, Any], context: Dict[str, Any], message: str
) -> Dict[str, Any]:
    """يُصفّي الأقسام والجنس وحقول الشكوى ضمن بيانات حقيقية."""
    allowed = set(context.get("categories_allowed") or context.get("categories_sample") or [])
    hits = set(context.get("product_category_hits") or [])
    fl = plan.get("filters") if isinstance(plan.get("filters"), dict) else {}
    raw = fl.get("suggested_categories") or []
    if isinstance(raw, str):
        raw = [raw] if raw.strip() else []
    if not isinstance(raw, list):
        raw = []
    g = str(fl.get("gender") or "").strip().lower()
    gender = g if g in ("male", "female") else None
    cleaned = [c for c in raw if isinstance(c, str) and c.strip() and c.strip() in allowed]
    cleaned = [
        c
        for c in cleaned
        if _category_acceptable_for_suggestion(c, message, gender, hits, allowed)
    ]
    uctx = context.get("extracted_user_context") or {}
    if uctx and cleaned:
        cleaned = sorted(
            cleaned,
            key=lambda x: (-_category_context_score(x, uctx), x),
        )
    fl = dict(fl)
    fl["suggested_categories"] = cleaned[:8]
    if gender:
        fl["gender"] = gender
    elif "gender" in fl:
        del fl["gender"]
    plan = dict(plan)
    plan["filters"] = fl
    act = str(plan.get("action") or "").strip().lower()
    if act == "complaint":
        if "needs_branch" not in plan:
            plan["needs_branch"] = True
        if "needs_details" not in plan:
            plan["needs_details"] = True
    return plan


def run_chat_orchestrator_openai(message: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    محرك القرار الرئيسي: يعيد خطة JSON (action + filters + message) أو None.
    """
    if not is_chat_orchestrator_enabled():
        return None
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    payload = {
        "customer_message": (message or "").strip()[:4000],
        "database_context": context,
    }
    user_text = json.dumps(payload, ensure_ascii=False)
    if len(user_text) > _MAX_CONTEXT_CHARS:
        user_text = user_text[:_MAX_CONTEXT_CHARS] + "…"
    model = (OPENAI_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"
    try:
        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=_MAX_ORCHESTRATOR_TOKENS,
            temperature=0.28,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _ORCHESTRATOR_SYSTEM},
                {"role": "user", "content": user_text},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        action = str(data.get("action") or "").strip().lower()
        allowed_actions = frozenset(
            {
                "product_search",
                "category_suggestion",
                "branch_request",
                "general_response",
                "complaint",
                "return_policy",
            }
        )
        if action not in allowed_actions:
            return None
        plan = merge_gender_into_plan(message, data)
        plan = merge_user_context_into_plan(message, plan)
        plan = normalize_orchestrator_plan(plan, context, message)
        plan = coerce_shopping_to_product_search(plan, message)
        return plan
    except Exception:
        logger.exception("OpenAI chat orchestrator failed")
        return None


def classify_unknown_intent_openai(message: str) -> Optional[str]:
    """
    عند فشل القواعد (unknown): يصنّف OpenAI المسار المقترح فقط — لا يُعرض للمستخدم.
    يعيد: product | location | complaint | None (unclear أو تعطيل أو خطأ).
    """
    if os.getenv(_INTENT_CLASSIFIER_ENV, "true").lower() not in ("1", "true", "yes"):
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
            max_tokens=_MAX_INTENT_CLASSIFY_TOKENS,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _UNKNOWN_ROUTE_SYSTEM},
                {"role": "user", "content": user_text},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        route = str(data.get("route") or "").strip().lower()
        if route in ("product", "location", "complaint"):
            return route
        return None
    except Exception:
        logger.exception("OpenAI unknown-route classifier failed")
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
