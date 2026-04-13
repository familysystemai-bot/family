# -*- coding: utf-8 -*-
"""
منطق الشكاوى في الشات (wizard، تصنيف، بريد، سياسة ما قبل التسجيل).
يُستدعى من chat_service — استيراد chat_service داخل الدوال لتفادي الدائرة.
"""
from __future__ import annotations

import hashlib
import logging
import random
from typing import Any, Literal, Optional
import re
from datetime import datetime

from flask import jsonify, session

from config import (
    ADMIN_EMAIL,
    COMPLAINT_MIN_DETAIL,
    MAIN_RECEIVER_EMAIL,
    SYSTEM_ALERTS_EMAIL,
)
from logic.complaint_scoring import (
    complaint_score_is_direct,
    compute_complaint_score,
    has_negative_complaint_tone,
    has_primary_complaint_signal,
)
from logic.complaint_classifier import classify_complaint_issue, complaint_type_label_ar
from logic.mail_service import send_email
from logic.branch_service import _branch_location_json
from logic.product_service import NO_PRODUCTS_PAYLOAD, _build_products_response
from logic import chat_context as chat_ctx
from logic.chat_handlers.complaint_handler import success_message
from site_config.branches import get_branch, get_management_emails
from site_config.company_policies import build_return_policy_complaint_precheck_summary


def _cs():
    import logic.chat_service as m

    return m


logger = logging.getLogger(__name__)

_TICKET_IN_MESSAGE_RE = re.compile(r"\bTKT-[A-Z0-9]{8}\b", re.IGNORECASE)

# عبارات تدل على وجود شكوى سابقة
_PREV_COMPLAINT_PHRASES = (
    "اشتكيت سابقا",
    "اشتكيت سابقاً",
    "اشتكيت قبل",
    "اشتكيت من قبل",
    "شكوى سابقة",
    "شكوى قديمة",
    "عندي شكوى مسبقة",
    "رفعت شكوى",
    "سجلت شكوى",
    "سجّلت شكوى",
    "عندي تذكرة",
    "رقم التذكرة",
    "رقم الشكوى",
    "شكواي السابقة",
    "شكواي القديمة",
    "وش صار بشكواي",
    "وش صار بالشكوى",
    "وش صار ب الشكوى",
    "ايش صار بشكواي",
    "ايش صار بالشكوى",
    "شو صار بشكواي",
    "متابعة شكوى",
    "اتابع شكوى",
    "أتابع شكوى",
    "شكواي",
    "شكوايه",
    "شكواي وين",
    "وين شكواي",
)


def _message_is_previous_complaint_followup(message: str) -> bool:
    """هل الرسالة تُشير لشكوى سابقة بدون رقم تذكرة؟"""
    t = (message or "").strip()
    if _TICKET_IN_MESSAGE_RE.search(t):
        return False   # عنده رقم → يُعالَج بـ try_complaint_ticket_status_lookup
    return any(p in t for p in _PREV_COMPLAINT_PHRASES)


def _message_after_complaint_saved(complaint_id: Optional[int]) -> str:
    msg_ok = success_message()
    if not complaint_id:
        return msg_ok
    cs = _cs()
    row = cs.get_db().get_complaint_row(int(complaint_id))
    ticket = (row.get("ticket_code") or "").strip() if row else ""
    if ticket:
        return (
            msg_ok
            + f"\n\nرقم تذكرتك: {ticket}\n"
            "احفظ الرقم للاستعلام عن حالة الشكوى في أي وقت."
        )
    return msg_ok


def rule_payload_is_complaint_submit_response(rule_d: Any) -> bool:
    """
    رد تسجيل شكوى نهائي من المسار المنظم (intent حرفياً complaint).
    بعد الحفظ تُزال complaint_data من الجلسة؛ يُستخدم هذا للتمييز عن complaint_rule وغيره.
    """
    return isinstance(rule_d, dict) and str(rule_d.get("intent") or "").strip() == "complaint"


def try_complaint_ticket_status_lookup(message: str):
    """
    إن وُجد رقم تذكرة (TKT-XXXXXXXX) في الرسالة، يُرجع jsonify باستعلام الحالة وإلا None.
    """
    m = _TICKET_IN_MESSAGE_RE.search(message or "")
    if not m:
        return None
    code = m.group(0).upper()
    get_db = _cs().get_db
    row = get_db().get_complaint_by_ticket_code(code)
    if not row:
        return jsonify(
            {
                "products": [],
                "message": (
                    f"لم نجد شكوى برقم التذكرة {code}. "
                    "تحقق من الرقم أو تواصل مع خدمة العملاء."
                ),
                "intent": "complaint_ticket_lookup",
            }
        )
    st = (row.get("status") or "").strip().lower()
    ticket = (row.get("ticket_code") or code).strip()
    if st == "resolved":
        notes = (row.get("resolution_notes") or "").strip()
        msg = f"تذكرتك {ticket}: تم الحل."
        if notes:
            msg += f"\n\nملاحظات الحل:\n{notes}"
    else:
        msg = (
            f"تذكرتك {ticket}: قيد المعالجة.\n"
            "فريقنا يتابع طلبك."
        )
    return jsonify(
        {
            "products": [],
            "message": msg,
            "intent": "complaint_ticket_lookup",
        }
    )


# حوار شكوى متعدد الخطوات (جمع التفاصيل + الفرع)
_MIN_COMPLAINT_DETAIL = COMPLAINT_MIN_DETAIL

_GULF_APOLOGIES = [
    "نعتذر منك على اللي صار",
    "حقك علينا وما يصير إلا كل خير",
    "سامحنا على التقصير",
    "نقدر زعلك واعتذارنا لك",
    "ما كان هذا مستوى الخدمة اللي نطمح له",
    "أبشر بنحل الموضوع بأسرع وقت",
    "المعذرة منك ونجري تحسين فوري",
    "نعتذر لك ونعوضك عن التجربة",
]
_EGYPT_APOLOGIES = [
    "معلش على اللي حصل",
    "حقك علينا وآسفين جدًا",
    "نأسف للتجربة دي",
    "سامحنا على التقصير",
    "إحنا مقدّرين شكواك جدًا",
    "هنحل المشكلة في أسرع وقت",
    "متأسفين جدًا على الإزعاج",
]
_STANDARD_APOLOGIES = [
    "نعتذر لك عن أي تقصير",
    "نقدر ملاحظتك ونعمل على حلها",
    "المعذرة ولن يتكرر بإذن الله",
    "نأسف لما حدث ونعمل على تحسين الخدمة",
    "حقك علينا ونعالج الموضوع فوراً",
]


def detect_complaint_score(message: str) -> int:
    """
    نقاط شكوى: كلمات أساسية +2، نبرة سلبية +1، وذكر فرع معروف +1 فقط إن وُجدت إشارة شكوى أو نبرة سلبية.
    عند بلوغ العتبة الموحّدة تُفعّل معالجة الشكوى.
    """
    raw = (message or "").strip()
    if not raw:
        return 0
    cs = _cs()
    t = cs.normalize_message_for_branch_search(raw)
    branch_name = cs.resolve_branch_from_message(raw)
    return compute_complaint_score(t, has_known_branch=bool(branch_name))


