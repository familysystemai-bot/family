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

# ── الدفعة 3-ج: بنية موحّدة للمزوّدات + قواعد صارمة + scope filter ──
try:
    from logic import llm_provider
    from logic import strict_prompts
    from logic.scope_filter import is_out_of_scope, build_out_of_scope_reply
    from logic.ai_usage_tracker import track_llm_call
    _LLM_PROVIDER_AVAILABLE = True
except ImportError as _e:
    logger.warning("llm_provider/strict_prompts not available: %s", _e)
    _LLM_PROVIDER_AVAILABLE = False
    llm_provider = None  # type: ignore
    strict_prompts = None  # type: ignore
    def is_out_of_scope(_msg): return None  # type: ignore
    def build_out_of_scope_reply(_t, _n=""): return ""  # type: ignore
    def track_llm_call(**_kw): pass  # type: ignore


def _any_openai_key_configured() -> bool:
    """مفتاح OpenAI من config أو من llm_provider (بيئة أو لوحة التحكم)."""
    if (OPENAI_API_KEY or "").strip():
        return True
    if _LLM_PROVIDER_AVAILABLE and llm_provider is not None:
        try:
            return bool((llm_provider.get_openai_api_key() or "").strip())
        except Exception:
            pass
    return False


_ORCHESTRATOR_ACTION_ALIASES = {
    # النموذج أو strict_prompts قد يعيدان اسم عملية قديم/بديل
    "complaint_routing": "complaint",
    "complaint_route": "complaint",
}


def _canonical_orchestrator_action(data: dict) -> str:
    """يوحّد حقل action مع المخطط المعتمد في allowed_actions."""
    if not isinstance(data, dict):
        return ""
    raw = str(data.get("action") or "").strip().lower()
    canon = _ORCHESTRATOR_ACTION_ALIASES.get(raw, raw)
    if canon != raw:
        data["action"] = canon
    return canon


# حدّ استجابة النموذج (تقريباً كما طُلب)
_MAX_COMPLETION_TOKENS = 500
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

