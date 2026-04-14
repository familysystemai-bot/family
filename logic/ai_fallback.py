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

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_ORCH_DEBUG
from logic.chat_semantic_expand import all_product_search_needles
from site_config.company_policies import policies_text_for_ai_context

# حدّ استجابة النموذج (تقريباً كما طُلب)
_MAX_COMPLETION_TOKENS = 300
# حدّ حجم سياق JSON المُرسَل في البرومبت
_MAX_CONTEXT_CHARS = 7500

_SYSTEM_PROMPT = """أنت موظف خدمة عملاء حقيقي في متجر عائلة FAMILY، تتحدث بشكل طبيعي وبشري تماماً.

القواعد:
1. رد بجملة أو جملتين — قصير ومباشر دائماً
2. استخدم نفس لهجة العميل (سعودي/خليجي/مصري/يمني)
3. ممنوع هذي الكلمات الروبوتية: "وضّح"، "أرشّح"، "التقطت"، "لقطت"، "لم أتمكن"
4. لو ما فهمت: اسأل سؤال واحد قصير — مثل "تبغى وش؟" أو "قصدك؟"
5. المعلومات فقط من database_context — لا تخترع أسعار أو منتجات
6. لو المنتج غير موجود: "ما عندنا هذا حالياً" — لا تقل "عندنا" وبعدين "ما عندنا"
7. لو سؤال عن توصيل/سياسات وما في معلومات: "ما عندي تفاصيل، تواصل مع الفرع"

أمثلة:
× "قولي وش تبغى عشان أساعدك" — روبوتي ممنوع
✓ "تبغى وش بالضبط؟" — طبيعي

× "في أكثر من قسم قريب من طلبك" — هبد ممنوع
✓ "عندنا ملابس رجالي، تبغى كاجوال ولا رسمي؟" — طبيعي

هدفك: رد بشري طبيعي كأنك موظف واتساب حقيقي."""

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
# عيّن OPENAI_ORCH_DEBUG=true على Render لطباعة تشخيصات stdout (الأسطر المطلوبة أدناه).
_ORCH_DEBUG_ENV = "OPENAI_ORCH_DEBUG"
_MAX_ORCHESTRATOR_TOKENS = 900
_ORCH_ENV_LOGGED_ONCE = False
_ORCH_DISABLED_WARNED = False