def detect_complaint_intent(message: str) -> bool:
    """توافق خلفي: شكوى عندما تبلغ النقاط العتبة الموحّدة."""
    return complaint_score_is_direct(detect_complaint_score(message))


def _complaint_apology_bucket(message: str) -> Literal["gulf", "egypt", "standard"]:
    """تصنيف بسيط للاعتذار حسب أسلوب الرسالة + لهجة الجلسة."""
    t = (message or "").strip()
    if any(x in t for x in ("وش", "ابغى", "أبغى", "إبغى", "عندكم", "عندك")):
        return "gulf"
    if any(x in t for x in ("عايز", "محتاج", "فين", "إيه", "ايه", "أيه")):
        return "egypt"
    cd = (session.get("chat_dialect") or "default").strip()
    if cd == "masri":
        return "egypt"
    if cd in ("hijazi", "najdi", "janoubi", "sharqi", "shamali", "yemeni"):
        return "gulf"
    return "standard"


def _apology_lines_for_bucket(bucket: str) -> list[str]:
    if bucket == "gulf":
        return list(_GULF_APOLOGIES)
    if bucket == "egypt":
        return list(_EGYPT_APOLOGIES)
    return list(_STANDARD_APOLOGIES)


def _pick_apology_line(message: str) -> str:
    """اختيار اعتذار عشوائي دون تكرار الجملة السابقة مباشرة."""
    bucket = _complaint_apology_bucket(message)
    lines = _apology_lines_for_bucket(bucket)
    last = (session.get("last_apology") or "").strip()
    pool = [x for x in lines if x != last] if last else lines
    if not pool:
        pool = lines
    pick = random.choice(pool)
    session["last_apology"] = pick
    return pick


def _complaint_warm_display_name() -> str:
    """اسم للعنوان في نص الشكوى — من الجلسة فقط."""
    nm = (session.get("name") or session.get("user_name") or "").strip()
    if len(nm) < 2 or nm in ("أخوي", "حضرتك"):
        return ""
    return nm


def _complaint_branch_display_short(city_name: Optional[str]) -> str:
    s = (city_name or "").strip()
    return s.replace("فرع ", "").strip() or s or "المذكور"


def _warm_complaint_opening_need_branch() -> str:
    """أول رد شكوى: اعتذار خليجي دافئ + طلب الفرع."""
    nm = _complaint_warm_display_name()
    ya = f" يا {nm}" if nm else ""
    return (
        f"نأسف جداً على هذه التجربة{ya} 🙏 "
        "هذا مو المستوى اللي نرضاه لعملائنا. "
        "ممكن تحدد لي الفرع عشان نتابع معك الموضوع؟"
    )


def _complaint_prompt_body(
    message: str, kind: Literal["need_branch", "need_details"]
) -> str:
    """نص بشري حسب خطوة الشكوى (بدون نبرة آلية)."""
    if kind == "need_branch":
        return _warm_complaint_opening_need_branch()
    nm = _complaint_warm_display_name()
    ya = f"يا {nm}، " if nm else ""
    return (
        f"{ya}نأسف إنك مرت بالموضوع 🙏 "
        "وش صار بالضبط؟ اكتب لي التفاصيل عشان نخدمك كويس."
    )


def _complaint_need_target_ack_and_followup(branch_city: Optional[str]) -> tuple[str, str]:
    """بعد تحديد الفرع: رسالة تأكيد + متابعة منفصلة لخيارات التصعيد."""
    nm = _complaint_warm_display_name()
    br = _complaint_branch_display_short(branch_city)
    if nm:
        ack = f"تمام يا {nm}، سجّلنا ملاحظتك على فرع {br}."
    else:
        ack = f"تمام، سجّلنا ملاحظتك على فرع {br}."
    follow = "تبي نرفع الشكوى لـ:\n\n• إدارة الفرع\n• الإدارة العليا"
    return ack, follow


def _complaint_success_message_after_submit(target: str) -> str:
    """
    بعد حفظ الشكوى — الرد دائماً مطمئن بغض النظر عن اختيار العميل.
    الشكاوى تُوجَّه للفرع دائماً أولاً (حتى لو اختار الإدارة العليا).
    """
    nm = _complaint_warm_display_name()
    ya = f" يا {nm}" if nm else ""
    return (
        f"ابشر{ya}، ما لك إلا اللي يرضيك ✅ "
        "تم رفع موضوعك وراح يتواصل معك الفريق بأقرب وقت ممكن 🙏"
    )


def _complaint_rule_submitted_user_message(
    complaint_id: Optional[int], target: str, email_ok: bool
) -> str:
    """نص نهائي بعد تسجيل شكوى مسار القواعد + تذكرة إن وُجدت."""
    msg_ok = _complaint_success_message_after_submit(target)
    if complaint_id:
        row = _cs().get_db().get_complaint_row(int(complaint_id))
        ticket = (row.get("ticket_code") or "").strip() if row else ""
        if ticket:
            msg_ok += (
                f"\n\nرقم تذكرتك: {ticket}\n"
                "احفظ الرقم للاستعلام عن حالة الشكوى في أي وقت."
            )
    if email_ok is False:
        msg_ok += " (تعذر إرسال البريد؛ تم حفظ الشكوى.)"
    return msg_ok


def _jsonify_complaint_rule_need_target(
    branch_city: Optional[str], branch_list: list,
) -> Any:
    ack, follow = _complaint_need_target_ack_and_followup(branch_city)
    return jsonify(
        {
            "products": [],
            "message": ack,
            "followup_message": follow,
            "intent": "complaint_rule",
            "branches": branch_list,
        }
    )


def _complaint_retry_target_message(message: str) -> str:
    return (
        "ما التقطنا اختيارك 🙏 "
        "اكتب بوضوح: «إدارة الفرع» أو «الإدارة العليا» — أو أرسل 1 أو 2."
    )


def _complaint_message_with_policy_ai(
    base_message: str,
    merged_issue: str,
    step_key: str,
) -> str:
    """
    يُرجع نص الخطوة فقط — بدون إلحاق نص من نموذج (كان يسبب هلوسة سياسات وتكراراً).
    التصنيف الاختياري يُستخرج بصمت دون تغيير الرسالة المعروضة.
    """
    raw = (merged_issue or "").strip()
    if len(raw) < _MIN_COMPLAINT_DETAIL:
        return base_message
    from logic import ai_fallback as ai_fb

    cache_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    prev = session.get("complaint_policy_ai_cache")
    if isinstance(prev, dict) and prev.get("k") == cache_key:
        session["complaint_ai_classification_ar"] = (prev.get("classification_ar") or "").strip()
        return base_message
    res = ai_fb.enrich_complaint_policy_interaction(raw, step_name=step_key)
    if res:
        cls = (res.get("classification_ar") or "").strip()
        session["complaint_ai_classification_ar"] = cls
        session["complaint_policy_ai_cache"] = {
            "k": cache_key,
            "classification_ar": cls,
            "reply_paragraph": "",
        }
    return base_message