_ORCHESTRATOR_SYSTEM = """أنت موظف خدمة عملاء حقيقي في منسّق محادثة متجر أزياء؛ تتحدث بشكل طبيعي ومرن، بعيداً عن الصياغة الروبوتية الجافة.

قواعد حقل message (وللصياغة عموماً):
- الطول: جمل قصيرة ومرتبة — غالباً 2–3 جمل؛ يصل إلى 4 جمل قصيرة فقط عند الحاجة في: complaint، general_response، general_reply، return_policy، أو عند شرح لطيف بعد سؤال عام. في مسار product_search وcategory_suggestion فضّل 2 جمل كحد أعلى ما أمكن (وضوح + سرعة).
- رد على طلب العميل؛ لا توسّع بمواضيع جانبية لا صلة لها.
- نفس لهجة العميل إن تبيّنت (سعودي / مصري / خليجي / فصحى)، وإلا خليجية بسيطة.
- أسلوب بشري (مو رسمي بزيادة)، بدون قوائم مرقّمة طويلة أو فقرات ضخمة أو تكرار.
- **حقائق** المنتجات والأقسام والفروع والأسعار والتوفر: فقط من database_context؛ لا تخمن ولا تخترع أسماء أقسام أو منتجات غير ظاهرة في العينة.
- عند عدم اليقين في الكتالوج: لا تقل "وضّح لي" أو "وضح لي". فضّل سؤالاً محدداً (مثل: رجالي ولا نسائي؟) أو قل بلطف إنك بتأكد من الفرع واضبط action = "product_search" للتصعيد.
  مثال: BAD "وضّح لي وش تدور" — GOOD "تبغى رجالي ولا نسائي؟"
- تجنّب عبارات فارغة مثل: "ما لقطت عليك"، "ما لقيت المنتج"، "وش تدور"، "جرب تشوف"، "تتفرج على"، "أقسام قريبة من طلبك".
- ممنوع: ذكر بطء النظام أو أعطال تقنية أو "جرّب بعد لحظة" — لا أعذار تقنية وهمية.
- ممنوع في message صياغة «ما لقيت / لا يوجد / غير موجود» **كإنك تغلق الموضوع نهائياً** عن منتج قد يكون عند الفرع — بدلاً من ذلك action = "product_search" ليتم التصعيد؛ يجوز أن تعتذر بلطف مع التصعيد في نفس الجملة.
- ممنوع اقتراح أقسام عشوائية بلا صلة بالرسالة.
- لا تخترع فروعاً أو أرقاماً أو دواماً. عندما يسأل عن **رقم فرع، أوقات العمل، العنوان، الخرائط، أو أقرب فرع**: استعمل **database_context.branch_directory** (من قاعدة البيانات) حرفياً؛ إذا كان الحقل فارغاً في الدليل فقل بلطف إن ما عندك التفاصيل داخل النظام ووجّه لتطبيق الشركة أو الزيارة.
- للأسئلة العامة غير المتعلقة بالفرع لا تطرح قائمة كل الفروع — اكتفِ بما يخدم سؤاله فقط.

database_context.branch_directory:
- قائمة منسّقة بالفروع المتاحة: اسم المدينة/الفرع، الهاتف، إيميل الشكوى إن وُجد، عنوان مختصر، رابط الخرائط، وملخص **أوقات العمل** عند وجودها في الجدول. هذه هي المرجعية عند الأسئلة العملانية عن الفروع.

database_context.company_info (سياسات وخدمات رسمية):
- المصدر الوحيد للإجابة عند سؤال العميل عن: التوصيل، الشحن، الاسترجاع، الدفع، الدوام، معلومات عامة عن الفروع، أو «خدمات» فرع معيّن — إن وُجدت في الحقول أو في branch_services.
- حقل «chat_extra» و«general»: نص يحدده المتجر (أسئلة شائعة، تعريف، أي معلومات عامة مسموح ذكرها للعملاء) — تلتزم به حرفياً عند الصلة ولا تخترع فوقه.
- لا تخترع شروطاً أو مدداً أو طرق دفع؛ إذا كان الحقل فارغاً أو لا يغطي السؤال: قل «حالياً ما عندنا توصيل، تواصل مع الفرع مباشرة للاستفسار» — ثم اضبط action على general_response لا category_suggestion.
- عند سؤال التوصيل: لا تقترح أقسام أو منتجات أبداً — action يجب أن يكون general_response فقط.
- أجب باختصار معقول؛ لا تكرر سياسات كاملة إلا إذا طلب العميل صراحة تلخيصاً.

صيغ طبيعية مسموحة بلا مبالغة: «ممكن يناسبك…»، «جرّب…»، «يا هلا» — طالما لا تُضف حقائق وهمية.

المصدر الوحيد لأسماء الأقسام والمنتجات: database_context — ممنوع اقتراح قسم أو منتج غير موجود في العينة.
لا تخترع أسعاراً ولا توفراً.

أعد JSON فقط بهذا الشكل:
{
  "action": "product_search" | "category_suggestion" | "branch_request" | "general_response" | "general_reply" | "location_info" | "complaint" | "return_policy",
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
  "message": "2–4 جمل قصيرة حسب الحاجة؛ رد واضح؛ سؤال توضيحي واحد في النهاية إن لزم (أقل في مسار المنتج).",
  "needs_branch": true أو false,
  "needs_details": true أو false
}

عند product_search ووجود مناسبة في الرسالة: اجعل message ترشيحياً باختصار (لماذا قد يناسب العميل) ضمن جملتين تقريباً، لا وصفاً تقنياً للقائمة.
database_context.extracted_user_context: سياق مستخرج قواعدياً — التزم به ولا تتجاهله.
database_context.conversation_history: ملخص آخر المحادثة (آخر 8 رسائل تقريباً) — استخدمه لتجنب تكرار نفس السؤال/نفس التحية، ولتذكر ما قرره العميل قبل قليل (مثلاً: المقاس/اللون/الفرع/الغرض)، لكن لا تخترع معلومات غير موجودة فيه.

إن وُجد intent_scoring في database_context:
- هو تلميح من نظام نقاط داخلي (كلمات مفتاحية + أوزان) وليس حقائق من قاعدة البيانات.
- استخدمه لتحسين اختيار action والصياغة فقط؛ لا تعتمد عليه وحدَه إذا كان needs_clarification = true أو كانت النقاط متقاربة.
- possible_intents يوضح ترتيب التخمين المحلي (product / branch / complaint).

أولوية الإجراء (إلزامية):
- إذا كان واضحاً أن العميل يتسوّق (يبغى/أبغى/عرض/سعر/متوفر/هدية/ملابس/مقاس/لون…): action يجب أن يكون product_search وليس general_response.
- إذا طلب العميل منتجاً/قسماً واضحاً وموجوداً في categories_sample: لا ترد بصياغة عامة مثل «عندنا منتجات متنوعة». الرد المطلوب يكون مباشرًا مثل:
  «ايوة عندنا قسم [اسم القسم]. تبغى موديل معين أو ترشيح؟ وهل الاستخدام لشيء معين مثل زواج أو طلعة؟»
- عند سؤال العميل عن قسم: اربط الطلب بأقسام database_context.categories_sample أولاً (sections) قبل أي فئة عامة، واجعل filters.suggested_categories من الأقسام المطابقة فقط.
- إذا طلب العميل صورة منتج محدد: وجّه المسار إلى product_search برسالة واضحة تطلب عرض الصور المتاحة الآن (لا تكتفِ برد عام).
- إذا لا توجد صور متاحة حالياً: أخبره بوضوح «حالياً ما عندي صور»، ثم اعرض خيار رفع استفسار للفرع بصياغة مباشرة (مثال: «إذا تبغى أرسل استفسار للفرع أبشر»).
- إذا وافق العميل على الإرسال للفرع: message يؤكد الرفع للفرع وأن الطلب سيظهر لديهم في لوحة التحكم، مع action = "product_search" و needs_branch = true.
- إذا سأل العميل عن التوصيل أو الشحن أو يوصلون أو توصلون أو يصلهم: action يجب أن يكون general_response فقط — ممنوع category_suggestion أو product_search.
- إذا سأل عن استرجاع/استبدال صريح: فضّل return_policy إن وُجد مرجع في السياق؛ وإلا general_response مع تصعيد لطيف للفرع عند الحاجة.
- إذا سأل عن دفع/دوام/خدمات عامة: general_response أو location_info حسب السؤال.
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
- message: اعتذر بلطف، خفّف التوتر بلهجة هادئة، ثم اطلب الفرع وتفاصيل المشكلة دون إطالة؛ يجوز حتى 4 جمل قصيرة هنا.

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


def _apply_category_bleed_guard(cats: List[str], message: str) -> List[str]:
    """
    يقلّل تداخل أقسام واضحة (حقائب/شنط مقابل بدلات/طقم) عندما تُذكر جهة واحدة فقط بوضوح.
    """
    m = (
        (message or "")
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
        .lower()
    )
    bag_kw = ("شنط", "شنطة", "حقيبه", "حقيبة", "حقائب", "كلتش", "باكباك", "شنطه")
    suit_kw = (
        "بدله",
        "بدلة",
        "بدل ",
        "طقم ",
        "جاكيت",
        "بنطلون رجالي",
        "قميص رجالي",
    )
    has_bag = any(k in m for k in bag_kw)
    has_suit = any(k in m for k in suit_kw)

    def _is_suitish(name: str) -> bool:
        n = (name or "").lower()
        return any(x in n for x in ("بدل", "بدلة", "طقم", "جاكيت"))

    def _is_baggish(name: str) -> bool:
        n = (name or "").lower()
        return any(x in n for x in ("حقائب", "حقيب", "شنط", "كلتش"))

    if has_bag and not has_suit:
        cats = [c for c in cats if not _is_suitish(c)]
    if has_suit and not has_bag:
        cats = [c for c in cats if not _is_baggish(c)]
    return cats


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
    out = _apply_category_bleed_guard(out, message)
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

    payload = {
        "user_message": (user_message or "").strip()[:2000],
        "user_context": uc,
        "draft_assistant_message": (draft_message or "").strip()[:1200],
        "product_names_shown": titles,
    }
    user_txt = json.dumps(payload, ensure_ascii=False)

    # ── الدفعة 3-ج: استدعاء عبر llm_provider (يدعم تبديل المزوّد + tracking) ──
    if _LLM_PROVIDER_AVAILABLE:
        # نُحقِن القواعد الصارمة في الـ system prompt
        system_with_rules = (
            (strict_prompts.build_recommendation_system_prompt() if strict_prompts else _RECOMMEND_MSG_SYSTEM)
        )
        result = llm_provider.chat(
            messages=[
                {"role": "system", "content": system_with_rules},
                {"role": "user", "content": user_txt},
            ],
            max_tokens=_MAX_REC_MSG_TOKENS,
            temperature=0.38,
            intent_label="product_recommendation",
        )
        if result.success and len(result.text) >= 12:
            return result.text
        return fallback or draft_message

    # ── المسار القديم (fallback لو llm_provider غير متاح) ──
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return fallback or draft_message
    try:
        from openai import OpenAI
    except ImportError:
        return fallback or draft_message

    model = (OPENAI_MODEL or "gpt-4o").strip() or "gpt-4o"
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
        # tracking للمسار القديم
        try:
            u = response.usage
            tokens = int(getattr(u, "total_tokens", 0) or 0) if u is not None else 0
            pt = int(getattr(u, "prompt_tokens", 0) or 0) if u is not None else 0
            ct = int(getattr(u, "completion_tokens", 0) or 0) if u is not None else 0
            track_llm_call(
                provider="openai",
                model=model,
                tokens=tokens,
                intent="product_recommendation",
                prompt_tokens=pt,
                completion_tokens=ct,
            )
        except Exception:
            pass
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
    if not _any_openai_key_configured():
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
    """تحليل ما قبل البحث عبر OpenAI — يعتمد على مفتاح OpenAI (بيئة أو لوحة) و OPENAI_PRESEARCH."""
    if not _any_openai_key_configured():
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
    يستدعي LLM لاستخراج حقول بحث فقط. يعيد dict أو None عند الفشل.
    لا يُستخدم للرد على العميل.
    """
    if not is_openai_presearch_enabled():
        return None

    user_text = (message or "").strip()[:_MAX_PRESEARCH_USER_CHARS]
    if len(user_text) < 2:
        return None

    # ── الدفعة 3-ج: عبر llm_provider ──
    if _LLM_PROVIDER_AVAILABLE:
        system_with_rules = (
            strict_prompts.build_search_analysis_system_prompt()
            if strict_prompts else _SEARCH_ANALYSIS_SYSTEM
        )
        result = llm_provider.chat(
            messages=[
                {"role": "system", "content": system_with_rules},
                {"role": "user", "content": user_text},
            ],
            max_tokens=_MAX_PRESEARCH_COMPLETION_TOKENS,
            temperature=0.15,
            json_mode=True,
            intent_label="search_analysis",
        )
        if not result.success or not result.text:
            return None
        try:
            data = json.loads(result.text)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

    # ── المسار القديم (fallback) ──
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    model = (OPENAI_MODEL or "gpt-4o").strip() or "gpt-4o"
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
        try:
            tokens = int(getattr(response.usage, "total_tokens", 0) or 0)
            track_llm_call(provider="openai", model=model, tokens=tokens, intent="search_analysis")
        except Exception:
            pass
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        logger.exception("OpenAI pre-search analysis failed")
        return None


