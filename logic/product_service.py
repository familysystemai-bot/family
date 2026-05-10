# -*- coding: utf-8 -*-
"""
منطق المنتجات في الشات — يُستدعى من chat_service.
يجب استدعاء الدوال بعد اكتمال تحميل chat_service (تفادي استيراد دائري).
"""
from __future__ import annotations

import copy
import os
import re
from typing import List, Optional

from flask import jsonify, session

from logic.chat_semantic_expand import all_product_search_needles
from logic.dialect_responses import dialect_message
from logic.keywords import PRODUCT_HINTS
from logic.product_query_parse import (
    blob_matches_gender_filter,
    extract_gender_filter,
    normalize_for_product_search,
)
from logic.product_repository import (
    _normalize_arabic_for_search as _norm_ar_for_kw,
    filter_rows_keyword_in_product_name,
)


def _cs():
    import logic.chat_service as m

    return m


NO_PRODUCTS_MESSAGE = ""
NO_PRODUCTS_PAYLOAD = {
    "intent": "no_products",
    "message": NO_PRODUCTS_MESSAGE,
    "products": [],
}


def _send_product_inquiry_to_branch(product_query: str) -> bool:
    """
    يرسل استفسار منتج للفرع بالإيميل لما ما يلقاه في قاعدة البيانات.
    يسجّل الطلب في product_requests أيضاً.
    """
    from logic.notification_guards import should_send_product_miss_notification

    if not should_send_product_miss_notification(product_query):
        import logging

        logging.getLogger(__name__).info(
            "تخطّي تنبيه بريدي — الرسالة لا تبدو طلب منتج حقيقياً (%s)",
            (product_query or "")[:120],
        )
        return False
    try:
        cs = _cs()
        db = cs.get_db()
        dn = cs._display_name()
        query = (product_query or "").strip()[:300]
        if not query:
            return False

        # حفظ في product_requests
        uid = "web_user_unknown"
        try:
            from flask import session as _sess
            cid = _sess.get("customer_id")
            if cid:
                uid = f"customer:{int(cid)}"
        except Exception:
            pass
        db.add_product_request(uid, query)

        # بناء الإيميل
        from logic.mail_service import send_email
        from config import MAIN_RECEIVER_EMAIL
        from datetime import datetime

        branches = db.get_all_branches()
        branch_emails = []
        for b in branches:
            bid = b.get("id")
            if bid:
                em = (db.get_branch_complaint_email(int(bid)) or "").strip()
                if em:
                    branch_emails.append(em)
        if not branch_emails and MAIN_RECEIVER_EMAIL:
            branch_emails = [MAIN_RECEIVER_EMAIL]
        if not branch_emails:
            return False

        subject = f"استفسار عن منتج من العميل {dn}"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        body = (
            "استفسار منتج — " + now_str + "\n\n"
            + "العميل: " + dn + "\n"
            + "طلبه: " + query + "\n\n"
            + "المنتج غير موجود في قاعدة البيانات.\n"
            + "يرجى الرد على العميل من لوحة التحكم أو إضافة المنتج."
        )
        return send_email(branch_emails, subject, body)
    except Exception:
        return False
# عند نية منتج من AI دون مطابقة DB — طلب توضيح بدل رد عام/مضلّل
PRODUCT_CLARIFY_MESSAGE = "وش المنتج اللي تقصده؟"
PRODUCT_CLARIFY_PAYLOAD = {
    "intent": "product_clarify",
    "message": PRODUCT_CLARIFY_MESSAGE,
    "products": [],
}
# نفس الرد القصير عند عدم توفر مطابقة بحث / توصية (بدون نصوص طويلة أو تخمين)
_PRODUCT_UNAVAILABLE_MSG = NO_PRODUCTS_MESSAGE

_LAST_PRODUCT_SHOWN_MSG = "هذا آخر الموجود حالياً 👍"

# طلب عرض منتج تالي من الدفعة المخزّنة في الجلسة (بدون تغيير SQL أو منطق البحث)
_NEXT_PRODUCT_TRIGGER_PHRASES = (
    "في غيره",
    "فيه غيره",
    "غيره",
    "كمان",
    "زيد",
    "ورني اكثر",
    "ورني أكثر",
    "وريني اكثر",
    "وريني أكثر",
    "ابي لون",
    "أبي لون",
    "ابغى لون",
    "ابغي لون",
    "لون ثاني",
    "لون غير",
    "موديل ثاني",
    "موديل غير",
    "غير موديل",
)

# كلمات تعني "أشوف المنتجات" أو "استعراض"
_BROWSE_PRODUCT_PHRASES = (
    "أشوف الموديلات",
    "اشوف الموديلات",
    "شوف الموديلات",
    "الموديلات",
    "موديلات",
    "أشوف المنتجات",
    "اشوف المنتجات",
    "المنتجات المتاحة",
    "وش عندكم",
    "وش موجود",
    "وش متوفر",
    "ايش عندكم",
    "عرض المنتجات",
    "أشوف العروض",
    "اشوف العروض",
)


def _looks_like_next_product_request(message: str) -> bool:
    t = (message or "").strip()
    if not t:
        return False
    tn = t.replace("؟", "").replace("?", "").strip()
    for p in _NEXT_PRODUCT_TRIGGER_PHRASES:
        if p in tn or p in t:
            return True
    return False


def _looks_like_browse_request(message: str) -> bool:
    """الرسالة تعني "أبغى أشوف المنتجات/الموديلات" بدون بحث محدد."""
    t = (message or "").strip()
    if not t:
        return False
    return any(p in t for p in _BROWSE_PRODUCT_PHRASES)


def _apply_step_by_step_slice(
    out_products: list, list_intent: str, max_visible: int = 2
) -> List[dict]:
    """
    يعرض حتى max_visible منتجاً (افتراضياً 2) ويخزّن الباقي في الجلسة.
    """
    if not out_products:
        session["remaining_products"] = []
        session["remaining_products_intent"] = list_intent
        return []
    mv = max(1, min(int(max_visible or 2), 6))
    session["remaining_products"] = [copy.deepcopy(x) for x in out_products[mv:]]
    session["remaining_products_intent"] = list_intent
    return out_products[:mv]


def _try_next_remaining_product_response(message: str):
    """
    إذا طلب المستخدم منتجاً تالياً من الدفعة السابقة نُرسل صفاً واحداً من remaining_products.
    """
    if not _looks_like_next_product_request(message):
        return None
    rem = session.get("remaining_products")
    if rem is None:
        return None
    intent = (session.get("remaining_products_intent") or "product").strip() or "product"
    if len(rem) == 0:
        return jsonify(
            {
                "products": [],
                "message": _LAST_PRODUCT_SHOWN_MSG,
                "intent": intent,
            }
        )
    next_p = copy.deepcopy(rem[0])
    session["remaining_products"] = rem[1:]
    cs = _cs()
    get_db = cs.get_db
    try:
        pid = int(next_p["id"])
    except (TypeError, ValueError):
        return None
    session["last_products"] = list(
        dict.fromkeys((session.get("last_products") or []) + [pid])
    )
    _save_chat_last_product_snapshot(
        next_p,
        get_db().get_product_variants(pid) or [],
    )
    session["pending_product_intent"] = True
    try:
        from logic import chat_context as _chat_ctx

        _chat_ctx.on_product_list_shown([next_p], intent)
    except Exception:
        pass
    return jsonify(
        {
            "products": [next_p],
            "message": "",
            "intent": intent,
        }
    )