def _user_facing_complaint_text(
    message: str,
    kind: Literal["need_branch", "need_details"],
    merged_issue_for_ai: str,
) -> str:
    return _complaint_message_with_policy_ai(
        _complaint_prompt_body(message, kind),
        merged_issue_for_ai,
        kind,
    )


def complaint_ready_for_ai() -> bool:
    """
    جاهز لإثراء AI: فرع معروف + تفاصيل كافية.
    لا يُمنع بسبب إعادة طلب الهدف — المنع فقط عند نقص الفرع أو التفاصيل.
    """
    if not session.get("complaint_active"):
        return False
    return bool(session.get("complaint_branch")) and bool(session.get("complaint_details"))


def _sync_complaint_progress_session(*, invalid_target: bool = False) -> None:
    """
    يحدّث مفاتيح الجلسة للشكوى: فرع، تفاصيل كافية، محاولة هدف خاطئ، ومنع/السماح بدمج AI.
    يُستدعى بعد كل تحديث لـ complaint_data.
    """
    cd = session.get("complaint_data") or {}
    br = cd.get("branch")
    issue = (cd.get("issue") or "").strip()
    session["complaint_active"] = True
    session["complaint_branch"] = br if br else None
    session["complaint_details"] = len(issue) >= _MIN_COMPLAINT_DETAIL
    session["complaint_invalid_target_retry"] = bool(invalid_target)
    # لا نمنع المنسّق أثناء جمع التفاصيل — دمج/إثراء يبقى متاحاً مع نص القواعد
    session["complaint_block_ai_merge"] = False


def _exit_complaint_rule_session():
    session.pop("complaint_data", None)
    session.pop("complaint_active", None)
    session.pop("complaint_block_ai_merge", None)
    session.pop("complaint_branch", None)
    session.pop("complaint_details", None)
    session.pop("complaint_invalid_target_retry", None)


# كلمات تربط الشكوى بسياسة الاستبدال/الاسترجاع → ملخص سياسة قبل التسجيل
_COMPLAINT_RETURN_POLICY_KW = (
    "استبدال",
    "استرجاع",
    "رفضوا",
    "ما يقبلون",
)

_RETURN_EXCHANGE_KEYWORDS = (
    "استبدال", "استرجاع", "إرجاع", "ارجاع",
    "رجعت", "أرجع", "ارجع", "يرجع",
    "استبدل", "أستبدل", "ما قبلوا", "ما يقبلون",
    "رفضوا الاسترجاع", "رفض الاستبدال",
    "مرتجع", "مرتجعات",
)


def _is_return_exchange_complaint(text: str) -> bool:
    """هل الشكوى تتعلق بالاسترجاع أو الاستبدال؟"""
    t = (text or "").strip()
    return any(k in t for k in _RETURN_EXCHANGE_KEYWORDS)

# خروج صريح من وضع الشكوى فقط بهذه الصياغ
_EXPLICIT_PRODUCT_SWITCH = (
    "أبغى منتج",
    "ابغى منتج",
    "أبي منتج",
    "ابي منتج",
    "ابغي منتج",
    "أريد منتج",
    "اريد منتج",
    "دور على منتج",
    "ابغى اشتري",
    "ابغي اشتري",
    "ابي اشتري",
    "أبحث عن منتج",
)
# نيات واضحة خارج سياق الشكوى — تُنهي المعالج وتُعيد التوجيه لمسار عادي
_INTENTS_EXIT_COMPLAINT_CONTEXT = frozenset(
    {
        "greeting",
        "product",
        "location",
        "location_pick",
        "section",
        "recommendation",
        "return_policy",
        "branch_phone",
        "thanks",
        "goodbye",
        "general",
    }
)


# متابعة تسوّق/اقتراحات قصيرة — لا تُبقى الجلسة في مسار الشكوى
_SHOPPING_FOLLOWUP_MARKERS = (
    "اقتراحات",
    "اقتراح",
    "وش تقترح",
    "وش تنصح",
    "نصيحة",
    "نصائح",
    "أفكار",
    "منك",
    "من عندك",
    "عطني اقتراح",
    "ساعدني اختار",
    "وش رايك",
    "شنو تنصح",
    "ممكن اقتراح",
)


def _looks_like_shopping_followup_not_complaint(message: str) -> bool:
    t = (message or "").strip()
    if not t or len(t) > 140:
        return False
    cs = _cs()
    norm = cs.normalize_message_for_branch_search(t)
    if has_primary_complaint_signal(norm):
        return False
    if has_negative_complaint_tone(norm):
        return False
    if any(k in t for k in _SHOPPING_FOLLOWUP_MARKERS):
        return True
    return False


def _fresh_intent_exits_complaint_flow(message: str) -> bool:
    """رسالة جديدة تُصنَّف بنية غير شكوى → لا نُبقي المستخدم في حوار الشكوى."""
    from logic.intent_handler import _complaint_signals_negated, detect_chat_intent

    msg = (message or "").strip()
    if _complaint_signals_negated(msg):
        return True
    cd = session.get("complaint_data") or {}
    step = (cd.get("step") or "").strip()
    if step == "need_target" and _parse_escalation_target(msg):
        return False
    if _looks_like_shopping_followup_not_complaint(msg):
        return True
    cs = _cs()
    intent = detect_chat_intent(msg, cs.resolve_branch_from_message)
    # اسم مدينة/فرع قصير يُصنَّف موقعاً — لا يُلغي جمع بيانات الشكوى
    if step in ("need_branch", "need_details", "need_target") and intent in (
        "location",
        "location_pick",
    ):
        return False
    if intent in _INTENTS_EXIT_COMPLAINT_CONTEXT:
        return True
    if intent == "unknown":
        li = (session.get("chat_last_intent") or "").strip()
        if li in ("product", "recommendation", "section"):
            if len(msg) < 96:
                return True
            if _looks_like_shopping_followup_not_complaint(msg):
                return True
    return False


def maybe_clear_complaint_session_before_router(message: str) -> None:
    """يُستدعى أول مسار الشات: يزيل سياق الشكوى عند نية خروج واضحة.

    يشمل المعالج والقواعد حتى لو complaint_active لم يُضبط بعد (_sync).
    """
    if not (
        session.get("complaint_active")
        or session.get("complaint_data")
        or session.get("complaint_wizard")
        or session.get("complaint_policy_precheck")
    ):
        return
    if _fresh_intent_exits_complaint_flow((message or "").strip()):
        clear_complaint_session_for_topic_switch()


def clear_complaint_session_for_topic_switch() -> None:
    """مسح كامل سياق الشكوى عند الخروج لمسار تسوّق/استفسار آخر."""
    session.pop("complaint_wizard", None)
    session.pop("complaint_data", None)
    session.pop("chat_active_complaint_id", None)
    session.pop("complaint_branch_label", None)
    session.pop("complaint_active", None)
    session.pop("complaint_block_ai_merge", None)
    session.pop("complaint_branch", None)
    session.pop("complaint_details", None)
    session.pop("complaint_invalid_target_retry", None)
    session.pop("last_apology", None)
    session.pop("complaint_policy_precheck", None)
    session.pop("complaint_ai_classification_ar", None)
    session.pop("complaint_policy_ai_cache", None)
    session["chat_current_intent"] = None


