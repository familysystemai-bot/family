# -*- coding: utf-8 -*-
"""
منطق الشات — تهيئة قاعدة البيانات، الجلسة، واستخراج الفرع.
توجيه مسار /chat_query في logic.chat_router؛ يُسجَّل تحليل أولي للنية في الجلسة
تحت المفتاح chat_intent_snapshot (قبل بناء الرد).

الذكاء الاصطناعي (LLM) في المشروع — دوره المحدود:
- يُستدعى من logic.llm_analyzer عند تفعيل LLM_ENABLED فقط.
- الوظيفة: تصنيف نية تقريبية (intent) واستخراج كلمات من نص المستخدم (نوع، لون، رجالي/نسائي)
  في حقل keywords — دون اختراع منتجات أو أقسام.
- لا يُستخدم لعرض منتجات أو للبحث في SQLite؛ البحث والقرار يتم عبر القواعد وقاعدة البيانات
  (انظر logic.product_repository و logic.product_service).
- ردود الشات للمستخدم تُبنى من بيانات حقيقية أو قوالب ثابتة، وليس من توليد يصف مخزوناً غير مؤكد.
"""
from __future__ import annotations

import os
from typing import Optional

from flask import session

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


def extract_branch_name(message):
    """تبحث في رسالة العميل عن اسم مدينة/فرع مسجل في قاعدة البيانات"""
    if not message:
        return None
    branches = get_db().get_all_branches()
    for branch in branches:
        branch_name = branch["city_name"]
        city_only = branch_name.replace("فرع", "").strip()
        if city_only in message or branch_name in message:
            return branch_name
    return None


# --- حالة محادثة الويب في الجلسة (بدون تغيير قاعدة البيانات) ---
_CHAT_PENDING_BRANCH = "await_branch_for_location"


def _branch_selection_prompt() -> str:
    lines = branch_list_lines()
    tail = "\n".join(lines) if lines else "• جدة\n• مكة\n• المدينة\n• خميس مشيط\n• قلوة"
    return f"أي فرع تقصد؟\n{tail}"


def _ensure_chat_user_session():
    if "user_id" not in session:
        session["user_id"] = "web_user_" + os.urandom(4).hex()


def _display_name():
    if _chat_context.has_declined_name():
        return "حضرتك"
    return (session.get("user_name") or "").strip() or "حضرتك"


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
    direct = extract_branch_name(message)
    if direct:
        return direct

    msg = message.strip()
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