_PRODUCT_COLOR_HINTS = (
    "بنفسجي",
    "موف",
    "وردي",
    "أبيض",
    "ابيض",
    "أسود",
    "اسود",
    "أحمر",
    "احمر",
    "كحلي",
    "ذهبي",
    "فضي",
    "بيج",
    "رمادي",
    "أخضر",
    "اخضر",
    "أزرق",
    "ازرق",
    "أصفر",
    "اصفر",
    "برتقالي",
    "بني",
    "نحاسي",
    "فيروزي",
    "سماوي",
)

# متابعة الأقسام (بعد last_section أو اختيار من قائمة)
# تأكيد اهتمام بالمنتج بعد عرض نتائج أولية (بدون فروع) — رسائل قصيرة أو صريحة
_PRODUCT_INTEREST_CONFIRM = frozenset(
    {
        "ايوه",
        "أيوه",
        "اييه",
        "أييه",
        "اي",
        "نعم",
        "يس",
        "يب",
        "تمام",
        "طيب",
        "موافق",
        "اوكي",
        "أوكي",
        "ok",
        "yes",
        "ابغاه",
        "ابغاها",
        "ابغيه",
        "أبغاه",
        "ابغا",
        "اريده",
        "أريده",
        "اخذه",
        "اخذها",
        "حابه",
        "حابها",
        "اكيد",
        "أكيد",
    }
)

_SECTION_FOLLOWUP_FILLER = frozenset(
    {
        "طيب",
        "تمام",
        "اوكي",
        "أوكي",
        "ok",
        "ماشي",
        "حاضر",
        "تم",
        "نعم",
        "لا",
        "يس",
        "هلا",
        "الو",
    }
)
_SECTION_FOLLOWUP_DESCRIPTION_WORDS = (
    "سهرة",
    "السهرة",
    "رسمي",
    "أسود",
    "أبيض",
    "أحمر",
    "أخضر",
    "أزرق",
    "كحلي",
    "ذهبي",
    "فضي",
    "وردي",
    "بيج",
    "زواج",
    "زفاف",
    "طلعة",
    "حفلة",
    "عرس",
    "سواريه",
    "خطوبة",
    "يومي",
    "يومية",
    "كلاسيك",
    "مطرز",
    "طويل",
    "قصير",
    "مفتوح",
    "مغلق",
)


def _chat_image_url(path: Optional[str]) -> str:
    """رابط مطلق لعرض الصورة في المتصفح (يفتح في تاب جديد)."""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return ""
    if p.startswith(("http://", "https://")):
        return p
    base_url = (
        (os.getenv("PUBLIC_BASE_URL") or os.getenv("BASE_URL") or "").strip().rstrip("/")
    )
    if p.startswith("/static/"):
        rel = p
    elif p.startswith("static/"):
        rel = "/" + p
    else:
        rel = "/static/" + p.lstrip("/")
    if base_url.startswith("https://"):
        return f"{base_url}{rel}"
    return rel


def _product_image_url_abs_https(path: Optional[str]) -> str:
    """رابط صورة بـ https لـ WhatsApp Cloud API (يفضّل PUBLIC_BASE_URL)."""
    u = (_chat_image_url(path) or "").strip()
    if not u:
        return ""
    if u.startswith("https://"):
        return u
    if u.startswith("http://"):
        return "https://" + u[len("http://") :]
    base = (os.getenv("PUBLIC_BASE_URL") or os.getenv("BASE_URL") or "").strip().rstrip("/")
    if base.startswith("https://") and u.startswith("/"):
        return base + u
    return u


def _short_chat_description(text: Optional[str], max_len: int = 180) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    one = t.split("\n")[0].strip()
    if len(one) > max_len:
        return one[: max_len - 1] + "…"
    return one


def _section_followup_partial_hint_match(text: str) -> bool:
    """نفس منطق تلميح المنتج دون المساس بـ intent detection."""
    for h in PRODUCT_HINTS:
        h = (h or "").strip()
        if len(h) < 2:
            continue
        if h in text:
            return True
        if len(h) >= 4:
            for i in range(len(h) - 2):
                if h[i : i + 3] in text:
                    return True
    return False


def _section_followup_has_description_word(text: str) -> bool:
    t = text or ""
    return any(w in t for w in _SECTION_FOLLOWUP_DESCRIPTION_WORDS)


def _section_followup_non_filler_text(message: str) -> str:
    """نص الرسالة بعد حذف الكلمات العامة فقط."""
    parts = []
    for w in (message or "").split():
        ws = w.strip()
        if not ws:
            continue
        if ws in _SECTION_FOLLOWUP_FILLER or ws.lower() in {x.lower() for x in _SECTION_FOLLOWUP_FILLER}:
            continue
        parts.append(ws)
    return " ".join(parts).strip()


def _section_followup_has_meaningful_term(message: str) -> bool:
    """كلمة منتج (تلميح) أو كلمة وصف — بعد تجاهل الحشو للتحقق من بقاء شيء."""
    t = (message or "").strip()
    if not t:
        return False
    if _section_followup_partial_hint_match(t) or _section_followup_has_description_word(t):
        return True
    stripped = _section_followup_non_filler_text(t)
    if not stripped:
        return False
    return _section_followup_partial_hint_match(stripped) or _section_followup_has_description_word(
        stripped
    )


def _format_product_detail_text(
    name, price, sizes, colors, qty, description: str = ""
) -> str:
    name = (name or "").strip() or "المنتج"
    try:
        price_val = float(price or 0)
    except (TypeError, ValueError):
        price_val = 0.0
    sz = "، ".join(sizes) if sizes else "—"
    cl = "، ".join(colors) if colors else "—"
    try:
        q = int(qty or 0)
    except (TypeError, ValueError):
        q = 0
    lines = [name]
    d = (description or "").strip()
    if d:
        first = d.split("\n")[0].strip()
        if len(first) > 200:
            first = first[:200] + "…"
        lines.append(first)
    lines.extend(
        [
            f"السعر: {price_val:g} ريال",
            f"المقاسات: {sz}",
            f"الألوان: {cl}",
            f"المتوفر: {q} قطع",
        ]
    )
    return "\n".join(lines)


def _save_chat_last_product_snapshot(product_dict, variants_raw):
    """يحفظ آخر منتج مُعرَض للمتابعة (أسئلة سعر/مقاس/لون) بدون تعديل قاعدة البيانات."""
    try:
        pid = int(product_dict.get("id"))
    except (TypeError, ValueError):
        return
    clean_variants = []
    for v in variants_raw or []:
        try:
            clean_variants.append(
                {
                    "size": (v.get("size") or "").strip(),
                    "color": (v.get("color") or "").strip(),
                    "quantity": int(v.get("quantity") or 0),
                    "price": float(v.get("price") or 0.0),
                }
            )
        except (TypeError, ValueError):
            continue
    session["chat_last_product"] = {
        "id": pid,
        "name": product_dict.get("name") or "",
        "price": product_dict.get("price"),
        "sizes": list(product_dict.get("sizes") or []),
        "colors": list(product_dict.get("colors") or []),
        "quantity": int(product_dict.get("quantity") or 0),
        "variants": clean_variants,
        "images": list(product_dict.get("images") or []),
        "image_url": (product_dict.get("image_url") or "").strip(),
        "primary_image_href": (product_dict.get("primary_image_href") or "").strip(),
    }


