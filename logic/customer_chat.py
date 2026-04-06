# -*- coding: utf-8 -*-
"""
ربط جلسة الشات بجدول customers (بريد، فرع، هاتف، موافقة تسويق داخلية فقط).
سؤال العروض يُعرض بعد شكر/وداع فقط (انظر prepare_closing_marketing_offer + chat_router).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from flask import Response, jsonify, session

import logic.chat_service as cs
from logic import chat_context as chat_ctx

MARKETING_PROMPT = "🌟 حياك الله، تحب نرسل لك العروض والتخفيضات أول بأول؟"

_MARKETING_YES_EXACT = frozenset(
    {
        "نعم",
        "ايه",
        "اي",
        "تم",
        "موافق",
        "اوكي",
        "أوكي",
        "ok",
        "yes",
        "يلا",
        "اكيد",
        "أكيد",
        "ابغى",
        "أبغى",
        "احب",
        "أحب",
        "ايوه",
        "ايوة",
        "هلا",
        "تمام",
    }
)


def _norm_ar(s: str) -> str:
    t = (s or "").strip().lower()
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        t = t.replace(a, b)
    return t


def _strip_trailing_punct(t: str) -> str:
    return t.strip("،.!؟ \t\r\n")


def _is_marketing_yes(message: str) -> bool:
    t = _strip_trailing_punct(_norm_ar(message))
    if not t:
        return False
    if t in _MARKETING_YES_EXACT:
        return True
    if len(t) <= 4 and t in ("اي", "تم", "نعم"):
        return True
    if len(t) <= 8 and t.startswith("نعم"):
        return True
    if len(t) <= 10 and t.startswith("موافق"):
        return True
    return False


def _is_marketing_no(message: str) -> bool:
    t = _norm_ar(message)
    if "لا شكر" in t or "مابي" in t.replace(" ", "") or "ما ابغى" in t.replace(" ", ""):
        return True
    if "مش موافق" in t:
        return True
    u = _strip_trailing_punct(t)
    return u in ("لا", "لأ", "لاء")


def _in_complaint_flow() -> bool:
    return bool(session.get("complaint_wizard") or session.get("chat_active_complaint_id"))


def apply_request_basics(data: dict) -> None:
    """مزامنة user_contact و user_name من الطلب إلى الجلسة (قبل مسارات الشات)."""
    proposed = (data.get("user_name") or "").strip()
    uc = (data.get("user_contact") or "").strip()
    if uc:
        session["user_contact"] = uc[:320]
    account_logged_in = bool(data.get("account_logged_in"))
    cs._apply_session_display_name(proposed, account_logged_in=account_logged_in)


def _extract_email(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s or "@" not in s:
        return None
    token = re.split(r"[\s,;]+", s)[0].strip().lower()
    if "@" not in token:
        return None
    left, right = token.rsplit("@", 1)
    if not left or "." not in right:
        return None
    return token[:200] if token else None


def _digits_only(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _looks_like_phone(raw: str) -> bool:
    d = _digits_only(raw)
    return len(d) >= 8


def _resolve_branch_id_from_message(message: str, db) -> Optional[int]:
    bn = cs.resolve_branch_from_message(message) or chat_ctx.get_last_branch()
    if not bn:
        return None
    return db.get_branch_id_by_city_name(bn)


def _resolve_customer_id(db) -> Optional[int]:
    cid = session.get("customer_id")
    if cid:
        try:
            return int(cid)
        except (TypeError, ValueError):
            pass
    em = _extract_email(session.get("user_contact") or "")
    if not em:
        return None
    row = db.get_customer_by_email(em)
    if not row:
        return None
    i = int(row["id"])
    session["customer_id"] = i
    return i


def prepare_closing_marketing_offer(db) -> None:
    """
    بعد نية شكر أو وداع: اعرض سؤال العروض فقط إن وُجد عميل مسجّل بالبريد
    ولم يوافق/يرفض نهائياً.
    """
    if _in_complaint_flow():
        return
    cid = _resolve_customer_id(db)
    if not cid:
        return
    row = db.get_customer_by_id(cid)
    if not row:
        return
    if int(row.get("prefers_marketing") or 0):
        return
    if int(row.get("declined_marketing_prompt") or 0):
        return
    session["_offer_marketing_followup"] = True


def sync_customer_from_session(db, message: str) -> None:
    if _in_complaint_flow():
        return
    contact = (session.get("user_contact") or "").strip()
    if not contact:
        return
    name = (session.get("user_name") or "").strip() or "ضيف"
    bid = _resolve_branch_id_from_message(message, db)

    email = _extract_email(contact)
    if email:
        row = db.customer_ensure_by_email(name, email, bid)
        if row:
            session["customer_id"] = int(row["id"])
            if bid:
                db.customer_set_branch(session["customer_id"], bid)
            dia = (session.get("chat_dialect") or "default").strip() or "default"
            prod_snap = session.get("chat_last_product") or {}
            prod_name = (prod_snap.get("name") or "").strip() or None
            db.customer_touch_engagement(
                int(row["id"]), dialect=dia, product_label=prod_name
            )
        return

    if _looks_like_phone(contact) and session.get("customer_id"):
        cid = int(session["customer_id"])
        digits = _digits_only(contact)
        if digits:
            db.customer_set_phone(cid, digits)
        if bid:
            db.customer_set_branch(cid, bid)
        dia = (session.get("chat_dialect") or "default").strip() or "default"
        prod_snap = session.get("chat_last_product") or {}
        prod_name = (prod_snap.get("name") or "").strip() or None
        db.customer_touch_engagement(cid, dialect=dia, product_label=prod_name)


def try_marketing_consent_reply(message: str, db) -> Optional[Any]:
    if not session.get("awaiting_marketing_consent"):
        return None
    if _in_complaint_flow():
        session.pop("awaiting_marketing_consent", None)
        return None
    cid = session.get("customer_id")
    if not cid:
        em = _extract_email(session.get("user_contact") or "")
        if em:
            row = db.get_customer_by_email(em)
            if row:
                cid = int(row["id"])
                session["customer_id"] = cid
    if not cid:
        session.pop("awaiting_marketing_consent", None)
        return None
    if _is_marketing_yes(message):
        db.customer_set_prefers_marketing(int(cid), True)
        session.pop("awaiting_marketing_consent", None)
        return jsonify(
            {
                "products": [],
                "message": "تمام 🌟 سجّلنا رغبتك باستلام العروض والتخفيضات أول بأول.",
                "intent": "customer_preferences",
            }
        )
    if _is_marketing_no(message):
        db.customer_decline_marketing_prompt(int(cid))
        session.pop("awaiting_marketing_consent", None)
        return jsonify(
            {
                "products": [],
                "message": "تمام، ما نرسل لك عروضاً إلا إذا طلبتها لاحقاً.",
                "intent": "customer_preferences",
            }
        )
    return None


def attach_marketing_followup_if_needed(resp: Any) -> Any:
    if not session.pop("_offer_marketing_followup", False):
        return resp
    if _in_complaint_flow():
        return resp
    if not isinstance(resp, Response):
        return resp
    data = resp.get_json(silent=True)
    if not isinstance(data, dict):
        return resp
    existing = (data.get("followup_message") or "").strip()
    if existing:
        data["followup_message"] = f"{existing}\n\n{MARKETING_PROMPT}"
    else:
        data["followup_message"] = MARKETING_PROMPT
    session["awaiting_marketing_consent"] = True
    return jsonify(data)