_EXPLICIT_LOCATION_SWITCH = (
    "فين الموقع",
    "وين الموقع",
    "ابغى الموقع",
    "أبغى الموقع",
    "ابي الموقع",
    "موقع الفرع",
    "ابغى العنوان",
    "أبغى العنوان",
    "وين الفرع",
    "فين الفرع",
)


def _complaint_type_fields(complaint_type: str) -> dict:
    ct = (complaint_type or "unspecified").strip() or "unspecified"
    return {
        "complaint_type": ct,
        "complaint_type_label": complaint_type_label_ar(ct),
    }


def _explicit_switch_from_complaint(message: str):
    """أثناء الشكوى: الخروج لمسار منتج/موقع فقط بعبارات صريحة."""
    t = (message or "").strip()
    if any(p in t for p in _EXPLICIT_PRODUCT_SWITCH):
        return "product"
    if _parse_escalation_target(t):
        return None
    if cs := _cs():
        if cs.resolve_branch_from_message(t):
            return None
    if any(p in t for p in _EXPLICIT_LOCATION_SWITCH):
        return "location"
    return None


def _exit_complaint_mode_after_successful_submit():
    """بعد حفظ الشكوى بنجاح: إنهاء وضع الشكوى حتى لا تُعالَج الرسائل التالية كملاحقات."""
    session.pop("complaint_wizard", None)
    session.pop("complaint_data", None)
    session.pop("chat_active_complaint_id", None)
    session.pop("complaint_branch_label", None)
    session.pop("complaint_active", None)
    session.pop("complaint_block_ai_merge", None)
    session.pop("complaint_branch", None)
    session.pop("complaint_details", None)
    session.pop("complaint_invalid_target_retry", None)
    session.pop("last_apology", None)
    session.pop("complaint_ai_classification_ar", None)
    session.pop("complaint_policy_ai_cache", None)
    session["chat_current_intent"] = None


def _send_complaint_email(complaint_id, branch_label, issue_text, is_append=False, branch_id=None):
    """إرسال بريد: MAIN_RECEIVER_EMAIL + إيميل الفرع (من قاعدة البيانات) + إداريون اختياريون."""
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kind = "تحديث شكوى" if is_append else "شكوى جديدة"
    subject = f"{kind} (#{complaint_id})"
    crow = get_db().get_complaint_row(complaint_id)
    ct_label = complaint_type_label_ar((crow or {}).get("complaint_type") or "unspecified")
    body = (
        f"{kind}\n\n"
        f"نص الشكوى:\n{issue_text}\n\n"
        f"تصنيف الشكوى: {ct_label}\n"
        f"اسم الفرع: {branch_label or 'غير محدد'}\n"
        f"اسم العميل (محادثة): {_display_name()}\n"
        f"الوقت: {now}\n"
    )
    recipients = []
    if MAIN_RECEIVER_EMAIL:
        recipients.append(MAIN_RECEIVER_EMAIL.strip())

    branch_email = None
    if branch_id is not None:
        branch_email = get_db().get_branch_complaint_email(branch_id)
    if not branch_email and branch_label:
        bid = get_db().get_branch_id_by_city_name(branch_label)
        if bid is not None:
            branch_email = get_db().get_branch_complaint_email(bid)
    if not branch_email and branch_label:
        contact = get_branch(branch_label) or {}
        branch_email = (contact.get("manager_email") or "").strip() or None

    if branch_email:
        recipients.append(branch_email)

    recipients.extend(get_management_emails())
    recipients = list(dict.fromkeys([r for r in recipients if r]))
    ok = send_email(recipients, subject, body)
    if not ok:
        print(
            f"❌ فشل إرسال بريد الشكوى #{complaint_id} — "
            "تحقق من SENDER_EMAIL و SENDER_PASSWORD (App Password) في متغيرات البيئة."
        )
    return ok


def _customer_contact_from_session():
    raw = (session.get("user_contact") or "").strip()
    phone, email = None, None
    if raw:
        if "@" in raw:
            email = raw[:320]
        else:
            phone = raw[:64]
    name = (session.get("name") or session.get("user_name") or "").strip()
    if not name or name in ("أخوي", "حضرتك"):
        name = None
    return name, phone, email


def _customer_user_id_for_records() -> str:
    cid = session.get("customer_id")
    try:
        if cid is not None:
            return f"customer:{int(cid)}"
    except (TypeError, ValueError):
        pass
    return session.get("user_id", "web_user_unknown")


def _submit_new_complaint(message: str, branch_name_override=None):
    """إنشاء سجل شكوى جديد + إشعار بريد. يعيد (complaint_id, branch_name, خطأ_للعرض, email_ok)."""
    cs = _cs()
    get_db = cs.get_db
    extract_branch_name = cs.extract_branch_name
    _display_name = cs._display_name
    branch_name = branch_name_override
    if branch_name is None:
        branch_name = extract_branch_name(message)
    branch_id = get_db().get_branch_id_by_city_name(branch_name) if branch_name else None
    nm = _display_name()
    message_plain = (message or "").strip()
    issue_body = (
        f"[العميل: {nm}]\n{message_plain}"
        if nm not in ("أخوي", "حضرتك")
        else message_plain
    )
    ctype = classify_complaint_issue(issue_body)
    branch_display = (branch_name or "").strip()
    if not branch_display and branch_id:
        br = get_db().get_branch_by_id(int(branch_id))
        if br:
            branch_display = (br.get("city_name") or "").strip()
    cust_name, cust_phone, cust_email = _customer_contact_from_session()
    ai_cls = (session.get("complaint_ai_classification_ar") or "").strip() or None
    complaint_id = get_db().add_complaint(
        user_id=_customer_user_id_for_records(),
        issue=issue_body,
        branch_id=branch_id,
        complaint_type=ctype,
        message=message_plain,
        branch_name=branch_display or None,
        customer_name=cust_name,
        customer_phone=cust_phone,
        customer_email=cust_email,
        complaint_ai_classification=ai_cls,
    )
    if complaint_id is None:
        print("❌ لم يُحفظ سجل الشكوى — أرجِع add_complaint بقيمة None")
        return None, branch_name, "تعذر حفظ الشكوى حالياً، حاول بعد قليل.", None, None
    row = get_db().get_complaint_row(complaint_id)
    full_issue = row["issue"] if row else issue_body
    email_ok = _send_complaint_email(
        complaint_id, branch_name or "", full_issue, is_append=False, branch_id=branch_id
    )
    return complaint_id, branch_name, None, email_ok, ctype