def _detail_question_match(msg: str) -> bool:
    t = (msg or "").strip()
    if len(t) < 2:
        return False
    needles = (
        "كم السعر",
        "كم سعر",
        "بكم",
        "بكام",
        "سعره",
        "وش السعر",
        "فيه مقاس",
        "في مقاس",
        "مقاس",
        "متوفر مقاس",
        "وش الألوان",
        "وش الالوان",
        "وش ألوان",
        "ايش الألوان",
        "الألوان",
        "الالوان",
        "ألوانك",
        "الكمية",
        "كم قطعة",
        "كم متوفر",
        "هل متوفر",
        "عدد القطع",
        "صور",
        "صورة",
        "ورني الصور",
        "ورني صورة",
        "فين الصور",
        "ارسل صورة",
        "أرسل صورة",
        "ابغى صور",
        "أبغى صور",
    )
    return any(n in t for n in needles)


def _norm_size(s: str) -> str:
    return (s or "").strip().replace(" ", "").upper()


def _reply_for_single_size(snap, sz: str, nm: str):
    cs = _cs()
    _display_name = cs._display_name
    variants = snap.get("variants") or []
    qsz = sum(
        int(v.get("quantity") or 0)
        for v in variants
        if _norm_size(v.get("size")) == _norm_size(sz)
    )
    if qsz > 0:
        return jsonify(
            {
                "products": [],
                "message": f"نعم يا {_display_name()}، متوفر مقاس {sz} بعدد {qsz}.",
                "intent": "product_followup",
            }
        )
    return jsonify(
        {
            "products": [],
            "message": f"حالياً مقاس {sz} غير متوفر لهذا المنتج.",
            "intent": "product_followup",
        }
    )


def _try_product_detail_reply(message: str):
    """رد على أسئلة تفاصيل آخر منتج مُعرَض (من الجلسة)."""
    cs = _cs()
    _display_name = cs._display_name
    snap = session.get("chat_last_product")
    if not snap:
        return None
    t = (message or "").strip()
    t_clean = t.replace("؟", "").replace("?", "").strip()
    nm = (snap.get("name") or "المنتج").strip()
    total_q = int(snap.get("quantity") or 0)

    matched_phrase = _detail_question_match(message)
    if not matched_phrase:
        for s in snap.get("sizes") or []:
            if not s:
                continue
            if t_clean == s.strip() or _norm_size(t_clean) == _norm_size(s):
                if total_q <= 0:
                    return jsonify(
                        {
                            "products": [],
                            "message": _PRODUCT_UNAVAILABLE_MSG,
                            "intent": "product_followup",
                        }
                    )
                return _reply_for_single_size(snap, s, nm)
        return None

    if total_q <= 0:
        return jsonify(
            {
                "products": [],
                "message": _PRODUCT_UNAVAILABLE_MSG,
                "intent": "product_followup",
            }
        )

    variants = snap.get("variants") or []
    price_val = snap.get("price")
    try:
        price_val = float(price_val or 0)
    except (TypeError, ValueError):
        price_val = 0.0

    if any(k in t for k in ("كم السعر", "كم سعر", "بكم", "بكام", "سعره", "وش السعر")) or (
        "السعر" in t and len(t) < 38
    ):
        return jsonify(
            {
                "products": [],
                "message": f"يا {_display_name()}، سعر «{nm}» هو {price_val:g} ريال.",
                "intent": "product_followup",
            }
        )

    if any(
        k in t
        for k in (
            "لون",
            "ألوان",
            "الألوان",
            "الالوان",
            "وش الألوان",
            "وش الالوان",
            "ايش الألوان",
        )
    ):
        cols = snap.get("colors") or []
        if cols:
            joined = "، ".join(cols)
            return jsonify(
                {
                    "products": [],
                    "message": f"الألوان المتوفرة لـ «{nm}»: {joined}.",
                    "intent": "product_followup",
                }
            )
        return jsonify(
            {
                "products": [],
                "message": "ما عندنا تفاصيل ألوان مسجّلة لهذا المنتج في النظام حالياً.",
                "intent": "product_followup",
            }
        )

    if any(k in t for k in ("صور", "صورة", "ورني", "فين الصور", "ارسل صورة", "أرسل صورة")):
        img_list = [str(x).strip() for x in (snap.get("images") or []) if str(x).strip()]
        if not img_list:
            i1 = (snap.get("image_url") or "").strip()
            if i1:
                img_list = [i1]
        if not img_list:
            i2 = (snap.get("primary_image_href") or "").strip()
            if i2:
                img_list = [i2]
        if img_list:
            detail_msg = _format_product_detail_text(
                nm,
                price_val,
                snap.get("sizes") or [],
                snap.get("colors") or [],
                total_q,
            )
            return jsonify(
                {
                    "products": [
                        {
                            "id": snap.get("id"),
                            "name": nm,
                            "price": price_val,
                            "quantity": total_q,
                            "sizes": list(snap.get("sizes") or []),
                            "colors": list(snap.get("colors") or []),
                            "images": img_list[:3],
                            "img1": img_list[0],
                            "primary_image_href": img_list[0],
                            "image_url": img_list[0],
                        }
                    ],
                    "message": f"تفضل، هذه صور المتوفر من «{nm}» 👇\n{detail_msg}",
                    "intent": "product_followup",
                }
            )

        session["pending_inquiry"] = {
            "text": f"صور المنتج: {nm}",
            "category": "",
            "branch_name": session.get("chat_selected_branch") or session.get("chat_last_branch") or "",
            "image_path": "",
        }
        return jsonify(
            {
                "products": [],
                "message": (
                    f"حالياً ما عندي صور جاهزة لـ «{nm}».\n"
                    "إذا تبغى أرسل استفسار للفرع الآن يجهزون لك صور المتوفر، قل: نعم."
                ),
                "intent": "inquiry_confirm",
            }
        )

    if "مقاس" in t or "فيه مقاس" in t or "في مقاس" in t:
        found_sizes = []
        for s in snap.get("sizes") or []:
            qs = (s or "").strip()
            if qs and qs in t:
                found_sizes.append(qs)
        if not found_sizes:
            for v in variants:
                qs = (v.get("size") or "").strip()
                if qs and qs in t and qs not in found_sizes:
                    found_sizes.append(qs)
        if len(found_sizes) == 1:
            return _reply_for_single_size(snap, found_sizes[0], nm)

    if any(k in t for k in ("المتوفر", "كم قطعة", "كم متوفر", "عدد القطع", "الكمية")):
        return jsonify(
            {
                "products": [],
                "message": f"المتوفر من «{nm}» حالياً: {total_q} قطع.",
                "intent": "product_followup",
            }
        )

    return jsonify(
        {
            "products": [],
            "message": _format_product_detail_text(
                nm,
                price_val,
                snap.get("sizes") or [],
                snap.get("colors") or [],
                total_q,
            ),
            "intent": "product_followup",
        }
    )


def _compose_product_search_text(name, desc, colors, sizes) -> str:
    """دمج حقول المنتج للمطابقة النصية (اسم + وصف + ألوان + مقاسات)."""
    parts = []
    n = (name or "").strip()
    d = (desc or "").strip()
    if n:
        parts.append(n)
    if d:
        parts.append(d)
    for c in colors or []:
        cc = (c or "").strip()
        if cc:
            parts.append(f"لون {cc}")
    for s in sizes or []:
        sz = (s or "").strip()
        if sz:
            parts.append(f"مقاس {sz}")
    return " ".join(parts).strip()