_ORCHESTRATOR_SYSTEM = """أنت موظف خدمة عملاء حقيقي في منسّق محادثة متجر أزياء؛ تتحدث بشكل طبيعي ومباشر، بعيداً عن الصياغة الروبوتية.

قواعد إلزامية لحقل message (وللصياغة عموماً):
- جملة أو جملتان فقط؛ لا إطالة ولا شرح زائد.
- رد فقط على طلب العميل؛ لا معلومات لم تُطلب ولا مواضيع جانبية.
- نفس لهجة العميل إن تبيّنت (سعودي / مصري / خليجي / فصحى)، وإلا خليجية بسيطة.
- أسلوب بشري (مو رسمي بزيادة)، بدون قوائم طويلة أو فقرات أو تكرار.
- معلومات المنتجات والأقسام والفروع فقط من database_context؛ لا تخمن ولا تخترع منتجات أو أقساماً غير موجودة في العينة.
- عند عدم اليقين: لا تقل "وضّح" أو "وضح لي" أبداً. بدلاً من ذلك قل "لحظة، بأكد لك من الفرع 🙏" واجعل action = "product_search" ليتم التصعيد للفرع.
- ممنوع نهائياً هذه العبارات: "وضّح لي"، "وضح لي"، "ما لقطت عليك"، "ما لقيت هذا"، "ما لقيت المنتج"، "وش تدور"، "جرب تشوف"، "تتفرج على"، "أقسام قريبة من طلبك"، "أقسام قد تناسبك".
- ممنوع تماماً: ذكر بطء النظام أو أعطال تقنية أو "جرّب بعد لحظة" — لا تخترع أعذاراً تقنية أبداً.
- ممنوع تماماً: قول "ما لقيت" أو "لا يوجد" أو "غير موجود" في حقل message — بدلاً من ذلك اجعل action = "product_search" وسيتولى النظام التصعيد للفرع تلقائياً.
- ممنوع «الهبد»: لا اقتراح أقسام عشوائية بعيدة عن طلب العميل (مثل أقسام لا صلة لها بالرسالة).
- لا تذكر فروعاً أو مدناً أو مواقع أو دواماً إلا إذا سأل العميل عن الفرع/الموقع/الأقرب/الزيارة صراحة — **استثناء**: إذا كان الرد مأخوذاً حصراً من database_context.company_info (نص رسمي مُدخل من الإدارة) فيُسمح بذكر ما ورد هناك.

database_context.company_info (سياسات وخدمات رسمية):
- المصدر الوحيد للإجابة عند سؤال العميل عن: التوصيل، الشحن، الاسترجاع، الدفع، الدوام، معلومات عامة عن الفروع، أو «خدمات» فرع معيّن — إن وُجدت في الحقول أو في branch_services.
- لا تخترع شروطاً أو مدداً أو طرق دفع؛ إذا كان الحقل فارغاً أو لا يغطي السؤال: قل «حالياً ما عندنا توصيل، تواصل مع الفرع مباشرة للاستفسار» — ثم اضبط action على general_response لا category_suggestion.
- عند سؤال التوصيل: لا تقترح أقسام أو منتجات أبداً — action يجب أن يكون general_response فقط.
- أجب باختصار شديد؛ لا تكرر سياسات كاملة إلا إذا طلب العميل صراحة تلخيصاً.

يمكنك أحياناً صيغاً قصيرة مثل: «ممكن يناسبك…»، «جرّب…» — بلا مبالغة.

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
  "message": "جملة أو جملتان فقط؛ رد واضح وعملي؛ سؤال توضيحي واحد في النهاية إن لزم.",
  "needs_branch": true أو false,
  "needs_details": true أو false
}

عند product_search ووجود مناسبة في الرسالة: اجعل message ترشيحياً باختصار (لماذا قد يناسب العميل) ضمن جملة أو جملتين، لا وصفاً تقنياً للقائمة.
database_context.extracted_user_context: سياق مستخرج قواعدياً — التزم به ولا تتجاهله.

إن وُجد intent_scoring في database_context:
- هو تلميح من نظام نقاط داخلي (كلمات مفتاحية + أوزان) وليس حقائق من قاعدة البيانات.
- استخدمه لتحسين اختيار action والصياغة فقط؛ لا تعتمد عليه وحدَه إذا كان needs_clarification = true أو كانت النقاط متقاربة.
- possible_intents يوضح ترتيب التخمين المحلي (product / branch / complaint).

أولوية الإجراء (إلزامية):
- إذا كان واضحاً أن العميل يتسوّق (يبغى/أبغى/عرض/سعر/متوفر/هدية/ملابس/مقاس/لون…): action يجب أن يكون product_search وليس general_response.
- إذا سأل العميل عن التوصيل أو الشحن أو يوصلون أو توصلون أو يصلهم: action يجب أن يكون general_response فقط — ممنوع category_suggestion أو product_search.
- إذا سأل عن سياسات (استرجاع/دفع/دوام): action يجب أن يكون general_response.
- إذا كانت رسالة العميل مجرد "نعم" أو "ايوه" أو موافقة قصيرة بعد سؤال من البوت: action يجب general_response مع سؤال توضيحي — لا تبحث عن منتج.
- category_suggestion فقط عندما لا يمكن استنتاج بحث منتج معقول، أو بعد افتراض أن البحث لن يجد شيئاً — ولا تقترح أقساماً خارج العينة أو خارج سياق الرسالة.
- general_response: ترحيب صافٍ، شكر، سؤال عن خدمة، أو استفسار لا علاقة له بالتسوق.
- ممنوع تماماً: قول "ما عندنا X" إذا كان النظام قال في رسالة سابقة "عندنا X" — تناقض يُسيء للتجربة.

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


def merged_turn_text_for_shopping(current_message: str) -> str:
    """
    يدمج الرسالة السابقة مع الحالية عند متابعة سياق التسوق (مثلاً «نساء» ثم «ملابس سباحة»).
    يعتمد على session['chat_last_incoming_message'] المُحدَّث بعد كل رد.
    """
    try:
        from flask import has_request_context, session
    except Exception:
        return (current_message or "").strip()
    if not has_request_context():
        return (current_message or "").strip()
    cur = (current_message or "").strip()
    prev = (session.get("chat_last_incoming_message") or "").strip()
    if not prev or prev == cur:
        return cur
    if len(cur) > 120:
        return cur
    prev_use = prev[-400:] if len(prev) > 400 else prev
    merged = f"{prev_use}\n{cur}"
    g_c = infer_gender_from_message(cur)
    g_m = infer_gender_from_message(merged)
    if g_m in ("male", "female") and g_c is None:
        return merged
    li = (session.get("chat_last_intent") or "").strip()
    ci = (session.get("chat_current_intent") or "").strip()
    in_flow = ci == "product" or li in ("product", "recommendation", "section")
    if in_flow and len(cur) <= 72:
        return merged
    return cur


def infer_gender_for_product_turn(message: str) -> Optional[str]:
    """استنتاج الجنس مع احتساب الرسالة السابقة عند الحاجة."""
    return infer_gender_from_message(merged_turn_text_for_shopping(message))


def extract_user_context_for_product_turn(message: str) -> Dict[str, Optional[str]]:
    """سياق التسوق مع دمج الرسالة السابقة عند الحاجة."""
    return extract_user_context(merged_turn_text_for_shopping(message))


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
    hint = infer_gender_for_product_turn(message)
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
    """
    كانت تضيف "جرب تتفرّج على..." — محذوف لأنه يعطي اقتراحات عشوائية غير مفيدة.
    الردود تُبنى على بيانات حقيقية أو تصعيد للفرع.
    """
    return (ai_msg or "").strip()


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


def _orchestrator_env_allowed() -> bool:
    """نفس شرط التشغيل الفعلي — يُستخدم للتشخيص دون تعارض."""
    if not (OPENAI_API_KEY or "").strip():
        return False
    v = os.getenv(ORCHESTRATOR_ENV, "true")
    return str(v).strip().lower() in ("1", "true", "yes")


def _orch_debug_prints() -> None:
    """سجلات تفصيلية عند OPENAI_ORCH_DEBUG (من config) أو متغير البيئة القديم."""
    if not OPENAI_ORCH_DEBUG and os.getenv(_ORCH_DEBUG_ENV, "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return
    logger.debug(
        "orch debug: key=%s ORCH_ENV=%s enabled=%s model=%s",
        bool(os.getenv("OPENAI_API_KEY")),
        os.getenv(ORCHESTRATOR_ENV, "true"),
        is_chat_orchestrator_enabled(),
        OPENAI_MODEL,
    )


def _log_orchestrator_env_once() -> None:
    """سطر واحد في السجل لكل عملية (Render logs) — أول استدعاء للمنسّق فقط."""
    global _ORCH_ENV_LOGGED_ONCE
    if _ORCH_ENV_LOGGED_ONCE:
        return
    _ORCH_ENV_LOGGED_ONCE = True
    logger.info(
        "openai_orchestrator env: OPENAI_API_KEY set=%s %s=%r MODEL=%r enabled=%s",
        bool(os.getenv("OPENAI_API_KEY")),
        ORCHESTRATOR_ENV,
        os.getenv(ORCHESTRATOR_ENV, "true"),
        OPENAI_MODEL,
        _orchestrator_env_allowed(),
    )


def is_chat_orchestrator_enabled() -> bool:
    global _ORCH_DISABLED_WARNED
    ok = _orchestrator_env_allowed()
    if not ok and not _ORCH_DISABLED_WARNED:
        _ORCH_DISABLED_WARNED = True
        if not (OPENAI_API_KEY or "").strip():
            logger.warning(
                "ORCHESTRATOR DISABLED: OPENAI_API_KEY missing or empty (check Render Environment)"
            )
        else:
            logger.warning(
                "ORCHESTRATOR DISABLED: %s=%r — use string true/1/yes (not empty)",
                ORCHESTRATOR_ENV,
                os.getenv(ORCHESTRATOR_ENV, "true"),
            )
    return ok


def build_orchestrator_context(
    db: Any,
    message: str,
    dialect: Optional[str] = None,
    intent_decision: Optional[Dict[str, Any]] = None,
    rule_findings: Optional[Dict[str, Any]] = None,
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

    out: Dict[str, Any] = {
        "branches": base.get("branches") or [],
        "categories_sample": allowed,
        "categories_allowed": list(dict.fromkeys(allowed)),
        "product_category_hits": list(dict.fromkeys(prod_cats)),
        "gender_hint": gender_hint,
        "extracted_user_context": uctx,
        "user_dialect": dialect or "default",
        "products_sample": base.get("products") or [],
        "sections_sample": base.get("sections") or [],
        "company_info": base.get("company_info") or {},
    }
    if intent_decision:
        snap = intent_decision.get("score_snapshot") or {}
        scores = snap.get("scores") or intent_decision.get("scores") or {}
        out["intent_scoring"] = {
            "message": (message or "").strip()[:2000],
            "detected_keywords": snap.get("detected_keywords")
            or intent_decision.get("detected_keywords")
            or {},
            "scores": scores,
            "possible_intents": snap.get("possible_intents")
            or intent_decision.get("possible_intents")
            or [],
            "top_score": snap.get("top_score", intent_decision.get("top_score")),
            "routing_route": intent_decision.get("route"),
            "needs_clarification": intent_decision.get("needs_clarification"),
            "score_intent_guess": intent_decision.get("score_intent"),
        }
    if rule_findings:
        out["rule_findings"] = dict(rule_findings)
    try:
        out["store_policies_summary"] = policies_text_for_ai_context(3400)
    except Exception:
        out["store_policies_summary"] = ""
    return out


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


def _print_openai_orchestrator_runtime_env() -> None:
    """تشخيص اختياري عند OPENAI_ORCH_DEBUG فقط."""
    if not OPENAI_ORCH_DEBUG:
        return
    logger.debug(
        "orchestrator env: OPENAI_API_KEY=%s ORCH=%s MODEL=%s RENDER=%s",
        bool(os.getenv("OPENAI_API_KEY")),
        os.getenv(ORCHESTRATOR_ENV, "true"),
        os.getenv("OPENAI_MODEL"),
        os.getenv("RENDER"),
    )


_MAX_COMPLAINT_POLICY_TOKENS = 520


def enrich_complaint_policy_interaction(
    issue_text: str, step_name: str = ""
) -> Optional[Dict[str, str]]:
    """
    تصنيف عربي للشكوى + فقرة رد ودود عند الصلة بسياسة الاستبدال/الاسترجاع (من site_config).
    لا يوقف جمع الفرع/التفاصيل — نص إضافي للمحادثة فقط.
    """
    raw = (issue_text or "").strip()
    if len(raw) < 8:
        return None
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    try:
        pol = policies_text_for_ai_context(2800)
    except Exception:
        pol = ""
    user_instr = (
        "نص شكوى العميل:\n"
        f"{raw[:3500]}\n\n"
        f"مرحلة جمع البيانات في النظام: {step_name or 'غير محدد'}\n\n"
        "أعد JSON فقط بالمفاتيح:\n"
        '"classification_ar": نص عربي قصير (مثل: استرجاع، استبدال، تأخير، جودة منتج، تعامل موظف، أخرى)\n'
        '"policy_relevant": true أو false — true إذا كانت الشكوى تتعلق باستبدال أو استرجاع أو شروط السياسة أعلاه\n'
        '"reply_paragraph": إذا policy_relevant=true: فقرة واحدة تبدأ بإحدى عبارات مثل '
        "(العفو منك أستاذنا، حقك علينا)، ثم شرح بسيط للشرط المناسب من السياسة (مثل المهل بالأيام)، "
        "وتختم بصيغة مثل: لكن لعيونك بنرفع طلب استثنائي لمدير الفرع وبإذن الله ما يصير إلا اللي يرضيك. "
        'إذا policy_relevant=false اجعل reply_paragraph "" فارغاً.\n'
        "لا تخترع أرقاماً أو شروطاً غير واردة في مرجع السياسات."
    )
    try:
        client = OpenAI(api_key=key)
        model = (OPENAI_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"
        response = client.chat.completions.create(
            model=model,
            max_tokens=_MAX_COMPLAINT_POLICY_TOKENS,
            temperature=0.35,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "أنت مساعد خدمة عملاء متجر ملابس. التزم بمرجع السياسات التالي ولا تخترع شروطاً.\n\n"
                    + (pol or "(لا يوجد مرجع سياسات محمّل)"),
                },
                {"role": "user", "content": user_instr},
            ],
        )
        txt = (response.choices[0].message.content or "").strip()
        if not txt:
            return None
        data = json.loads(txt)
        if not isinstance(data, dict):
            return None
        out = {
            "classification_ar": str(data.get("classification_ar") or "").strip(),
            "reply_paragraph": str(data.get("reply_paragraph") or "").strip(),
        }
        pr = data.get("policy_relevant")
        if pr is False:
            out["reply_paragraph"] = ""
        return out
    except Exception:
        logger.exception("enrich_complaint_policy_interaction failed")
        return None


def friendly_orchestrator_fallback_plan(message: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    خطة آمنة عند تعطيل OpenAI أو فشل الاستدعاء — رد قواعدي بلهجة الجلسة (لا صمت).
    """
    d = (context or {}).get("user_dialect") or "default"
    from logic.dialect_responses import dialect_message
    from logic import chat_service as _cs

    text = dialect_message(d, "unknown_fallback", name=_cs._display_name())
    return {
        "action": "general_response",
        "message": text,
        "filters": {},
        "needs_branch": False,
    }