def _complaint_return_precheck_message(addressing: str) -> str:
    """
    رسالة ذكية وبشرية للاسترجاع/الاستبدال:
    1. تعرض الشروط من قاعدة البيانات إن وُجدت
    2. تطمّن العميل أن الفرع سيتدخل حتى لو الشروط لا تنطبق
    3. تسأله هل يريد تسجيل الشكوى
    """
    get_db = _cs().get_db
    policy_text = get_db().get_complaint_precheck_policy_summary_text()
    name = (addressing or "").strip()
    nm = name if name and name not in ("أخوي", "حضرتك") else None
    ya = f"يا {nm}، " if nm else ""

    if policy_text:
        return (
            f"{ya}فهمت موضوعك ويهمنا نخدمك صح 🙏\n\n"
            f"حسب سياسة الاسترجاع والاستبدال المعتمدة عندنا:\n\n"
            f"{policy_text}\n\n"
            "إذا حسيت إن حالتك ما انطبقت على الشروط، أو الموظف ما التزم — "
            "قُل **نعم** وأنا أرفع الموضوع مباشرة لإدارة الفرع يشوفون لك الحل المناسب."
        )

    # لا توجد سياسة مسجّلة → رد بشري مطمئن
    return (
        f"{ya}فهمت، وما يصير إلا كل خير 🙏\n\n"
        "حسب الشروط العامة، عمليات الاسترجاع والاستبدال تخضع لحالة المنتج ومدة الشراء. "
        "بس حتى لو ما كانت حالتك ضمن الشروط، إدارة الفرع تقدر تشوف لك حل استثنائي.\n\n"
        "تبغى أرفع موضوعك لإدارة الفرع يتواصلون معك؟ قُل **نعم** أو **سجّل الشكوى**."
    )
def _complaint_mentions_return_policy(text: str) -> bool:
    t = text or ""
    return any(k in t for k in _COMPLAINT_RETURN_POLICY_KW)


def _user_confirms_complaint_after_policy(msg: str) -> bool:
    m = (msg or "").strip()
    if not m:
        return False
    ml = m.lower()
    if "لا" in ml[:8] or ml.startswith("لأ"):
        return False
    if any(k in m for k in ("سجل الشكوى", "سجّل الشكوى", "تسجيل شكوى")):
        return True
    if len(m) <= 40:
        if any(w in ml for w in ("نعم", "ايوه", "أيوة", "أكد", "اكد", "كمل", "أكمل", "اكمل", "واضح")):
            return True
        if ml in ("تم", "اوكي", "أوكي", "ok", "yes"):
            return True
    return False


def _user_cancels_policy_precheck(msg: str) -> bool:
    m = (msg or "").strip().lower()
    if not m or len(m) > 56:
        return False
    if m in ("تمام", "شكرا", "شكراً", "الله يعطيك العافية", "يعطيك العافية"):
        return True
    if m.startswith("لا") or m.startswith("لأ"):
        return True
    if "لا شكر" in m or "لا أبغى" in m or "لا ابغى" in m or "ملغي" in m or "إلغاء" in m:
        return True
    return False


def _handle_complaint_policy_precheck_turn(message: str, branch_list: list):
    """خطوة التأكيد بعد عرض ملخص السياسة."""
    cs = _cs()
    _display_name = cs._display_name
    _branch_label_for_chat = cs._branch_label_for_chat
    cp = session.get("complaint_policy_precheck")
    if not cp:
        return None
    msg = (message or "").strip()
    if _fresh_intent_exits_complaint_flow(msg):
        clear_complaint_session_for_topic_switch()
        return None
    if _user_cancels_policy_precheck(msg):
        clear_complaint_session_for_topic_switch()
        return jsonify(
            {
                "products": [],
                "message": f"تمام يا {_display_name()}، إذا احتجتنا نحن موجودين.",
                "intent": "general",
            }
        )
    if _user_confirms_complaint_after_policy(msg):
        issue = (cp.get("issue") or "").strip()
        br = cp.get("branch")
        session.pop("complaint_policy_precheck", None)
        if not issue:
            return jsonify(
                {
                    "products": [],
                    "message": "صار لغبطة بسيطة، اكتب نص الشكوى مرة ثانية.",
                    "intent": "general",
                }
            )
        complaint_id, branch_name, err, email_ok, ctype = _submit_new_complaint(
            issue, branch_name_override=br
        )
        if err:
            return jsonify({"products": [], "message": err, "intent": "complaint"})
        _exit_complaint_mode_after_successful_submit()
        nm = _complaint_warm_display_name()
        ya = f" يا {nm}" if nm else ""
        msg_ok = (
            f"ابشر{ya}، فهمنا موضوعك ✅ "
            "راح يتواصل معك فريق الفرع بأقرب وقت ويشوفون لك الحل المناسب 🙏"
        )
        row_c = _cs().get_db().get_complaint_row(complaint_id) if complaint_id else None
        ticket = (row_c.get("ticket_code") or "").strip() if row_c else ""
        if ticket:
            msg_ok += f"\n\nرقم تذكرتك: {ticket}\nاحفظه للاستعلام في أي وقت."
        if email_ok is False:
            msg_ok += " (تعذر إرسال نسخة البريد؛ تم حفظ الشكوى.)"
        payload = {
            "products": [],
            "message": msg_ok,
            "complaint_id": complaint_id,
            "intent": "complaint",
            "email_sent": bool(email_ok),
        }
        payload.update(_complaint_type_fields(ctype or "unspecified"))
        return jsonify(payload)

    return jsonify(
        {
            "products": [],
            "message": (
                f"يا {_display_name()}، عشان أسجّل الشكوى اكتب **نعم** أو **سجّل الشكوى**. "
                "ولو ما تحتاج، قل لا أو تمام."
            ),
            "intent": "complaint_policy_precheck",
        }
    )