def _parse_product_query_constraints(message: str) -> dict:
    """استخراج مقاس/لون/جنس/كلمات وصف من رسالة العميل، وباقي النص كمدخل بحث."""
    t = normalize_for_product_search((message or "").strip())
    t = t.replace("؟", " ").replace("?", " ")
    sizes = []
    colors = []
    work = t

    for m in re.finditer(r"مقاس\s*([A-Za-z0-9\u0600-\u06FF]+)", work):
        s = m.group(1).strip()
        if s and s not in ("مقاس",):
            sizes.append(s)
    work = re.sub(r"مقاس\s*[A-Za-z0-9\u0600-\u06FF]+", " ", work)

    for m in re.finditer(r"\b([SMXL]{1,3})\b", work, re.I):
        u = m.group(1).upper()
        if u not in sizes:
            sizes.append(u)

    for m in re.finditer(r"لون\s+([^\s،,]+)", work):
        colors.append(m.group(1).strip())
    work = re.sub(r"لون\s+[^\s،,]+", " ", work)

    for cw in _PRODUCT_COLOR_HINTS:
        if cw in t:
            colors.append(cw)
    colors = list(dict.fromkeys(colors))

    needle = " ".join(work.split())
    for c in colors:
        if len(c) >= 2:
            needle = needle.replace(c, " ")
    for s in sizes:
        needle = needle.replace(s, " ")
    for tok in ("مقاس", "لون"):
        needle = needle.replace(tok, " ")
    needle = " ".join(needle.split())

    if len(needle) < 2:
        needle = t

    gender = extract_gender_filter(t)

    return {
        "needle": needle,
        "sizes": sizes,
        "colors": colors,
        "desc_keywords": [],
        "gender": gender,
    }


def _color_constraint_match(requested: str, variant_color: str) -> bool:
    rq = (requested or "").strip()
    vc = (variant_color or "").strip()
    if not rq or not vc:
        return False
    return rq in vc or vc in rq


def _product_matches_query_constraints(
    variants: list,
    product_description: str,
    search_text: str,
    constraints: dict,
    *,
    extra_blob: str = "",
) -> bool:
    sizes_req = constraints.get("sizes") or []
    colors_req = constraints.get("colors") or []
    desc_kw = constraints.get("desc_keywords") or []
    gender = constraints.get("gender")

    blob_for_gender = f"{search_text} {(product_description or '')} {extra_blob}".strip()
    if gender and not blob_matches_gender_filter(blob_for_gender, gender):
        return False

    if desc_kw:
        blob = f"{search_text} {(product_description or '')}"
        for dk in desc_kw:
            if dk not in blob:
                return False

    variants = variants if variants is not None else []
    if not variants:
        return True

    active_variants = [v for v in variants if int(v.get("quantity") or 0) > 0]
    if not active_variants:
        active_variants = variants

    if colors_req:
        ok = False
        for v in active_variants:
            vc = v.get("color") or ""
            for cr in colors_req:
                if _color_constraint_match(cr, vc):
                    ok = True
                    break
            if ok:
                break
        if not ok:
            return False

    if sizes_req:
        ok = False
        for v in active_variants:
            vs = v.get("size") or ""
            for sr in sizes_req:
                if _norm_size(sr) == _norm_size(vs):
                    ok = True
                    break
            if ok:
                break
        if not ok:
            return False

    return True


def _snapshot_search_text(snap: dict) -> str:
    """بناء search_text من آخر منتج معروض (اسم + وصف + متغيرات)."""
    cs = _cs()
    get_db = cs.get_db
    if not snap:
        return ""
    try:
        pid = int(snap.get("id"))
    except (TypeError, ValueError):
        return ""
    d = get_db().get_product_detail(pid)
    if not d:
        return ""
    variants = get_db().get_product_variants(pid) or []
    colors = sorted({v["color"] for v in variants if v.get("color")})
    sizes = sorted({v["size"] for v in variants if v.get("size")})
    return _compose_product_search_text(
        d.get("product_name") or "",
        d.get("description") or "",
        colors,
        sizes,
    )


def _recommendation_needle_from_search_text(st: str) -> str:
    st = (st or "").strip()
    if len(st) < 2:
        return ""
    return st[:500]


def _looks_like_product_interest_confirmation(message: str) -> bool:
    """رسالة قصيرة أو صريحة تدل على رغبة بمعرفة الفروع بعد عرض المنتج أول مرة."""
    t = (message or "").strip()
    if not t:
        return False
    tl = t.lower().strip()
    if t in _PRODUCT_INTEREST_CONFIRM or tl in _PRODUCT_INTEREST_CONFIRM:
        return True
    for k in (
        "ابغاه",
        "ابغاها",
        "ابغيه",
        "أبغاه",
        "اريده",
        "أريده",
        "الفروع",
        "فروعكم",
        "وين الفرع",
        "وين الفروع",
        "موقع الفرع",
        "اقرب فرع",
        "أقرب فرع",
    ):
        if k in t:
            return True
    return False


def _try_pending_product_intent_confirmation(message: str):
    """
    بعد عرض منتجات: إذا طلب المستخدم «فروع/وين» نذكر الفروع؛ وإلا رد قصير بدون سرد فروع.
    """
    if not session.get("pending_product_intent"):
        return None
    if not _looks_like_product_interest_confirmation(message):
        return None
    session.pop("pending_product_intent", None)
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    t = (message or "").strip()
    wants_branch_detail = any(
        k in t
        for k in (
            "أي فرع",
            "اي فرع",
            "أي فرع؟",
            "وين الفرع",
            "وين الفروع",
            "الفروع",
            "فروعكم",
            "موقع الفرع",
            "اقرب فرع",
            "أقرب فرع",
        )
    )
    ids = list(session.get("last_products") or [])
    if not ids and session.get("chat_last_product"):
        try:
            ids = [int(session["chat_last_product"]["id"])]
        except (TypeError, ValueError):
            ids = []
    out_products = []
    for pid in ids[:12]:
        p = get_db().get_product_detail(pid)
        if not p:
            continue
        variants = get_db().get_product_variants(pid) or []
        out_products.append(
            _product_dict_for_chat(p, pid, variants, show_branch_in_chat=False)
        )
    d = session.get("chat_dialect") or "default"
    if wants_branch_detail:
        cities = _distinct_branch_city_labels(out_products)
        msg = dialect_message(d, "product_branch_prompt", name=_display_name())
        if cities:
            msg += "\n" + "\n".join(f"• {c}" for c in cities)
        return jsonify({"products": [], "message": msg, "intent": "product"})
    msg = dialect_message(d, "product_available_ack", name=_display_name())
    return jsonify({"products": [], "message": msg, "intent": "product"})