def _company_info_policy_reference_block(
    context: Dict[str, Any], max_chars: int = 3200
) -> str:
    """نص سياسات من database_context.company_info — المصدر الأسبق على ملف site_config."""
    ci = (context or {}).get("company_info") or {}
    if not isinstance(ci, dict):
        return ""
    parts: List[str] = []
    labels = {
        "delivery": "التوصيل والشحن",
        "returns": "الاسترجاع",
        "exchange": "الاستبدال",
        "hours": "الدوام وأوقات العمل",
        "payment": "طرق الدفع",
        "branches_blurb": "معلومات الفروع",
        "general": "معلومات عامة",
    }
    for k, lab in labels.items():
        v = (ci.get(k) or "").strip()
        if v:
            parts.append(f"{lab}:\n{v}")
    svc = ci.get("branch_services")
    if isinstance(svc, list) and svc:
        lines: List[str] = []
        for b in svc[:24]:
            if not isinstance(b, dict):
                continue
            bn = (b.get("branch_name") or "").strip()
            for s in (b.get("services") or [])[:16]:
                if not isinstance(s, dict):
                    continue
                tt = (s.get("title") or "").strip()
                dd = (s.get("details") or "").strip()
                if tt or dd:
                    chunk = f"- {bn}: {tt}" + (f" — {dd}" if dd else "")
                    lines.append(chunk)
        if lines:
            parts.append("خدمات الفروع:\n" + "\n".join(lines[:48]))
    text = "\n\n".join(parts).strip()
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def run_chat_orchestrator_openai(message: str, context: Dict[str, Any], history: list = None) -> Dict[str, Any]:
    """
    محرك القرار الرئيسي: يعيد خطة JSON (action + filters + message).
    عند تعطيل المفتاح أو الخطأ يُعاد دائماً خطة general_response ودية (لا None).
    """
    _print_openai_orchestrator_runtime_env()
    _log_orchestrator_env_once()
    if OPENAI_ORCH_DEBUG:
        _orch_debug_prints()

    fallback = friendly_orchestrator_fallback_plan(message, context)

    if not is_chat_orchestrator_enabled():
        logger.info("orchestrator: using rule-based fallback (disabled or missing key)")
        return fallback
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        logger.info("orchestrator: using rule-based fallback (empty API key)")
        return fallback
    try:
        from openai import OpenAI
    except ImportError as e:
        logger.error("OpenAI SDK not installed: %s", e)
        return fallback

    payload = {
        "customer_message": (message or "").strip()[:4000],
        "database_context": context,
    }
    user_text = json.dumps(payload, ensure_ascii=False)
    if len(user_text) > _MAX_CONTEXT_CHARS:
        user_text = user_text[:_MAX_CONTEXT_CHARS] + "…"
    model = (OPENAI_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"
    policy_block = _company_info_policy_reference_block(context, 3200)
    if not policy_block.strip():
        try:
            policy_block = policies_text_for_ai_context(3200)
        except Exception:
            policy_block = ""
    system_content = _ORCHESTRATOR_SYSTEM
    if policy_block:
        system_content = (
            _ORCHESTRATOR_SYSTEM
            + "\n\n--- مرجع سياسات المتجر (أولوية: ما سبق من لوحة الإدارة؛ ثم الملخص التالي إن وُجد) ---\n"
            + policy_block
        )
    # ── تنظيف تاريخ المحادثة للصيغة الصحيحة ──
    clean_history = []
    for h in (history or []):
        if isinstance(h, dict):
            role = h.get("role", "")
            content = h.get("content", "")
            # تحويل role "bot" أو "assistant" إلى "assistant"
            if role in ("bot", "assistant"):
                role = "assistant"
            elif role == "user":
                pass
            else:
                continue
            if content and isinstance(content, str):
                # إذا كان content هو JSON payload كبير → استخرج customer_message فقط
                if content.strip().startswith("{") and "customer_message" in content:
                    try:
                        parsed = json.loads(content)
                        content = parsed.get("customer_message") or content
                    except Exception:
                        pass
                clean_history.append({"role": role, "content": str(content)[:800]})

    try:
        if OPENAI_ORCH_DEBUG:
            logger.debug("orchestrator: calling OpenAI model=%s", model)
        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=_MAX_ORCHESTRATOR_TOKENS,
            temperature=0.25,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_content},
                *clean_history,
                {"role": "user", "content": user_text},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            logger.warning("OpenAI orchestrator returned empty completion content")
            return fallback
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("OpenAI orchestrator invalid JSON: %s", e)
            return fallback
        if not isinstance(data, dict):
            logger.warning("OpenAI orchestrator JSON root is not an object")
            return fallback
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
            logger.warning("OpenAI orchestrator unknown action: %r", action)
            return fallback
        plan = merge_gender_into_plan(message, data)
        plan = merge_user_context_into_plan(message, plan)
        plan = normalize_orchestrator_plan(plan, context, message)
        plan = coerce_shopping_to_product_search(plan, message)
        return plan
    except Exception as e:
        logger.exception("OpenAI chat orchestrator API failed: %s", e)
        return fallback


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

    try:
        out["company_info"] = db.get_company_info_for_ai()
    except Exception:
        out["company_info"] = {}

    return out