def _try_complaint_wizard(message: str, branch_list: list):
    """
    متابعة حوار جمع الشكوى (تفاصيل + فرع) بأسلوب بشري.
    يُرجع jsonify(...) أو None إن لم يكن هناك معالج نشط.
    """
    cs = _cs()
    resolve_branch_from_message = cs.resolve_branch_from_message
    get_db = cs.get_db
    _display_name = cs._display_name
    _branch_selection_prompt = cs._branch_selection_prompt
    _CHAT_PENDING_BRANCH = cs._CHAT_PENDING_BRANCH
    w = session.get("complaint_wizard")
    if not w:
        return None
    msg = (message or "").strip()
    if _fresh_intent_exits_complaint_flow(msg):
        clear_complaint_session_for_topic_switch()
        return None

    # اكتشاف: العميل يتحدث عن شكوى سابقة → اطلب رقم التذكرة
    if _message_is_previous_complaint_followup(msg):
        clear_complaint_session_for_topic_switch()
        session["pending_complaint_lookup"] = True
        session["chat_current_intent"] = "complaint_ticket_lookup"
        return jsonify(
            {
                "products": [],
                "message": (
                    "أرسل لي رقم تذكرتك لو سمحت، يبدأ بـ TKT-\n"
                    "وإذا ما عندك الرقم، اكتب لي اسمك والفرع وأحاول أساعدك."
                ),
                "intent": "complaint_ticket_lookup",
            }
        )

    apology_merge = bool(w.pop("_apology_turn_merged", False))

    if w.get("apology_sent"):
        prev = (w.get("issue") or "").strip()
        merged_issue = f"{prev}\n{msg}".strip() if prev else msg
        br = (
            resolve_branch_from_message(msg)
            or resolve_branch_from_message(merged_issue)
            or w.get("branch")
        )
        if br:
            chat_ctx.remember_branch_by_name(br)
        w = {
            **w,
            "apology_sent": False,
            "issue": merged_issue,
            "branch": br,
            "step": w.get("step") or "collecting",
            "_apology_turn_merged": True,
        }
        session["complaint_wizard"] = w
        apology_merge = True

    sw = _explicit_switch_from_complaint(msg)
    if sw == "product":
        session.pop("complaint_wizard", None)
        session["chat_pending_action"] = None
        prod = _build_products_response(msg)
        if not prod.get("products"):
            return jsonify(dict(NO_PRODUCTS_PAYLOAD))
        return jsonify(prod)
    if sw == "location":
        session.pop("complaint_wizard", None)
        bn = resolve_branch_from_message(msg)
        if not bn:
            session["chat_pending_action"] = _CHAT_PENDING_BRANCH
            session["chat_current_intent"] = "location"
            return jsonify(
                {
                    "products": [],
                    "message": f"حاضر يا {_display_name()}، {_branch_selection_prompt()}",
                    "branches": branch_list,
                    "intent": "location",
                }
            )
        session["chat_pending_action"] = None
        session["chat_selected_branch"] = bn
        session["chat_current_intent"] = "location"
        return jsonify(_branch_location_json(bn, msg))

    w = session.get("complaint_wizard") or {}
    prev_issue = (w.get("issue") or "").strip()
    prev_branch = w.get("branch")
    step = w.get("step") or "collecting"

    if step == "need_branch":
        issue_text = prev_issue
        br = (
            resolve_branch_from_message(msg)
            or prev_branch
        )
    else:
        if apology_merge:
            issue_text = prev_issue
            br = (
                w.get("branch")
                or resolve_branch_from_message(msg)
                or resolve_branch_from_message(issue_text)
                or prev_branch
            )
        else:
            issue_text = f"{prev_issue}\n{msg}".strip() if prev_issue else msg
            br = (
                resolve_branch_from_message(msg)
                or resolve_branch_from_message(issue_text)
                or prev_branch
            )
    if br:
        chat_ctx.remember_branch_by_name(br)

    detail_ok = len(issue_text.strip()) >= _MIN_COMPLAINT_DETAIL
    branch_ok = bool(br) and get_db().get_branch_id_by_city_name(br) is not None

    # كشف مبكر داخل الـ wizard: استرجاع/استبدال → اعرض الشروط فوراً بدون انتظار
    if _is_return_exchange_complaint(issue_text) or _complaint_mentions_return_policy(issue_text):
        session["complaint_policy_precheck"] = {"issue": issue_text, "branch": br}
        session.pop("complaint_wizard", None)
        return jsonify(
            {
                "products": [],
                "message": _complaint_return_precheck_message(_display_name()),
                "intent": "complaint_policy_precheck",
            }
        )

    if detail_ok and not branch_ok:
        session["complaint_wizard"] = {
            "step": "need_branch",
            "issue": issue_text,
            "branch": None,
        }
        session["chat_pending_action"] = None
        return jsonify(
            {
                "products": [],
                "message": "في أي فرع حصلت المشكلة؟",
                "intent": "complaint_wizard",
                "branches": branch_list,
            }
        )

    if not detail_ok:
        session["complaint_wizard"] = {
            "step": "collecting",
            "issue": issue_text,
            "branch": br,
        }
        return jsonify(
            {
                "products": [],
                "message": "ممكن توضح المشكلة أكثر؟",
                "intent": "complaint_wizard",
                "branches": branch_list,
            }
        )

    session.pop("complaint_wizard", None)
    complaint_id, branch_name, err, email_ok, ctype = _submit_new_complaint(
        issue_text, branch_name_override=br
    )
    if err:
        return jsonify({"products": [], "message": err, "intent": "complaint"})
    _exit_complaint_mode_after_successful_submit()
    msg_ok = _message_after_complaint_saved(complaint_id)
    if email_ok is False:
        msg_ok += " (تعذر إرسال نسخة البريد؛ تم حفظ الشكوى في النظام.)"
    payload = {
        "products": [],
        "message": msg_ok,
        "complaint_id": complaint_id,
        "intent": "complaint",
        "email_sent": bool(email_ok),
    }
    payload.update(_complaint_type_fields(ctype or "unspecified"))
    return jsonify(payload)


def _handle_new_complaint_intent(message: str, branch_list: list):
    """
    أول رد على الشكوى.
    إذا كانت الشكوى عن استرجاع/استبدال → يعرض الشروط فوراً بدل الانتظار.
    غيرها → يبدأ wizard لجمع التفاصيل.
    """
    cs = _cs()
    resolve_branch_from_message = cs.resolve_branch_from_message
    _display_name = cs._display_name
    msg = (message or "").strip()
    br = resolve_branch_from_message(msg)
    if br:
        chat_ctx.remember_branch_by_name(br)

    # كشف مبكر: استرجاع أو استبدال → اعرض الشروط فوراً
    if _is_return_exchange_complaint(msg) or _complaint_mentions_return_policy(msg):
        session["complaint_policy_precheck"] = {"issue": msg, "branch": br}
        session["chat_current_intent"] = "complaint"
        return jsonify(
            {
                "products": [],
                "message": _complaint_return_precheck_message(_display_name()),
                "intent": "complaint_policy_precheck",
            }
        )

    session["complaint_wizard"] = {
        "apology_sent": True,
        "issue": msg,
        "branch": br,
        "step": "collecting",
    }
    return jsonify(
        {
            "products": [],
            "message": _warm_complaint_opening_need_branch(),
            "intent": "complaint_wizard",
            "branches": branch_list,
        }
    )


def _try_chat_active_complaint_turn(message: str, branch_list: list):
    """متابعة شكوى نشطة (chat_active_complaint_id): ملاحقات أو خروج لمنتج/موقع."""
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    resolve_branch_from_message = cs.resolve_branch_from_message
    _branch_selection_prompt = cs._branch_selection_prompt
    _CHAT_PENDING_BRANCH = cs._CHAT_PENDING_BRANCH
    cid = session.get("chat_active_complaint_id")
    if session.get("chat_current_intent") != "complaint" or not cid:
        return None
    if _fresh_intent_exits_complaint_flow(message):
        clear_complaint_session_for_topic_switch()
        return None
    sw = _explicit_switch_from_complaint(message)
    if sw == "product":
        clear_complaint_session_for_topic_switch()
        prod = _build_products_response(message)
        if not prod.get("products"):
            return jsonify(dict(NO_PRODUCTS_PAYLOAD))
        return jsonify(prod)
    if sw == "location":
        clear_complaint_session_for_topic_switch()
        branch_name = resolve_branch_from_message(message)
        if not branch_name:
            session["chat_pending_action"] = _CHAT_PENDING_BRANCH
            session["chat_current_intent"] = "location"
            return jsonify(
                {
                    "products": [],
                    "message": f"حاضر يا {_display_name()}، {_branch_selection_prompt()}",
                    "branches": branch_list,
                    "intent": "location",
                }
            )
        session["chat_pending_action"] = None
        session["chat_selected_branch"] = branch_name
        session["chat_current_intent"] = "location"
        return jsonify(_branch_location_json(branch_name, message))
    if not get_db().append_complaint_issue(cid, message):
        return jsonify(
            {
                "products": [],
                "message": f"يا {_display_name()}، حصل خطأ أثناء حفظ الملاحظة. حاول مرة ثانية.",
                "intent": "complaint",
            }
        )
    row = get_db().get_complaint_row(cid)
    full_issue = row["issue"] if row else message
    ct = classify_complaint_issue(full_issue)
    get_db().update_complaint_type(cid, ct)
    bl = session.get("complaint_branch_label") or ""
    bid = row["branch_id"] if row else None
    email_ok = _send_complaint_email(cid, bl, full_issue, is_append=True, branch_id=bid)
    note = (
        f"تمام يا {_display_name()}، لقينا ملاحظتك وسجّلناها للفريق "
        "وراح يشوفونها بأقرب وقت 🙏"
    )
    if email_ok is False:
        note += " (تعذر إرسال التنبيه بالبريد؛ تم حفظ الملاحظة في النظام.)"
    payload = {
        "products": [],
        "message": note,
        "complaint_id": cid,
        "intent": "complaint",
        "email_sent": bool(email_ok),
    }
    payload.update(_complaint_type_fields(ct))
    return jsonify(payload)