def _product_dict_for_chat(
    p: dict, product_id: int, variants: list, *, show_branch_in_chat: bool = False
) -> dict:
    cs = _cs()
    get_db = cs.get_db
    if variants:
        sizes = sorted({v["size"] for v in variants if v.get("size")})
        colors = sorted({v["color"] for v in variants if v.get("color")})
        total_qty = int(sum(int(v.get("quantity") or 0) for v in variants))
        prices = [float(v.get("price") or 0.0) for v in variants]
        price = min(prices) if prices else float(p.get("price") or 0.0)
    else:
        sizes = []
        colors = []
        total_qty = 0
        price = float(p.get("price") or 0.0)

    images = get_db().get_product_images(product_id)[:3]
    branches = get_db().get_product_branches(product_id)
    detail_text = _format_product_detail_text(
        p.get("product_name"),
        price,
        sizes,
        colors,
        total_qty,
        p.get("description") or "",
    )
    search_text = _compose_product_search_text(
        p.get("product_name"),
        p.get("description") or "",
        colors,
        sizes,
    )
    br_name = ""
    if branches:
        br_name = (branches[0].get("name") or "").strip()
    if not br_name:
        br_name = (p.get("branch_city_name") or "").strip()
    sd = _short_chat_description(p.get("description") or "")
    chat_lines = [
        (p.get("product_name") or "").strip() or "المنتج",
        f"السعر: {price:g} ريال",
    ]
    if show_branch_in_chat and br_name:
        chat_lines.append(f"الفرع: {br_name}")
    if sd:
        chat_lines.append(sd)
    chat_text = "\n".join(chat_lines)
    img1_col = (p.get("img1") or "").strip()
    if not images and img1_col:
        images = [img1_col]
    img1_out = (images[0] if images else "") or img1_col
    primary_href = _chat_image_url(images[0] if images else "")
    if not primary_href and img1_col:
        primary_href = _chat_image_url(img1_col)
    image_url = _product_image_url_abs_https(images[0] if images else "") or _product_image_url_abs_https(
        img1_col
    )
    name_str = (p.get("product_name") or "").strip() or "المنتج"
    price_str = f"{price:g}"
    return {
        "id": product_id,
        "name": name_str,
        "description": p.get("description") or "",
        "price": price_str,
        "sizes": sizes,
        "colors": colors,
        "quantity": total_qty,
        "images": images,
        "img1": img1_out,
        "branches": branches,
        "detail_text": detail_text,
        "chat_text": chat_text,
        "primary_image_href": primary_href,
        "image_url": image_url,
        "search_text": search_text,
    }


# كلمات وصف/صفة تُؤخّر عن كونها «نوع المنتج» الأساسي (لا تُختار كمرشّح أول للاسم)
_DEPRIORITIZE_PRIMARY_TOKENS = frozenset(
    {
        "رجالي",
        "رجال",
        "نسائي",
        "نساء",
        "نسا",
        "ولادي",
        "اولادي",
        "سهرة",
        "زواج",
        "زفاف",
        "طلعة",
        "حفلة",
        "رسمي",
        "كاجوال",
    }
) | frozenset(_PRODUCT_COLOR_HINTS)


def _infer_keyword_for_name_filter(message: str, needle: str) -> str:
    """
    كلمة أساسية للتأكد أن اسم المنتج يحتويها (مثل تشيرت وليس مطابقة وصف/قسم فقط).
    يفضّل تلميحاً من PRODUCT_HINTS ثم أول كلمة معنوية بالترتيب (وليس «أطول كلمة»).
    """
    from logic import keywords as kw

    t = normalize_for_product_search((message or "").strip())
    best = ""
    for h in kw.PRODUCT_HINTS:
        hs = (h or "").strip()
        if len(hs) >= 2 and hs in t and len(hs) > len(best):
            best = hs
    if best:
        return best
    n = (needle or "").strip()
    stop = frozenset(
        {
            "أبغى",
            "ابغى",
            "أبي",
            "ابي",
            "أريد",
            "اريد",
            "عندكم",
            "عندك",
            "فيه",
            "ودي",
            "ابغي",
            "أبغي",
        }
    )
    parts = [p for p in n.replace("؟", " ").split() if len(p) >= 2]
    parts = [p for p in parts if p not in stop]
    if not parts:
        return n[:40] if n else ""
    for p in parts:
        if p in _DEPRIORITIZE_PRIMARY_TOKENS:
            continue
        if len(p) >= 2:
            return p
    for p in parts:
        if len(p) >= 2:
            return p
    return n[:40] if n else ""


# أنواع من PRODUCT_HINTS تستبعد بعضها عند السياق الواحد (تشيرت ≠ بنطلون …) إن وُجد نوع واضح في الرسالة
_PRODUCT_EXCLUSION_SKIP = frozenset(
    {
        "ملابس",
        "لون",
        "مقاس",
        "سعر",
        "كم سعر",
        "شراء",
        "عرض",
        "تخفيض",
        "تشكيلة",
        "ابغى",
        "ابغي",
        "ابي",
        "أبي",
        "أريد",
        "اريد",
        "دور على",
    }
)
_PRODUCT_EXCLUSION_HINTS = tuple(
    (h or "").strip()
    for h in PRODUCT_HINTS
    if (h or "").strip()
    and len((h or "").strip()) >= 2
    and (h or "").strip() not in _PRODUCT_EXCLUSION_SKIP
)


def _dominant_exclusion_hint_from_message(message: str) -> str:
    """أطول تلميح نوع منتج من الرسالة (من PRODUCT_HINTS) لاستخدامه في فلترة النتائج."""
    nm = normalize_for_product_search((message or "").strip())
    if len(nm) < 2:
        return ""
    hits = [(len((h or "").strip()), (h or "").strip()) for h in _PRODUCT_EXCLUSION_HINTS]
    hits = [(ln, h) for ln, h in hits if ln >= 2 and h in nm]
    if not hits:
        return ""
    hits.sort(reverse=True)
    return hits[0][1]


def _rows_single_dominant_product_hint(rows: list, message: str) -> list:
    """
    إذا حدد المستخدم نوعاً واضحاً (مثل تشيرت) يُستبعد صف يحمل نوعاً مختلفاً
    (مثل بنطلون) إن وُجد في اسم المنتج دون أن يطابق النوع المطلوب.
    إذا أفرغ الفلتر القائمة نُعيد الصفوف الأصلية.
    """
    if not rows:
        return rows
    nm = normalize_for_product_search((message or "").strip())
    type_hits = [h for h in _PRODUCT_EXCLUSION_HINTS if len(h) >= 2 and h in nm]
    if len(type_hits) > 1:
        return rows
    dominant = _dominant_exclusion_hint_from_message(message)
    if not dominant:
        return rows
    kept: List[dict] = []
    for r in rows:
        pname = (r.get("product_name") or "")
        pn = normalize_for_product_search(pname)
        if dominant in pn:
            kept.append(r)
            continue
        drop = False
        for h in _PRODUCT_EXCLUSION_HINTS:
            hs = (h or "").strip()
            if len(hs) < 2 or hs == dominant:
                continue
            if hs in nm:
                continue
            if hs in pn:
                drop = True
                break
        if not drop:
            kept.append(r)
    return kept if kept else rows


def _distinct_branch_city_labels(out_products: list) -> list:
    """أسماء فروع مميزة بالترتيب (من بيانات المنتج في الشات)."""
    seen = set()
    ordered = []
    for p in out_products or []:
        for br in p.get("branches") or []:
            nm = (br.get("name") or "").strip()
            if not nm:
                continue
            city = nm.replace("فرع ", "").strip()
            if city and city not in seen:
                seen.add(city)
                ordered.append(city)
    return ordered


def _browse_context_label(last_section: str, user_message: str) -> str:
    ls = (last_section or "").strip()
    t = (user_message or "").strip()
    if "نسائي" in t or "نساء" in t:
        return f"{ls} للنساء"
    if "رجالي" in t or "رجال" in t:
        return f"{ls} للرجال"
    return ls


def _product_list_intro_message(_display_name, out_products: list, has_more: bool) -> str:
    """مقدمة قصيرة — بدون سرد فروع؛ المنتجات تظهر في البطاقات."""
    names = [
        str(p.get("name") or "").strip()
        for p in (out_products or [])
        if (p.get("name") or "").strip()
    ]
    d = session.get("chat_dialect") or "default"
    if not names:
        intro = dialect_message(d, "product_available_ack", name=_display_name())
    elif len(names) == 1:
        intro = f"عندنا {names[0]}"
    elif len(names) == 2:
        intro = f"عندنا {names[0]} و{names[1]}"
    else:
        intro = f"عندنا {names[0]} و{names[1]} وغيرها"
    if has_more:
        intro += "\n" + dialect_message(d, "product_found_soft_more")
    return intro