def generate_ai_response(message: str, db_data: Dict[str, Any], history: list = None) -> Optional[str]:
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
                *(history or []),
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


# --- طبقة تقصير/تنقية الردود النهائية (تُستدعى من chat_router فقط) ---

BOT_RESPONSE_SHAPING_SKIP_INTENTS = frozenset(
    {
        "complaint",
        "complaint_rule",
        "complaint_wizard",
        "complaint_policy_precheck",
        "complaint_ticket_lookup",
        "return_policy",
        "location",
        "branch_phone",
        "delivery_local",
        "attachment",
        "error",
        "account_session_sync",
        "collect_name",
        "greeting",
        "thanks",
        "goodbye",
        "silent",
    }
)

_VAGUE_SHAPING_INTENTS = frozenset(
    {
        "product",
        "recommendation",
        "section",
        "general",
        "unknown",
        "no_products",
        "product_clarify",
        "product_followup",
    }
)

_USER_BRANCH_QUESTION_KEYS = (
    "فرع",
    "وين",
    "الموقع",
    "موقع",
    "أقرب",
    "زيارة",
)

_BOT_BRANCH_SENTENCE_MARKERS = (
    "فرع",
    "فروع",
    "فروعنا",
    "مكة",
    "مكه",
    "الرياض",
    "جدة",
    "جده",
    "المدينة",
    "المدينه",
    "خميس",
    "قلوة",
    "قلوه",
    "موقع الفرع",
    "عنوان الفرع",
    "عناوين",
    "خرائط",
    "خريطة",
    "google",
    "دوام",
    "ساعات العمل",
)