def _parse_escalation_target(msg: str) -> Optional[str]:
    t = (msg or "").strip().lower()
    t = t.replace("أ", "ا").replace("إ", "ا")
    admin_markers = (
        "الادارة العليا",
        "الإدارة العليا",
        "ادارة عليا",
        "عليا",
        "الاداره العليا",
        "ادارة عامة",
        "الإدارة العامة",
        "management",
    )
    branch_markers = (
        "الفرع",
        "فرع",
        "موظفين الفرع",
        "موظف الفرع",
        "ادارة الفرع",
        "إدارة الفرع",
        "للفروع",
    )
    if any(m in t for m in admin_markers):
        return "admin"
    if any(m in t for m in branch_markers):
        return "branch"
    if re.match(r"^\s*(1|١)\s*$", t):
        return "branch"
    if re.match(r"^\s*(2|٢)\s*$", t):
        return "admin"
    return None


def _send_complaint_routed_email(
    complaint_id: int,
    issue_text: str,
    branch_label: str,
    branch_id: Optional[int],
    target: str,
) -> bool:
    """
    target: 'branch' | 'admin' — إرسال إلى بريد الفرع أو الإدارة العليا + نسخة لـ SYSTEM_ALERTS_EMAIL.
    """
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    crow = get_db().get_complaint_row(complaint_id)
    ct_label = complaint_type_label_ar((crow or {}).get("complaint_type") or "unspecified")
    tgt_ar = "إدارة الفرع" if target == "branch" else "الإدارة العليا"
    body = (
        f"شكوى جديدة — توجيه: {tgt_ar}\n\n"
        f"رقم التذكرة: #{complaint_id}\n"
        f"نص الشكوى:\n{issue_text}\n\n"
        f"تصنيف: {ct_label}\n"
        f"الفرع: {branch_label or 'غير محدد'}\n"
        f"العميل (محادثة): {_display_name()}\n"
        f"الوقت: {now}\n"
    )
    subject = f"شكوى [{tgt_ar}] (#{complaint_id})"

    alerts = (SYSTEM_ALERTS_EMAIL or "").strip()
    admin_m = (ADMIN_EMAIL or "").strip()
    main_m = (MAIN_RECEIVER_EMAIL or "").strip()

    recipients: list[str] = []
    branch_email = None
    if branch_id is not None:
        branch_email = (get_db().get_branch_complaint_email(branch_id) or "").strip() or None
    if not branch_email and branch_label:
        bid = get_db().get_branch_id_by_city_name(branch_label)
        if bid is not None:
            branch_email = (get_db().get_branch_complaint_email(bid) or "").strip() or None
            branch_id = bid
    if not branch_email and branch_label:
        contact = get_branch(branch_label) or {}
        branch_email = (contact.get("manager_email") or "").strip() or None

    # التوجيه الذكي: كل الشكاوى تروح للفرع أولاً
    # إذا اختار العميل "الإدارة العليا" يُضاف ADMIN_EMAIL كنسخة إضافية فقط
    if branch_email:
        recipients.append(branch_email)
    else:
        logger.warning("complaint routing: missing branch email — fallback to main")
        if main_m:
            recipients.append(main_m)
        elif admin_m:
            recipients.append(admin_m)

    # لو target=admin أضف نسخة للإدارة لكن الفرع يبقى المسؤول الأول
    if target == "admin" and admin_m and admin_m not in recipients:
        recipients.append(admin_m)
        # غيّر عنوان البريد للإشارة لأنها مُصعَّدة
        subject = f"[مُصعَّدة] {subject}" if "subject" in dir() else subject

    if alerts:
        recipients.append(alerts)
    recipients = list(dict.fromkeys([r for r in recipients if r]))
    if not recipients:
        logger.error("complaint routing: no email recipients resolved")
        return False
    ok = send_email(recipients, subject, body)
    if not ok:
        logger.error("complaint routing: send_email failed for #%s", complaint_id)
    return ok


def _submit_complaint_rule_session(
    issue_text: str, branch_name: Optional[str], target: str
) -> tuple:
    """يحفظ الشكوى ويرسل البريد حسب التوجيه. يعيد (complaint_id, err, email_ok, ctype)."""
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    branch_id = get_db().get_branch_id_by_city_name(branch_name) if branch_name else None
    nm = _display_name()
    plain = (issue_text or "").strip()
    issue_body = f"[العميل: {nm}]\n{plain}" if nm not in ("أخوي", "حضرتك") else plain
    ctype = classify_complaint_issue(issue_body)
    dept = "فرع" if target == "branch" else "إدارة عليا"
    cust_name, cust_phone, cust_email = _customer_contact_from_session()
    ai_cls = (session.get("complaint_ai_classification_ar") or "").strip() or None
    complaint_id = get_db().add_complaint(
        user_id=_customer_user_id_for_records(),
        issue=issue_body,
        branch_id=branch_id,
        complaint_type=ctype,
        message=plain,
        branch_name=(branch_name or "").strip() or None,
        customer_name=cust_name,
        customer_phone=cust_phone,
        customer_email=cust_email,
        department=dept,
        complaint_ai_classification=ai_cls,
    )
    if complaint_id is None:
        return None, "تعذر حفظ الشكوى حالياً، حاول بعد قليل.", False, None
    bl = (branch_name or "").strip()
    email_ok = _send_complaint_routed_email(
        complaint_id, plain, bl, branch_id, target
    )
    return complaint_id, None, email_ok, ctype