def _search_query_from_image_description(description: str) -> tuple[str, str]:
    """
    يستخرج سطر SEARCH: من مخرجات نموذج الرؤية إن وُجد، ويعيد (نص_البحث، نص_للتلميح).
    """
    t = (description or "").strip()
    if not t:
        return "", ""
    lines = [ln.strip() for ln in t.replace("\r\n", "\n").split("\n") if ln.strip()]
    search_line = ""
    display_parts: list[str] = []
    for ln in lines:
        low = ln.lower()
        if low.startswith("search:") or low.startswith("search :"):
            search_line = ln.split(":", 1)[-1].strip()
        else:
            display_parts.append(ln)
    display = "\n".join(display_parts).strip() or t
    q = (search_line or display).strip()
    return q, display


def build_products_response_from_customer_image(description: str) -> Optional[dict]:
    """
    بعد تحليل صورة العميل (Gemini/OpenAI): يبحث في قاعدة المنتجات ويعيد نفس شكل _build_products_response.
    لا يرسل استفساراً تلقائياً للفرع عند عدم المطابقة (صورة قد تكون غامضة).
    يعيد None إذا كان الوصف يشير لصورة غير صالحة للبحث.
    """
    blob = (description or "").strip()
    if not blob:
        return None
    if "غير واضحة" in blob and ("وضّح" in blob or "وضح" in blob):
        return None
    if "وضّح طلبك بالنص" in blob or "وضح طلبك بالنص" in blob:
        return None
    q, hint = _search_query_from_image_description(blob)
    search_msg = q if len(q) >= 2 else blob
    hint_src = hint if len(hint) >= 2 else blob
    return _build_products_response(
        search_msg,
        hint_source_message=hint_src,
        image_attachment=True,
    )


def _build_products_response(
    message: str,
    hint_source_message: Optional[str] = None,
    *,
    image_attachment: bool = False,
):
    """
    بحث منتجات للشات — على كل الفروع، بترتيب: قسم فرعي → فئة → اسم → وصف،
    مع توسيع المناسبات العامة (زواج، مناسبة، …).
    يعرض حتى منتجين لكل رد؛ الباقي يُعرض عند طلب «غيره».
    hint_source_message: نص المستخدم الأصلي (لتلميحات النوع وفلترة الأسماء) عندما يختلف نص البحث عنه.
    image_attachment: إذا True، عدم المطابقة لا يُرسل استفساراً تلقائياً للفرع (مسار صورة العميل).
    """
    raw_message = (message or "").strip()

    # طلب استعراض عام "الموديلات / عرض المنتجات" → ابحث في آخر قسم أو ابحث عاماً
    if _looks_like_browse_request(raw_message):
        last = session.get("last_section") or ""
        cs_obj = _cs()
        get_db_obj = cs_obj.get_db
        _dn = cs_obj._display_name
        rows_b = []
        if last:
            rows_b = get_db_obj().search_products_in_section(last, last, limit=20)
        if not rows_b:
            rows_b = get_db_obj().search_products("", limit=20) if not last else []
        if rows_b:
            out_b = []
            for p in rows_b[:4]:
                pid_b = int(p["product_id"])
                vv_b = get_db_obj().get_product_variants(pid_b) or []
                out_b.append(_product_dict_for_chat(p, pid_b, vv_b, show_branch_in_chat=False))
            if out_b:
                session["last_products"] = [int(x["id"]) for x in out_b]
                session["pending_product_intent"] = True
                _save_chat_last_product_snapshot(out_b[0], get_db_obj().get_product_variants(int(out_b[0]["id"])) or [])
                shown_b = _apply_step_by_step_slice(out_b, "product")
                lbl = (last or "المنتجات").strip()
                return {"products": shown_b, "intent": "product", "message": f"هذا اللي عندنا في {lbl}:"}

    hint_src = normalize_for_product_search(
        (hint_source_message if hint_source_message is not None else message) or ""
    )
    message = normalize_for_product_search(raw_message)
    infer_src = hint_src if hint_source_message is not None else message
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    constraints = _parse_product_query_constraints(message)
    needle = (constraints.get("needle") or "").strip()
    if len(needle) < 2:
        needle = (message or "").strip()

    rows, has_more_tier = [], False
    for n in all_product_search_needles(needle, (message or "").strip()):
        if len(n) < 2:
            continue
        r, h = get_db().chat_tiered_product_search(n, per_tier_limit=4)
        if r:
            fk = _infer_keyword_for_name_filter(infer_src, n)
            r = filter_rows_keyword_in_product_name(r, fk) if fk else r
            r = _rows_single_dominant_product_hint(r, hint_src) if r else r
            if r:
                rows, has_more_tier = r, h
                break
    # إذا لم يُرجع البحث المُرتّب صفوفاً، نجرب البحث على الاسم فقط
    if not rows:
        for n in all_product_search_needles(needle, (message or "").strip()):
            if len(n) < 2:
                continue
            alt = get_db().search_products(n, limit=30)
            if alt:
                fk = _infer_keyword_for_name_filter(infer_src, n)
                alt = filter_rows_keyword_in_product_name(alt, fk) if fk else alt
                alt = _rows_single_dominant_product_hint(alt, hint_src) if alt else alt
                if alt:
                    rows, has_more_tier = alt, False
                    break
    if not rows:
        session.pop("remaining_products", None)
        session.pop("remaining_products_intent", None)
        cs_dn = _cs()._display_name()
        if image_attachment:
            return {
                "products": [],
                "intent": "product",
                "message": (
                    f"ما لقيت في الكتالوج مطابقة واضحة لما في الصورة يا {cs_dn}. "
                    "جرّب تكتب اسم المنتج أو القسم، أو أرسل صورة أوضح 👍"
                ),
            }
        # أرسل للفرع (فقط إن كانت الرسالة تبدو طلب منتج حقيقي)
        sent_mail = _send_product_inquiry_to_branch(raw_message)
        if sent_mail:
            return {
                "products": [],
                "intent": "product_inquiry_sent",
                "message": (
                    f"لحظات يا {cs_dn}، راسلت الفرع عن هذا المنتج "
                    "وراح يردوا عليك بأقرب وقت 👍"
                ),
            }
        return {
            "products": [],
            "intent": "product",
            "message": (
                f"ما لقيت في الكتالوج مطابقة مباشرة يا {cs_dn}. "
                "جرّب تكتب الاسم أو النوع بشكل أوضح 👍 أو تواصل فرعنا من صفحة «الفروع»."
            ),
        }

    out_products = []
    fallback_products = []  # منتجات تطابق الاسم لكن ليس اللون/المقاس
    for p in rows:
        product_id = int(p["product_id"])
        variants = get_db().get_product_variants(product_id) or []
        sizes = sorted({v["size"] for v in variants if v.get("size")}) if variants else []
        colors = sorted({v["color"] for v in variants if v.get("color")}) if variants else []
        search_text = _compose_product_search_text(
            p.get("product_name"),
            p.get("description") or "",
            colors,
            sizes,
        )
        extra_blob = f"{(p.get('category_name') or '')} {(p.get('section_name') or '')}"
        if not _product_matches_query_constraints(
            variants,
            p.get("description") or "",
            search_text,
            constraints,
            extra_blob=extra_blob,
        ):
            # احفظ كبديل — المنتج موجود لكن لونه/مقاسه مختلف
            if len(fallback_products) < 2:
                fallback_products.append(
                    _product_dict_for_chat(p, product_id, variants, show_branch_in_chat=False)
                )
            continue
        out_products.append(
            _product_dict_for_chat(p, product_id, variants, show_branch_in_chat=False)
        )
        if len(out_products) >= 2:
            break

    # إذا فلتر اللون/المقاس شال كل المنتجات → اعرض البدائل بدل "ما لقيت"
    if not out_products and fallback_products:
        out_products = fallback_products

    if not out_products:
        session.pop("remaining_products", None)
        session.pop("remaining_products_intent", None)
        cs_dn = _cs()._display_name()
        if image_attachment:
            return {
                "products": [],
                "intent": "product",
                "message": (
                    f"ما طابقت الصورة بمنتج محدد حالياً يا {cs_dn}. "
                    "اكتب اسم القطعة أو تصفّح الأقسام إذا تحب."
                ),
            }
        sent_mail = _send_product_inquiry_to_branch(raw_message)
        if sent_mail:
            return {
                "products": [],
                "intent": "product_inquiry_sent",
                "message": (
                    f"لحظات يا {cs_dn}، راسلت الفرع عن هذا المنتج "
                    "وراح يردوا عليك بأقرب وقت 👍"
                ),
            }
        return {
            "products": [],
            "intent": "product",
            "message": (
                f"ما لقيت مطابقة دقيقة حسب المواصفات يا {cs_dn}. "
                "جرّب تخفف الفلاتر أو اكتب وصف مختلف، أو أسألك الفروع برسالة أوضح إذا تحب 👍"
            ),
        }

    has_more = has_more_tier or len(rows) > len(out_products)
    session["last_products"] = [int(x["id"]) for x in out_products]
    session["pending_product_intent"] = True
    _save_chat_last_product_snapshot(
        out_products[0],
        get_db().get_product_variants(int(out_products[0]["id"])) or [],
    )
    intro = _product_list_intro_message(_display_name, out_products, has_more)
    shown = _apply_step_by_step_slice(out_products, "product")
    try:
        from logic import chat_context as _chat_ctx

        _chat_ctx.on_product_list_shown(shown, "product")
    except Exception:
        pass
    return {
        "products": shown,
        "intent": "product",
        "message": intro,
    }


