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
    """يُلحق طلب الاسم الاختياري بعد أول رد خدمة فعلي."""
    if not isinstance(payload, dict):
        return payload
    intent = payload.get("intent")
    if intent not in ("product", "location", "section", "recommendation"):
        return payload
    if has_declined_name() or (session.get("user_name") or "").strip():
        bump_service_turn()
        return payload
    turns = get_service_turns()
    if turns == 0:
        from logic.chat_rules import NAME_PROMPT_AFTER_SERVICE

        msg = (payload.get("message") or "").rstrip()
        payload["message"] = f"{msg}\n\n{NAME_PROMPT_AFTER_SERVICE}".strip()
        session[KEY_AWAIT_OPTIONAL_NAME] = True
    bump_service_turn()
    return payload


def has_declined_name() -> bool:
    return bool(session.get(KEY_NAME_DECLINED))