def _orchestrator_env_allowed() -> bool:
    """نفس شرط التشغيل الفعلي — يُستخدم للتشخيص دون تعارض."""
    v = os.getenv(ORCHESTRATOR_ENV, "true")
    if str(v).strip().lower() not in ("1", "true", "yes"):
        return False
    if _LLM_PROVIDER_AVAILABLE and llm_provider is not None:
        try:
            prov = llm_provider.get_active_provider()
            if llm_provider.is_available(prov):
                return True
        except Exception:
            pass
    return _any_openai_key_configured()


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
        has_key = _any_openai_key_configured()
        if not has_key:
            logger.warning(
                "ORCHESTRATOR DISABLED: لا يوجد مفتاح للمزوّد النشط "
                "(لوحة المؤسس ← التكاملات، أو متغيرات البيئة)"
            )
        else:
            logger.warning(
                "ORCHESTRATOR DISABLED: %s=%r — استخدم القيمة true أو 1 أو yes",
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
        "branch_directory": base.get("branch_directory") or [],
    }
    # ملخص المحادثة للمنسّق: جلسة chat_context إن وُجد؛ وإلا آخر أدوار من DB (_conv_history في الطلب)
    try:
        from logic import chat_context as _cc
        from flask import has_request_context, session as _sess

        summary = (_cc.get_conversation_summary(last_n=8) or "").strip()
        if not summary and has_request_context():
            hist = _sess.get("_conv_history") or []
            if isinstance(hist, list) and hist:
                lines: List[str] = []
                for row in hist[-8:]:
                    if not isinstance(row, dict):
                        continue
                    role = str(row.get("role") or "").strip().lower()
                    content = (row.get("content") or "").strip()
                    if not content:
                        continue
                    if role in ("user", "customer"):
                        label = "العميل"
                    elif role in ("bot", "assistant"):
                        label = "البوت"
                    else:
                        label = role or "رسالة"
                    lines.append(f"{label}: {content[:200]}")
                summary = "\n".join(lines)
        out["conversation_history"] = summary
    except Exception:
        out["conversation_history"] = ""
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

    system_msg = (
        "أنت مساعد خدمة عملاء متجر ملابس. التزم بمرجع السياسات التالي ولا تخترع شروطاً.\n\n"
        + (pol or "(لا يوجد مرجع سياسات محمّل)")
    )

    # ── الدفعة 3-ج: عبر llm_provider ──
    if _LLM_PROVIDER_AVAILABLE:
        # نضيف القواعد الصارمة للسطر العلوي
        if strict_prompts:
            system_msg = strict_prompts.STRICT_RULES_BLOCK + "\n\n" + system_msg
        result = llm_provider.chat(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_instr},
            ],
            max_tokens=_MAX_COMPLAINT_POLICY_TOKENS,
            temperature=0.35,
            json_mode=True,
            intent_label="complaint_policy",
        )
        if not result.success or not result.text:
            return None
        try:
            data = json.loads(result.text)
        except (json.JSONDecodeError, ValueError):
            return None
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

    # ── المسار القديم ──
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    try:
        client = OpenAI(api_key=key)
        model = (OPENAI_MODEL or "gpt-4o").strip() or "gpt-4o"
        response = client.chat.completions.create(
            model=model,
            max_tokens=_MAX_COMPLAINT_POLICY_TOKENS,
            temperature=0.35,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_instr},
            ],
        )
        try:
            tokens = int(getattr(response.usage, "total_tokens", 0) or 0)
            track_llm_call(provider="openai", model=model, tokens=tokens, intent="complaint_policy")
        except Exception:
            pass
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
    from logic import chat_service as _cs

    text = _cs.personalized_service_offer()
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
        "general": "معلومات عامة (رسمية)",
        "chat_extra": "معلومات إضافية للشات (أسئلة شائعة، تفاصيل يحددها المتجر)",
        "social_media": "حسابات التواصل والقنوات (روابط من لوحة الإدارة)",
        "online_store": "المتجر الإلكتروني (رابط من لوحة الإدارة)",
    }
    for k, lab in labels.items():
        v = (ci.get(k) or "").strip()
        if v:
            parts.append(f"{lab}:\n{v}")
    urls = ci.get("delivery_image_urls")
    if isinstance(urls, list) and urls:
        clean = [str(u).strip() for u in urls if str(u).strip()][:12]
        if clean:
            parts.append(
                "صور التوصيل/الشحن (روابط جاهزة للعرض أو الإرسال):\n"
                + "\n".join(clean)
            )
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

    def _orchestrator_api_failure(reason: str, detail: str = "") -> Dict[str, Any]:
        """بعد محاولة استدعاء المنسّق: اعتذار للعميل + تنبيه بريد/لوحة تحليلات."""
        from logic import ai_orchestrator_alerts as _orch_al

        _orch_al.notify_orchestrator_failure(
            reason,
            detail=detail,
            user_message_preview=(message or "")[:500],
        )
        return _orch_al.orchestrator_failure_plan(reason=reason)

    # ── الدفعة 3-ج: scope filter — رفض الرسائل خارج النطاق محلياً ──
    if _LLM_PROVIDER_AVAILABLE:
        scope_type = is_out_of_scope(message)
        if scope_type:
            logger.info("orchestrator: rejecting out-of-scope message (type=%s)", scope_type)
            customer_name = ""
            try:
                customer_name = (context or {}).get("customer", {}).get("name", "")
            except Exception:
                pass
            return {
                "action": "general_response",
                "message": build_out_of_scope_reply(scope_type, customer_name),
                "filters": {},
                "needs_branch": False,
                "out_of_scope": True,
            }

    if not is_chat_orchestrator_enabled():
        logger.info("orchestrator: using rule-based fallback (disabled or missing key)")
        return fallback

    payload = {
        "customer_message": (message or "").strip()[:4000],
        "database_context": context,
    }
    user_text = json.dumps(payload, ensure_ascii=False)
    if len(user_text) > _MAX_CONTEXT_CHARS:
        user_text = user_text[:_MAX_CONTEXT_CHARS] + "…"
    policy_block = _company_info_policy_reference_block(context, 3200)
    if not policy_block.strip():
        try:
            policy_block = policies_text_for_ai_context(3200)
        except Exception:
            policy_block = ""

    # ── الدفعة 3-ج: استخدام strict_prompts للقواعد الصارمة ──
    if _LLM_PROVIDER_AVAILABLE and strict_prompts:
        # نضيف قواعد صارمة شاملة + نُمرّر السياق الكامل للنموذج
        system_content = strict_prompts.build_orchestrator_system_prompt(context)
        if policy_block:
            system_content += (
                "\n\n--- مرجع سياسات المتجر (أولوية: ما سبق من لوحة الإدارة؛ ثم الملخص التالي إن وُجد) ---\n"
                + policy_block
            )
        # نُلحِق التعليمات الأصلية لنحافظ على نفس السلوك
        system_content += "\n\n--- التعليمات الإضافية ---\n" + _ORCHESTRATOR_SYSTEM
    else:
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
            if role in ("bot", "assistant"):
                role = "assistant"
            elif role == "user":
                pass
            else:
                continue
            if content and isinstance(content, str):
                if content.strip().startswith("{") and "customer_message" in content:
                    try:
                        parsed = json.loads(content)
                        content = parsed.get("customer_message") or content
                    except Exception:
                        pass
                clean_history.append({"role": role, "content": str(content)[:800]})

    # ── الدفعة 3-ج: استدعاء عبر llm_provider (يدعم تبديل المزوّد) ──
    if _LLM_PROVIDER_AVAILABLE:
        if OPENAI_ORCH_DEBUG:
            logger.debug("orchestrator: calling via llm_provider (provider=%s)", llm_provider.get_active_provider())
        result = llm_provider.chat(
            messages=[
                {"role": "system", "content": system_content},
                *clean_history,
                {"role": "user", "content": user_text},
            ],
            max_tokens=_MAX_ORCHESTRATOR_TOKENS,
            temperature=0.38,
            json_mode=True,
            intent_label="orchestrator",
        )
        if not result.success or not result.text:
            logger.warning("orchestrator (llm_provider): %s", result.error or "empty")
            return _orchestrator_api_failure(
                "llm_provider_error", detail=result.error or "empty_response"
            )
        raw = result.text
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("orchestrator invalid JSON: %s", e)
            return _orchestrator_api_failure("invalid_json", detail=str(e))
        if not isinstance(data, dict):
            logger.warning("orchestrator JSON root is not an object")
            return _orchestrator_api_failure("invalid_json", detail="root_not_object")
        action = _canonical_orchestrator_action(data)
        allowed_actions = frozenset(
            {
                "product_search",
                "category_suggestion",
                "branch_request",
                "general_response",
                "general_reply",
                "location_info",
                "complaint",
                "return_policy",
            }
        )
        if action not in allowed_actions:
            logger.warning("orchestrator unknown action: %r", action)
            return _orchestrator_api_failure("unknown_action", detail=action)
        plan = merge_gender_into_plan(message, data)
        plan = merge_user_context_into_plan(message, plan)
        plan = normalize_orchestrator_plan(plan, context, message)
        plan = coerce_shopping_to_product_search(plan, message)
        return plan

    # ── المسار القديم (fallback لو llm_provider غير متاح) ──
    key = (OPENAI_API_KEY or "").strip()
    if _LLM_PROVIDER_AVAILABLE and llm_provider is not None:
        key = (llm_provider.get_openai_api_key() or "").strip() or key
    if not key:
        logger.info("orchestrator: using rule-based fallback (empty API key)")
        return fallback
    try:
        from openai import OpenAI
    except ImportError as e:
        logger.error("OpenAI SDK not installed: %s", e)
        return _orchestrator_api_failure("openai_sdk_missing", detail=str(e))

    model = (OPENAI_MODEL or "gpt-4o").strip() or "gpt-4o"
    try:
        if OPENAI_ORCH_DEBUG:
            logger.debug("orchestrator: calling OpenAI model=%s", model)
        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=_MAX_ORCHESTRATOR_TOKENS,
            temperature=0.38,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_content},
                *clean_history,
                {"role": "user", "content": user_text},
            ],
        )
        try:
            tokens = int(getattr(response.usage, "total_tokens", 0) or 0)
            track_llm_call(provider="openai", model=model, tokens=tokens, intent="orchestrator")
        except Exception:
            pass
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            logger.warning("OpenAI orchestrator returned empty completion content")
            return _orchestrator_api_failure("empty_completion")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("OpenAI orchestrator invalid JSON: %s", e)
            return _orchestrator_api_failure("invalid_json", detail=str(e))
        if not isinstance(data, dict):
            logger.warning("OpenAI orchestrator JSON root is not an object")
            return _orchestrator_api_failure("invalid_json", detail="root_not_object")
        action = _canonical_orchestrator_action(data)
        allowed_actions = frozenset(
            {
                "product_search",
                "category_suggestion",
                "branch_request",
                "general_response",
                "general_reply",
                "location_info",
                "complaint",
                "return_policy",
            }
        )
        if action not in allowed_actions:
            logger.warning("OpenAI orchestrator unknown action: %r", action)
            return _orchestrator_api_failure("unknown_action", detail=action)
        plan = merge_gender_into_plan(message, data)
        plan = merge_user_context_into_plan(message, plan)
        plan = normalize_orchestrator_plan(plan, context, message)
        plan = coerce_shopping_to_product_search(plan, message)
        return plan
    except Exception as e:
        logger.exception("OpenAI chat orchestrator API failed: %s", e)
        return _orchestrator_api_failure("openai_api_exception", detail=str(e)[:1500])


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

    branch_directory: List[Dict[str, Any]] = []
    try:
        for b in db.get_all_branches() or []:
            bid = b.get("id")
            if bid is None:
                continue
            try:
                bid_int = int(bid)
            except (TypeError, ValueError):
                continue
            city = str(b.get("city_name") or "").strip()
            phone = str(b.get("phone") or "").strip()
            em = str(b.get("complaint_email") or "").strip()
            loc = None
            addr = ""
            maps_u = ""
            try:
                loc = db.get_branch_location(bid_int)
            except Exception:
                loc = None
            if isinstance(loc, dict):
                addr = str(loc.get("address") or "").strip()
                maps_u = str(loc.get("google_maps_url") or "").strip()
            wh_s = ""
            try:
                wh_rows = db.get_working_hours(bid_int) or []
                parts: List[str] = []
                for r in wh_rows:
                    if not isinstance(r, dict):
                        continue
                    day = (
                        str(r.get("day_type") or r.get("day_name") or "")
                        .strip()
                    )
                    o = str(
                        r.get("open_time")
                        or r.get("start_time_1")
                        or ""
                    ).strip()
                    cl = str(
                        r.get("close_time")
                        or r.get("end_time_1")
                        or ""
                    ).strip()
                    if day and (o or cl):
                        parts.append(f"{day}: {o}–{cl}")
                if parts:
                    wh_s = "; ".join(parts)[:800]
            except Exception:
                wh_s = ""
            branch_directory.append(
                {
                    "branch_city": city,
                    "phone": phone,
                    "complaint_email": em,
                    "address_short": addr[:280] if addr else "",
                    "maps_url": maps_u[:500] if maps_u else "",
                    "working_hours_compact": wh_s,
                }
            )
    except Exception:
        branch_directory = []
    out["branch_directory"] = branch_directory

    return out