def _keywords_grounded_in_user_text(keywords: list, original: str) -> list:
    """
    يقبل كلمات مستخرجة من نموذج لغوي فقط إذا ظهرت (بعد تطبيع عربي) في نص المستخدم.
    يقلّل الثقة بمخرجات غير مُتحقَّقة قبل البحث في قاعدة البيانات.
    """
    blob = _norm_ar_for_kw(normalize_for_product_search((original or "").strip()))
    if len(blob) < 2:
        return []
    out: List[str] = []
    seen = set()
    for k in keywords or []:
        k = str(k).strip()
        if len(k) < 2 or k in seen:
            continue
        nk = _norm_ar_for_kw(k)
        if len(nk) >= 2 and nk in blob:
            seen.add(k)
            out.append(k)
    return out


def _build_search_text_from_llm(cleaned: str, keywords: list, original: str) -> str:
    kw_ok = _keywords_grounded_in_user_text(keywords, original)
    parts: List[str] = []
    c = (cleaned or "").strip()
    if c:
        ob = _norm_ar_for_kw(normalize_for_product_search((original or "").strip()))
        nc = _norm_ar_for_kw(c)
        if ob and len(nc) >= 2 and nc in ob:
            parts.append(c)
    for k in kw_ok:
        if k and k not in parts:
            parts.append(k)
    s = " ".join(parts).strip()
    if len(s) >= 2:
        return s
    return (original or "").strip()


def _recommendation_response():
    """بدائل ذكية: نفس القسم أو نفس كلمات البحث، بدون تكرار المعرض سابقاً."""
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    exclude = set()
    for lp in session.get("last_products") or []:
        try:
            exclude.add(int(lp))
        except (TypeError, ValueError):
            pass
    snap = session.get("chat_last_product")
    if snap:
        try:
            exclude.add(int(snap["id"]))
        except (TypeError, ValueError):
            pass

    has_ctx = bool(snap or session.get("last_products") or session.get("last_section"))
    if not has_ctx:
        return jsonify(NO_PRODUCTS_PAYLOAD)

    if not session.get("last_products") and snap:
        session["last_products"] = [int(snap["id"])]

    needle = ""
    if snap:
        needle = _recommendation_needle_from_search_text(_snapshot_search_text(snap))
    if not needle and session.get("last_products"):
        pid0 = int(session["last_products"][0])
        d0 = get_db().get_product_detail(pid0)
        if d0:
            vv = get_db().get_product_variants(pid0) or []
            cols = sorted({v["color"] for v in vv if v.get("color")})
            szs = sorted({v["size"] for v in vv if v.get("size")})
            st0 = _compose_product_search_text(
                d0.get("product_name") or "",
                d0.get("description") or "",
                cols,
                szs,
            )
            needle = _recommendation_needle_from_search_text(st0)
    if not needle:
        needle = (session.get("last_section") or "").strip()
    if not needle or len(needle) < 2:
        return jsonify(NO_PRODUCTS_PAYLOAD)

    last_section = session.get("last_section")
    rows = []
    if last_section:
        for sec in get_db().section_names_for_followup(last_section, needle):
            rows = get_db().search_products_in_section(sec, needle, limit=30)
            if rows:
                break
    if not rows:
        rows = get_db().search_products(needle, limit=30)

    filtered = [r for r in rows if int(r["product_id"]) not in exclude][:12]
    if not filtered:
        session.pop("remaining_products", None)
        session.pop("remaining_products_intent", None)
        return jsonify(NO_PRODUCTS_PAYLOAD)

    out_products = []
    for p in filtered:
        product_id = int(p["product_id"])
        variants = get_db().get_product_variants(product_id) or []
        out_products.append(
            _product_dict_for_chat(p, product_id, variants, show_branch_in_chat=False)
        )
        if len(out_products) >= 2:
            break

    prev = list(session.get("last_products") or [])
    new_ids = [int(p["id"]) for p in out_products]
    session["last_products"] = list(dict.fromkeys(prev + new_ids))
    session["pending_product_intent"] = True
    _save_chat_last_product_snapshot(
        out_products[0],
        get_db().get_product_variants(int(out_products[0]["id"])) or [],
    )
    shown = _apply_step_by_step_slice(out_products, "recommendation")
    try:
        from logic import chat_context as _chat_ctx

        _chat_ctx.on_product_list_shown(shown, "recommendation")
    except Exception:
        pass
    rec_msg = "هذا كمان من عندنا 👇"
    return jsonify(
        {
            "products": shown,
            "intent": "recommendation",
            "message": rec_msg,
        }
    )