_OFF_TOPIC_SENTENCE_MARKERS = (
    "أواني منزلية",
    "أواني",
    "الأواني",
    "رياضة",
    "رياضي",
    "رياضية",
    "مفروشات",
    "إلكترونيات",
    "إلكتروني",
    "أثاث",
    "ألعاب أطفال",
)

_SHAPE_SENT_BOUNDARY = re.compile(r"(?<=[\.\!\?\؟۔])\s+|(?<=[\n\r])")


def _split_sentences_for_shaping(text: str) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []
    parts: List[str] = []
    for para in re.split(r"[\n\r]+", t):
        p = para.strip()
        if not p:
            continue
        chunks = [c.strip() for c in _SHAPE_SENT_BOUNDARY.split(p) if c.strip()]
        if not chunks:
            parts.append(p)
        else:
            parts.extend(chunks)
    return parts if parts else [t]


def _user_asks_branch_or_location(user_message: str) -> bool:
    u = (user_message or "").strip()
    if not u:
        return False
    return any(k in u for k in _USER_BRANCH_QUESTION_KEYS)


def _sentence_mentions_branch_location(sentence: str) -> bool:
    s = (sentence or "").strip()
    if not s:
        return False
    for m in _BOT_BRANCH_SENTENCE_MARKERS:
        if m in s:
            return True
    return False