def generate_ai_response(message: str, db_data: Dict[str, Any], history: list = None) -> Optional[str]:
    """
    يستدعي LLM مرة واحدة ويعيد نص الرد أو None عند التعذر.
    """
    if not is_ai_fallback_enabled():
        return None

    payload = {
        "customer_message": (message or "").strip(),
        "database_context": db_data or {},
    }
    user_text = json.dumps(payload, ensure_ascii=False)
    if len(user_text) > _MAX_CONTEXT_CHARS:
        user_text = user_text[: _MAX_CONTEXT_CHARS] + "…"

    # ── الدفعة 3-ج: عبر llm_provider ──
    if _LLM_PROVIDER_AVAILABLE:
        # نضيف القواعد الصارمة على النص الأصلي
        system_with_rules = _SYSTEM_PROMPT
        if strict_prompts:
            system_with_rules = strict_prompts.STRICT_RULES_BLOCK + "\n\n" + _SYSTEM_PROMPT
        result = llm_provider.chat(
            messages=[
                {"role": "system", "content": system_with_rules},
                *(history or []),
                {"role": "user", "content": user_text},
            ],
            max_tokens=_MAX_COMPLETION_TOKENS,
            temperature=0.35,
            intent_label="general_chat",
        )
        if result.success and result.text:
            return result.text
        return None

    # ── المسار القديم ──
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    model = (OPENAI_MODEL or "gpt-4o").strip() or "gpt-4o"
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
        try:
            tokens = int(getattr(response.usage, "total_tokens", 0) or 0)
            track_llm_call(provider="openai", model=model, tokens=tokens, intent="general_chat")
        except Exception:
            pass
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
        "complaint_ai",
        "complaint_escalated",
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
        "inquiry_confirm",
        "inquiry_sent",
        "inquiry_error",
        "founder_attribution",
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


def _truncate_long_reply(text: str, max_chars: int = 360) -> str:
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


def _first_sentence_only(text: str, max_chars: int = 280) -> str:
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
        t = _first_sentence_only(t, max_chars=280)
    else:
        t = _truncate_long_reply(t, max_chars=360)
    return t.strip()