def _try_last_section_product_followup(message: str):
    """بعد نجاح استعلام قسم: رد يُفسَّر كبحث منتج داخل آخر قسم (last_section)."""
    from logic.category_service import (
        message_asks_clothing_departments_overview,
        message_asks_full_category_catalog,
    )

    if message_asks_full_category_catalog(message):
        return None
    if message_asks_clothing_departments_overview(message):
        return None

    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    if session.get("pending_section_choices"):
        return None
    last = session.get("last_section")
    if not last:
        return None
    t = (message or "").strip()
    if not t or len(t) > 120:
        return None
    _sec_kw = ("قسم", "أقسام", "اقسام", "الأقسام", "الاقسام", "القسم")
    if any(k in t for k in _sec_kw):
        return None
    if not _section_followup_has_meaningful_term(t):
        # لو الرسالة مجرد "نعم" أو موافقة → اسأل عن المنتج المحدد
        stripped = _section_followup_non_filler_text(t)
        if not stripped or t in _SECTION_FOLLOWUP_FILLER:
            cs = _cs()
            _display_name = cs._display_name
            ls = (last or "").strip() or "هذا القسم"
            return jsonify({
                "products": [],
                "message": f"حلو، وش تبغى بالضبط من {ls}؟ مثل: لون، مقاس، أو نوع معين.",
                "intent": "product_clarify",
            })
        return jsonify(NO_PRODUCTS_PAYLOAD)
    t = normalize_for_product_search(t)
    constraints = _parse_product_query_constraints(t)
    query = _section_followup_non_filler_text(t) or t
    needle = (constraints.get("needle") or "").strip()
    if len(needle) < 2:
        needle = query
    blob_candidates = f"{t} {needle} {query}"
    section_candidates = get_db().section_names_for_followup(last, blob_candidates)
    rows = []
    for sec in section_candidates:
        rows = get_db().search_products_in_section(sec, needle, limit=50)
        if rows:
            break
    if not rows:
        for sec in section_candidates:
            rows = get_db().search_products_in_section(sec, query, limit=50)
            if rows:
                break
    if not rows:
        session.pop("remaining_products", None)
        session.pop("remaining_products_intent", None)
        ls = (last or "").strip() or "هذا القسم"
        session["chat_pending_branch_phone_offer"] = True
        return jsonify(
            {
                "products": [],
                "message": f"ما لقيت هذا المنتج بالضبط في قسم {ls}.",
                "followup_message": "تبي أرسل لك رقم الفرع تسأل مباشرة؟",
                "intent": "product",
            }
        )
    out_products = []
    for p in rows:
        product_id = int(p["product_id"])
        variants = get_db().get_product_variants(product_id) or []
        sizes = sorted({v["size"] for v in variants if v.get("size")}) if variants else []
        colors = sorted({v["color"] for v in variants if v.get("color")}) if variants else []
        search_text = _compose_product_search_text(
            p.get("product_name"),
            p.get("description") or "",
            colors,
            sizes,
        )
        extra_blob = f"{(p.get('category_name') or '')} {(p.get('section_name') or '')}"
        if not _product_matches_query_constraints(
            variants,
            p.get("description") or "",
            search_text,
            constraints,
            extra_blob=extra_blob,
        ):
            continue
        out_products.append(
            _product_dict_for_chat(p, product_id, variants, show_branch_in_chat=False)
        )
        if len(out_products) >= 2:
            break

    if not out_products:
        session.pop("remaining_products", None)
        session.pop("remaining_products_intent", None)
        ls = (last or "").strip() or "هذا القسم"
        sent_mail = _send_product_inquiry_to_branch(t)
        cs_dn = _cs()._display_name()
        if sent_mail:
            return jsonify(
                {
                    "products": [],
                    "message": (
                        f"لحظات يا {cs_dn}، راسلت الفرع عن هذا المنتج "
                        "وراح يردوا عليك بأقرب وقت 👍"
                    ),
                    "intent": "product_inquiry_sent",
                }
            )
        return jsonify(
            {
                "products": [],
                "message": (
                    f"ما طابقت المنتج في قسم {ls} كما هو واصف الآن يا {cs_dn}. "
                    "جرّب كلمات أبسط لوصف القطعة، أو أكد لو تبي أبحث أكثر عمّا يشبهها."
                ),
                "intent": "product",
            }
        )
    session["last_products"] = [int(p["id"]) for p in out_products]
    session["pending_product_intent"] = True
    _save_chat_last_product_snapshot(
        out_products[0],
        get_db().get_product_variants(int(out_products[0]["id"])) or [],
    )
    session["chat_current_intent"] = "product"
    shown = _apply_step_by_step_slice(out_products, "product")
    try:
        from logic import chat_context as _chat_ctx

        _chat_ctx.on_product_list_shown(shown, "product")
    except Exception:
        pass
    lbl = _browse_context_label(last, message)
    msg = f"هذا اللي عندي حالياً في {lbl}:"
    return jsonify(
        {
            "products": shown,
            "intent": "product",
            "message": msg,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# دالة عامة: بحث مع نظام الاستفسار عند عدم وجود المنتج
# ─────────────────────────────────────────────────────────────────────────────

def try_product_search_with_inquiry(
    message: str,
    hint_source_message: Optional[str] = None,
) -> dict:
    """
    يبحث عن المنتج أولاً.
    إذا وُجد → يعيده مباشرة.
    إذا لم يُجد → دائماً يصعّد للفرع (لا يقول "ما لقيت" ولا يخمّن).

    التدفق:
    1. يحفظ pending_inquiry في الجلسة.
    2. يعيد سؤال "تبغى أسأل الفرع؟" للعميل.
    3. chat_router يلتقط رد العميل عبر _try_resolve_pending_inquiry.
    4. عند التأكيد → ينشئ الاستفسار ويرسل للفرع.

    القاعدة: إذا ما عندنا جواب أكيد → نصعّد. لا نخمن.
    """
    result = _build_products_response(message, hint_source_message)

    # إذا وُجدت منتجات → أعد النتيجة مباشرة
    if result.get("intent") != "no_products":
        return result

    # ── المنتج غير موجود → تصعيد للفرع دائماً ──
    try:
        from logic.category_classifier import infer_category_from_text

        cs_obj = _cs()
        db = cs_obj.get_db()

        # جلب أسماء الأقسام الفعلية من قاعدة البيانات
        db_cats: list[dict] = []
        try:
            raw_cats = db.get_main_categories() or []
            db_cats = [{"name": c.get("name", "")} for c in raw_cats if c.get("name")]
        except Exception:
            db_cats = []

        # استخدم النص الأصلي للتحليل
        original_text = (hint_source_message or message).strip()

        # حاول استنتاج الفئة — لكن لا تتوقف إذا ما وُجدت
        category = infer_category_from_text(original_text, db_cats) or ""

        branch_name = (
            session.get("chat_selected_branch")
            or session.get("chat_last_branch")
            or ""
        )
        customer_name = (session.get("user_name") or "").strip()
        name_prefix = f"يا {customer_name}، " if customer_name else ""

        # ── احفظ الاستفسار في الجلسة دائماً ──
        session["pending_inquiry"] = {
            "text": original_text,
            "category": category,
            "branch_name": branch_name,
            "image_path": session.get("_pending_image_path", ""),
        }

        # ── بناء سؤال التأكيد ──
        if category and branch_name:
            q = (
                f"{name_prefix}عندنا قسم {category} ✅\n"
                f"بأكد لك التوفر من فرع {branch_name} — تبغى أسأل الفرع، أو أشوف في كل الفروع؟"
            )
        elif category:
            q = (
                f"{name_prefix}عندنا قسم {category} ✅\n"
                f"بأكد لك التوفر من الفرع — تبغى أسأل الفروع عنه؟"
            )
        elif branch_name:
            q = f"{name_prefix}لحظة، بأكد لك من فرع {branch_name} 🙏 — تبغى؟"
        else:
            q = f"{name_prefix}لحظة، بأكد لك من الفرع 🙏 — تبغى أسأل الفروع عنه؟"

        return {
            "products": [],
            "message": q,
            "intent": "inquiry_confirm",
        }

    except Exception:
        import logging
        logging.getLogger(__name__).exception("try_product_search_with_inquiry: failed")
        return result