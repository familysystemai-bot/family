# -*- coding: utf-8 -*-
"""
منطق الشات — تهيئة قاعدة البيانات، الجلسة، واستخراج الفرع.
توجيه مسار /chat_query في logic.chat_router؛ يُسجَّل تحليل أولي للنية في الجلسة
تحت المفتاح chat_intent_snapshot (قبل بناء الرد).

الذكاء الاصطناعي في المشروع:
- OpenAI (عند وجود OPENAI_API_KEY): استخراج حقول بحث قبل SQLite عبر logic.ai_fallback — لا يغيّر نية التوجيه القائمة على القواعد.
- محلل LLM اختياري (logic.llm_analyzer عند LLM_ENABLED) — بدون مزوّد محلي منسوخ.
- عرض المنتجات يبقى من بيانات قاعدة البيانات فعلياً (انظر logic.product_repository و logic.product_service).
- ردود الشات للمستخدم تُبنى من بيانات حقيقية أو قوالب ثابتة، وليس من توليد يصف مخزوناً غير مؤكد.
"""
from __future__ import annotations

import os
from typing import Optional

from flask import has_request_context, session

from logic.chat_rules import is_acceptable_display_name
from logic.database import DatabaseManager
from logic import chat_context as _chat_context
from site_config.branches import branch_list_lines

_db: Optional[DatabaseManager] = None


def init_chat_service(database: DatabaseManager) -> None:
    global _db
    _db = database


def get_db() -> DatabaseManager:
    assert _db is not None
    return _db


def allowed_file(filename: str) -> bool:
    from config import ALLOWED_EXTENSIONS

    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def normalize_message_for_branch_search(message: Optional[str]) -> str:
    """
    تطبيع صياغي لتحسين مطابقة الفروع (مثل: بمكة / في مكة → تمييز «مكة» بلا حرف جر).
    لا يغيّر معنى طلب المنتجات بشكل جوهري؛ يُستخدم قبل استخراج الفرع وتحليل النية.
    """
    if not message or not str(message).strip():
        return ""
    t = str(message).strip()
    t = t.replace("ٱ", "ا").replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    pairs = (
        ("بمكة", " مكة "),
        ("بمكه", " مكة "),
        ("في مكة", " مكة "),
        ("في مكه", " مكة "),
        ("من مكة", " مكة "),
        ("للمكة", " مكة "),
        ("بجدة", " جدة "),
        ("بجده", " جدة "),
        ("في جدة", " جدة "),
        ("في جده", " جدة "),
        ("بالمدينة", " المدينة "),
        ("في المدينة", " المدينة "),
        ("في المدينه", " المدينة "),
        ("للمدينة", " المدينة "),
        ("بخميس مشيط", " خميس مشيط "),
        ("خميس مشيط", " خميس مشيط "),
        ("بقلوة", " قلوة "),
        ("في قلوة", " قلوة "),
        ("عندكم فرع", " فرع "),
        ("عندك فرع", " فرع "),
    )
    for a, b in pairs:
        if a in t:
            t = t.replace(a, b)
    return " ".join(t.split())


def extract_branch_name(message):
    """تبحث في رسالة العميل عن اسم مدينة/فرع مسجل في قاعدة البيانات"""
    if not message:
        return None
    blob = normalize_message_for_branch_search(message) + " " + (message or "")
    branches = get_db().get_all_branches()
    for branch in branches:
        branch_name = branch["city_name"]
        city_only = branch_name.replace("فرع", "").strip()
        if city_only in blob or branch_name in blob:
            return branch_name
    return None


# --- حالة محادثة الويب في الجلسة (بدون تغيير قاعدة البيانات) ---
_CHAT_PENDING_BRANCH = "await_branch_for_location"