def _remove_branch_info(text: str, user_message: str = "") -> str:
    """يزيل جملاً تذكر فرعاً/مدينة/موقعاً إذا لم يطلب المستخدم ذلك صراحة."""
    raw = (text or "").strip()
    if not raw:
        return raw
    if _user_asks_branch_or_location(user_message):
        return raw
    sents = _split_sentences_for_shaping(raw)
    if len(sents) <= 1:
        stub = sents[0] if sents else raw
        if _sentence_mentions_branch_location(stub):
            return ""  # ترك النص فارغاً → مسار التصعيد
        return raw
    kept = [s for s in sents if not _sentence_mentions_branch_location(s)]
    return " ".join(kept).strip()


def _remove_irrelevant_category_sentences(text: str, user_message: str) -> str:
    """يحذف جملاً تذكر أصنافاً بعيدة إذا لم ترد في سؤال المستخدم."""
    raw = (text or "").strip()
    u = (user_message or "").strip()
    if not raw:
        return raw
    sents = _split_sentences_for_shaping(raw)
    if not sents:
        return raw
    kept: List[str] = []
    for s in sents:
        markers_in_s = [m for m in _OFF_TOPIC_SENTENCE_MARKERS if m in s]
        if not markers_in_s:
            kept.append(s)
            continue
        if any(m in u for m in markers_in_s):
            kept.append(s)
    out = " ".join(kept).strip()
    return out if out else raw


