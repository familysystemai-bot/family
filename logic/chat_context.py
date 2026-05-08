# -*- coding: utf-8 -*-
"""
ذاكرة سياق الشات في الجلسة فقط (لا حفظ في قاعدة البيانات).
- last_branch / last_intent / last_product_or_section
- تنسيق مع chat_selected_branch عند تحديد فرع.
"""
from __future__ import annotations

from typing import Any, List, Optional

from flask import session

KEY_LAST_BRANCH = "chat_last_branch"
KEY_LAST_INTENT = "chat_last_intent"
KEY_LAST_PRODUCT_OR_SECTION = "chat_last_product_or_section_label"
KEY_NAME_DECLINED = "chat_name_declined"
KEY_SERVICE_TURNS = "chat_service_turns"
KEY_NEXT_OPEN_ISO = "chat_last_next_open_iso"
KEY_AWAIT_OPTIONAL_NAME = "chat_awaiting_optional_name"
KEY_CONVERSATION_HISTORY = "chat_conversation_history"
KEY_CURRENT_REQUEST = "chat_current_request"
# آخر رسالة عميل عن دوام/موقع/… قبل سؤال «أي فرع؟» — لدمجها مع رد المدينة القصير
KEY_PENDING_BRANCH_KIND_SOURCE = "chat_pending_branch_kind_source"

# ─── حد أقصى لعدد الرسائل المحفوظة في الجلسة ───
_MAX_HISTORY_TURNS = 20


def set_pending_branch_kind_source(user_message: str) -> None:
    """يُحفظ عند طلب توضيح الفرع ليعاد استخدام النص مع ردّ المدينة لاحقاً (دوام لا يُفسَّر كموقع)."""
    t = (user_message or "").strip()
    if t:
        session[KEY_PENDING_BRANCH_KIND_SOURCE] = t[:500]


def peek_pending_branch_kind_source() -> Optional[str]:
    v = session.get(KEY_PENDING_BRANCH_KIND_SOURCE)
    return (str(v).strip() if v else None) or None


def pop_pending_branch_kind_source() -> Optional[str]:
    v = session.pop(KEY_PENDING_BRANCH_KIND_SOURCE, None)
    return (str(v).strip() if v else None) or None


def merged_message_with_pending_branch_kind(current_message: str) -> str:
    """يدمج آخر استفسار (مثل: متى تفتحون) مع رد المدينة القصير لاحقاً."""
    p = peek_pending_branch_kind_source()
    c = (current_message or "").strip()
    if not p:
        return c
    return f"{p} {c}".strip()


def remember_branch_by_name(city_name: Optional[str]) -> None:
    """يحدّث آخر فرع مذكور في السياق (اسم كما في branches.city_name)."""
    cn = (city_name or "").strip()
    if not cn:
        return
    session[KEY_LAST_BRANCH] = cn
    session["chat_selected_branch"] = cn


def set_last_intent(intent: str) -> None:
    if intent:
        session[KEY_LAST_INTENT] = (intent or "").strip()[:80]


def set_last_product_or_section(label: Optional[str]) -> None:
    t = (label or "").strip()
    if t:
        session[KEY_LAST_PRODUCT_OR_SECTION] = t[:200]
    else:
        session.pop(KEY_LAST_PRODUCT_OR_SECTION, None)


def get_last_branch() -> Optional[str]:
    v = session.get(KEY_LAST_BRANCH) or session.get("chat_selected_branch")
    return (str(v).strip() if v else None) or None


def on_product_list_shown(products: List[dict], intent: str) -> None:
    """يُستدعى بعد بناء قائمة منتجات للعرض في الشات."""
    if not products:
        return
    first = products[0]
    pid = first.get("id")
    if not pid:
        return
    try:
        from logic.chat_service import get_db

        branches = get_db().get_product_branches(int(pid)) or []
        if branches:
            bn = (branches[0].get("name") or "").strip()
            if bn:
                remember_branch_by_name(bn)
    except Exception:
        pass
    set_last_intent(intent)
    nm = (first.get("name") or "").strip()
    if nm:
        set_last_product_or_section(nm)


def is_islamic_salam_message(message: str) -> bool:
    from logic.chat_rules import is_islamic_salam_message as _salam

    return _salam(message)