def _try_complaint_rule_flow(message: str, branch_list: list):
    """
    تدفق شكوى بالقواعد (قبل AI): complaint_data في الجلسة.
    يُرجع jsonify أو None.
    """
    cs = _cs()
    get_db = cs.get_db
    resolve_branch_from_message = cs.resolve_branch_from_message
    _display_name = cs._display_name
    _branch_selection_prompt = cs._branch_selection_prompt
    msg = (message or "").strip()

    cd = session.get("complaint_data")
    if cd:
        if _fresh_intent_exits_complaint_flow(msg):
            clear_complaint_session_for_topic_switch()
            return None
        step = cd.get("step") or "need_branch"
        issue = (cd.get("issue") or "").strip()
        br_prev = cd.get("branch")

        if step == "need_branch":
            br = resolve_branch_from_message(msg)
            merged = f"{issue}\n{msg}".strip() if issue else msg
            if br:
                chat_ctx.remember_branch_by_name(br)
            if not br:
                session["complaint_data"] = {
                    "step": "need_branch",
                    "issue": merged,
                    "branch": None,
                }
                _sync_complaint_progress_session(invalid_target=False)
                return jsonify(
                    {
                        "products": [],
                        "message": _user_facing_complaint_text(msg, "need_branch", merged),
                        "intent": "complaint_rule",
                        "branches": branch_list,
                    }
                )
            detail_ok = len(merged.strip()) >= _MIN_COMPLAINT_DETAIL
            if not detail_ok:
                session["complaint_data"] = {
                    "step": "need_details",
                    "issue": merged,
                    "branch": br,
                }
                _sync_complaint_progress_session(invalid_target=False)
                return jsonify(
                    {
                        "products": [],
                        "message": _user_facing_complaint_text(msg, "need_details", merged),
                        "intent": "complaint_rule",
                        "branches": branch_list,
                    }
                )
            session["complaint_data"] = {
                "step": "need_target",
                "issue": merged,
                "branch": br,
            }
            session["chat_current_intent"] = "complaint"
            _sync_complaint_progress_session(invalid_target=False)
            return _jsonify_complaint_rule_need_target(br, branch_list)

        if step == "need_details":
            merged = f"{issue}\n{msg}".strip() if issue else msg
            br = br_prev or resolve_branch_from_message(merged)
            if br:
                chat_ctx.remember_branch_by_name(br)
            if not br:
                session["complaint_data"] = {
                    "step": "need_branch",
                    "issue": merged,
                    "branch": None,
                }
                _sync_complaint_progress_session(invalid_target=False)
                return jsonify(
                    {
                        "products": [],
                        "message": _user_facing_complaint_text(msg, "need_branch", merged),
                        "intent": "complaint_rule",
                        "branches": branch_list,
                    }
                )
            if len(merged.strip()) < _MIN_COMPLAINT_DETAIL:
                session["complaint_data"] = {
                    "step": "need_details",
                    "issue": merged,
                    "branch": br,
                }
                _sync_complaint_progress_session(invalid_target=False)
                return jsonify(
                    {
                        "products": [],
                        "message": _user_facing_complaint_text(msg, "need_details", merged),
                        "intent": "complaint_rule",
                        "branches": branch_list,
                    }
                )
            session["complaint_data"] = {
                "step": "need_target",
                "issue": merged,
                "branch": br,
            }
            _sync_complaint_progress_session(invalid_target=False)
            return _jsonify_complaint_rule_need_target(br, branch_list)

        if step == "need_target":
            # استرجاع/استبدال: لا نسأل عن الهدف — نوجّه مباشرة للفرع
            issue_check = (cd.get("issue") or "").strip()
            if _is_return_exchange_complaint(issue_check):
                tgt = "branch"
            else:
                tgt = _parse_escalation_target(msg)
            if not tgt:
                _sync_complaint_progress_session(invalid_target=True)
                return jsonify(
                    {
                        "products": [],
                        "message": _complaint_retry_target_message(msg),
                        "intent": "complaint_rule",
                        "branches": branch_list,
                    }
                )
            _sync_complaint_progress_session(invalid_target=False)
            br = br_prev or resolve_branch_from_message(issue)
            if not br:
                session["complaint_data"] = {
                    "step": "need_branch",
                    "issue": issue,
                    "branch": None,
                }
                _sync_complaint_progress_session(invalid_target=False)
                return jsonify(
                    {
                        "products": [],
                        "message": _user_facing_complaint_text(msg, "need_branch", issue),
                        "intent": "complaint_rule",
                        "branches": branch_list,
                    }
                )
            bid = get_db().get_branch_id_by_city_name(br) if br else None
            cid, err, email_ok, ctype = _submit_complaint_rule_session(issue, br, tgt)
            if err:
                return jsonify({"products": [], "message": err, "intent": "complaint"})
            _exit_complaint_mode_after_successful_submit()
            msg_ok = _complaint_rule_submitted_user_message(cid, tgt, email_ok)
            payload = {
                "products": [],
                "message": msg_ok,
                "complaint_id": cid,
                "intent": "complaint",
                "email_sent": bool(email_ok),
                "complaint_target": tgt,
            }
            if ctype:
                payload.update(_complaint_type_fields(ctype))
            return jsonify(payload)

    if session.get("complaint_wizard"):
        return None
    if session.get("complaint_policy_precheck"):
        return None

    if not complaint_score_is_direct(detect_complaint_score(message)):
        return None

    # اكتشاف: العميل يتحدث عن شكوى سابقة بدون رقم
    if _message_is_previous_complaint_followup(msg):
        session["pending_complaint_lookup"] = True
        session["chat_current_intent"] = "complaint_ticket_lookup"
        return jsonify(
            {
                "products": [],
                "message": (
                    "أرسل لي رقم تذكرتك لو سمحت، يبدأ بـ TKT-\n"
                    "وإذا ما عندك الرقم، اكتب لي اسمك والفرع وأحاول أساعدك."
                ),
                "intent": "complaint_ticket_lookup",
            }
        )

    br = resolve_branch_from_message(msg)
    if br:
        chat_ctx.remember_branch_by_name(br)
    issue_text = msg

    # كشف مبكر في مسار القواعد: استرجاع/استبدال → اعرض الشروط مباشرة
    if _is_return_exchange_complaint(issue_text) or _complaint_mentions_return_policy(issue_text):
        session["complaint_policy_precheck"] = {"issue": issue_text, "branch": br}
        session["chat_current_intent"] = "complaint"
        _sync_complaint_progress_session(invalid_target=False)
        return jsonify(
            {
                "products": [],
                "message": _complaint_return_precheck_message(_cs()._display_name()),
                "intent": "complaint_policy_precheck",
            }
        )

    detail_ok = len(issue_text.strip()) >= _MIN_COMPLAINT_DETAIL

    if not br:
        session["complaint_data"] = {
            "step": "need_branch",
            "issue": issue_text,
            "branch": None,
        }
        session["chat_current_intent"] = "complaint"
        _sync_complaint_progress_session(invalid_target=False)
        return jsonify(
            {
                "products": [],
                "message": _user_facing_complaint_text(msg, "need_branch", issue_text),
                "intent": "complaint_rule",
                "branches": branch_list,
            }
        )
    if not detail_ok:
        session["complaint_data"] = {
            "step": "need_details",
            "issue": issue_text,
            "branch": br,
        }
        session["chat_current_intent"] = "complaint"
        _sync_complaint_progress_session(invalid_target=False)
        return jsonify(
            {
                "products": [],
                "message": _user_facing_complaint_text(msg, "need_details", issue_text),
                "intent": "complaint_rule",
                "branches": branch_list,
            }
        )
    session["complaint_data"] = {
        "step": "need_target",
        "issue": issue_text,
        "branch": br,
    }
    session["chat_current_intent"] = "complaint"
    _sync_complaint_progress_session(invalid_target=False)
    return _jsonify_complaint_rule_need_target(br, branch_list)