def _truncate_long_reply(text: str, max_chars: int = 200) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    sents = _split_sentences_for_shaping(t)
    if not sents:
        return t[:max_chars].rstrip() + "…"
    one = sents[0]
    if len(sents) == 1:
        return one[:max_chars].rstrip() + ("…" if len(one) > max_chars else "")
    two = f"{one} {sents[1]}".strip()
    if len(two) <= max_chars:
        return two
    if len(one) <= max_chars:
        return one
    return one[:max_chars].rstrip() + "…"


def _first_sentence_only(text: str, max_chars: int = 200) -> str:
    sents = _split_sentences_for_shaping(text)
    if not sents:
        t = (text or "").strip()
        return t[:max_chars].rstrip() + ("…" if len(t) > max_chars else "")
    first = sents[0]
    if len(first) > max_chars:
        return first[:max_chars].rstrip() + "…"
    return first


def _is_vague_user_message(user_message: str) -> bool:
    u = (user_message or "").strip()
    if not u:
        return True
    if len(u) < 20:
        return True
    if len(u.split()) <= 5 and len(u) < 52:
        return True
    return False


def apply_bot_response_shaping(
    text: str,
    *,
    user_message: str = "",
    intent: str = "",
) -> str:
    """
    تقصير وتنقية رد البوت قبل الإرسال: طول، أقسام بعيدة، ذكر فروع دون طلب، سؤال واحد عند الغموض.
    """
    it = (intent or "").strip()
    if it in BOT_RESPONSE_SHAPING_SKIP_INTENTS:
        return (text or "").strip()
    t = (text or "").strip()
    if not t:
        return t
    t = _remove_irrelevant_category_sentences(t, user_message)
    t = _remove_branch_info(t, user_message)
    if it in _VAGUE_SHAPING_INTENTS and _is_vague_user_message(user_message):
        t = _first_sentence_only(t, max_chars=200)
    else:
        t = _truncate_long_reply(t, max_chars=200)
    return t.strip()