def set_next_opening_from_dt(dt) -> None:
    """يُستدعى عند حساب أقرب افتتاح (للمتابعة «يعني الساعة كم»)."""
    if dt is None:
        session.pop(KEY_NEXT_OPEN_ISO, None)
        return
    try:
        session[KEY_NEXT_OPEN_ISO] = dt.isoformat()
    except Exception:
        pass


def get_next_opening_dt():
    from datetime import datetime

    raw = session.get(KEY_NEXT_OPEN_ISO)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def bump_service_turn() -> None:
    session[KEY_SERVICE_TURNS] = int(session.get(KEY_SERVICE_TURNS) or 0) + 1


def get_service_turns() -> int:
    return int(session.get(KEY_SERVICE_TURNS) or 0)


def is_decline_name_message(message: str) -> bool:
    """المستخدم لا يريد تسمية."""
    t = (message or "").strip().lower()
    if not t:
        return False
    markers = (
        "ما ابغى",
        "ما ابغا",
        "ما أبغى",
        "ما ابدي",
        "عادي",
        "بدون اسم",
        "بدون اسم.",
        "لا ابغى",
        "لا أبغى",
        "لا ابغا",
        "ما ابي اسم",
        "ما أبي اسم",
        "مابي اسم",
        "لا اسم",
    )
    return any(m in t for m in markers)


def mark_name_declined() -> None:
    session[KEY_NAME_DECLINED] = True
    session.pop("user_name", None)
    session["awaiting_user_name"] = False
    session.pop(KEY_AWAIT_OPTIONAL_NAME, None)


def enrich_service_message(payload: dict) -> dict:
    """
    كانت تُلحق طلب الاسم الاختياري — تم إلغاء الطلب لأنه مزعج.
    الاسم يُؤخذ من الجلسة إذا سجّل العميل دخوله.
    """
    if not isinstance(payload, dict):
        return payload
    bump_service_turn()
    return payload


def has_declined_name() -> bool:
    return bool(session.get(KEY_NAME_DECLINED))


# ─────────────────────────────────────────────────────────────────────────────
# سياق المحادثة: تاريخ الرسائل + الطلب الحالي
# ─────────────────────────────────────────────────────────────────────────────

def add_user_message(message: str, intent: str = "") -> None:
    """يضيف رسالة العميل لتاريخ المحادثة في الجلسة."""
    history: list = session.get(KEY_CONVERSATION_HISTORY) or []
    history.append({"role": "user", "text": (message or "")[:500], "intent": intent})
    if len(history) > _MAX_HISTORY_TURNS * 2:
        history = history[-_MAX_HISTORY_TURNS * 2:]
    session[KEY_CONVERSATION_HISTORY] = history


def add_bot_message(message: str, intent: str = "") -> None:
    """يضيف رد البوت لتاريخ المحادثة في الجلسة."""
    history: list = session.get(KEY_CONVERSATION_HISTORY) or []
    history.append({"role": "bot", "text": (message or "")[:500], "intent": intent})
    if len(history) > _MAX_HISTORY_TURNS * 2:
        history = history[-_MAX_HISTORY_TURNS * 2:]
    session[KEY_CONVERSATION_HISTORY] = history


def get_conversation_history() -> list:
    """يعيد آخر رسائل المحادثة."""
    return session.get(KEY_CONVERSATION_HISTORY) or []


def get_conversation_summary(last_n: int = 6) -> str:
    """
    يبني ملخص نصي لآخر N رسائل لتمريرها للـ AI كسياق.
    """
    history = get_conversation_history()
    if not history:
        return ""
    recent = history[-last_n:]
    lines = []
    for msg in recent:
        role = "العميل" if msg.get("role") == "user" else "البوت"
        text = (msg.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text[:200]}")
    return "\n".join(lines)


def set_current_request(request_text: str) -> None:
    """يحفظ الطلب الحالي للعميل (للسياق عبر الرسائل)."""
    session[KEY_CURRENT_REQUEST] = (request_text or "")[:500]


def get_current_request() -> str:
    """يعيد آخر طلب حالي محفوظ."""
    return session.get(KEY_CURRENT_REQUEST) or ""


def clear_current_request() -> None:
    session.pop(KEY_CURRENT_REQUEST, None)