def _name_likely_female(display_name: str) -> bool:
    """تخمين بسيط للتذكير بلقب أنثوي — دون ضمان."""
    n = (display_name or "").strip()
    if len(n) < 2:
        return False
    if n.endswith("ة") and len(n) >= 3:
        return True
    return n in (
        "فاطمة", "سارة", "نورة", "مريم", "هدى", "رنا", "لينا", "هند", "أمل", "نجلاء",
    )


def personalized_service_offer() -> str:
    """
    سطر خدمة مهذب — بدون سرد فروع ولا صيغة «أساعدك فيه».
    """
    nm = _display_name().strip()
    if not nm or nm in ("عميلنا", "العميل", "زائر", "ضيف"):
        return "تفضل."
    if nm in ("أخوي", "أستاذ"):
        return f"تفضل يا {nm}."
    hon = "أستاذة" if _name_likely_female(nm) else "أستاذ"
    return f"تفضل يا {hon} {nm}."


def branch_clarify_block(heading: str) -> str:
    """عنوان قصير (مثل: أي فرع تقصد؟) + سطر خدمة مخصص."""
    h = (heading or "").strip()
    tail = personalized_service_offer()
    return f"{h}\n{tail}" if h else tail


def _branch_selection_prompt() -> str:
    """
    توافق رجعي مع الكود القديم: نفس personalized_service_offer دون قوائم مدن.
    """
    return personalized_service_offer()


def _ensure_chat_user_session():
    if "user_id" not in session:
        session["user_id"] = "web_user_" + os.urandom(4).hex()


def _display_name():
    if not has_request_context():
        return "أخوي"
    if _chat_context.has_declined_name():
        return "أخوي"
    return (session.get("user_name") or "").strip() or "أخوي"


def _apply_session_display_name(proposed: str, *, account_logged_in: bool) -> None:
    """اسم الحساب يستبدل الاسم المؤقت؛ بدون حساب يُضبط الاسم مرة واحدة عند أول إرسال."""
    proposed = (proposed or "").strip()
    if len(proposed) < 2:
        return
    if account_logged_in:
        session["user_name"] = proposed[:120]
        session["awaiting_user_name"] = False
    elif not session.get("user_name"):
        if not is_acceptable_display_name(proposed):
            return
        session["user_name"] = proposed[:120]
        session["awaiting_user_name"] = False


def resolve_branch_from_message(message: str):
    """استخراج اسم الفرع كما هو مخزن في branches.city_name لموحدة get_branch_id_by_city_name."""
    if not message or not message.strip():
        return None
    msg = normalize_message_for_branch_search(message.strip()) + " " + message.strip()
    direct = extract_branch_name(message)
    if direct:
        return direct

    msg = msg.strip()
    branches = get_db().get_all_branches()
    candidates = []
    for b in branches:
        cn = (b.get("city_name") or "").strip()
        if not cn:
            continue
        alts = {cn, cn.replace("فرع", "").strip()}
        for alt in alts:
            alt2 = alt.replace("(", " ").replace(")", " ").replace("  ", " ").strip()
            if alt in msg or alt2 in msg or msg in alt or msg in alt2:
                candidates.append(cn)
                break
            parts = [p for p in alt2.split() if len(p) >= 2]
            if parts and all(p in msg for p in parts):
                candidates.append(cn)
                break
            matched = sum(1 for p in parts if p in msg)
            if matched >= 1 and len(msg) <= 14 and len(parts) <= 3:
                candidates.append(cn)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # تجنّب التكرار مع الحفاظ على أطول تطابق (مثل المدينة + الدائري)
    return max(set(candidates), key=lambda x: (len(x), x))


def _branch_label_for_chat(city_name: str) -> str:
    cn = (city_name or "").strip()
    if not cn:
        return ""
    if cn.startswith("فرع"):
        return cn
    return f"فرع {cn}"


def chat_query():
    """معالج مسار /chat_query — يُستدعى من app.route؛ التحليل في dispatch_chat_query."""
    _ensure_chat_user_session()
    from logic.chat_router import dispatch_chat_query

    return dispatch_chat_query()
