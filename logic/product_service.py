# -*- coding: utf-8 -*-
"""
منطق المنتجات في الشات — يُستدعى من chat_service.
يجب استدعاء الدوال بعد اكتمال تحميل chat_service (تفادي استيراد دائري).
"""
from __future__ import annotations

import copy
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


def _cs():
    import logic.chat_service as m

    return m


NO_PRODUCTS_MESSAGE = "ما حصلت نفس الطلب، تبغى خيارات قريبة؟"
NO_PRODUCTS_PAYLOAD = {
    "intent": "no_products",
    "message": NO_PRODUCTS_MESSAGE,
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
    if p.startswith("/static/"):
        return p
    if p.startswith("static/"):
        return "/" + p
    return "/static/" + p.lstrip("/")


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
    بعد عرض منتجات بدون ذكر فروع: إذا أكد العميل الاهتمام نعرض الفروع/السؤال فقط.
    """
    if not session.get("pending_product_intent"):
        return None
    if not _looks_like_product_interest_confirmation(message):
        return None
    session.pop("pending_product_intent", None)
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
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
            _product_dict_for_chat(p, pid, variants, show_branch_in_chat=True)
        )
    cities = _distinct_branch_city_labels(out_products)
    d = session.get("chat_dialect") or "default"
    msg = dialect_message(d, "product_branch_prompt", name=_display_name())
    if cities:
        msg += "\n" + "\n".join(f"• {c}" for c in cities)
    return jsonify({"products": [], "message": msg, "intent": "product"})


def _product_dict_for_chat(
    p: dict, product_id: int, variants: list, *, show_branch_in_chat: bool = True
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
    return {
        "id": product_id,
        "name": p.get("product_name"),
        "description": p.get("description") or "",
        "price": price,
        "sizes": sizes,
        "colors": colors,
        "quantity": total_qty,
        "images": images,
        "img1": img1_out,
        "branches": branches,
        "detail_text": detail_text,
        "chat_text": chat_text,
        "primary_image_href": primary_href,
        "search_text": search_text,
    }


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


def _product_list_intro_message(_display_name, out_products: list, has_more: bool) -> str:
    """مقدمة قصيرة مبنية على بيانات فعلية فقط — بدون ادّعاء مخزون غير معروض."""
    d = session.get("chat_dialect") or "default"
    intro = dialect_message(d, "product_search_intro", name=_display_name())
    if has_more:
        intro += "\n" + dialect_message(d, "product_found_soft_more")
    return intro


def _build_products_response(message: str):
    """
    بحث منتجات للشات — على كل الفروع، بترتيب: قسم فرعي → فئة → اسم → وصف،
    مع توسيع المناسبات العامة (زواج، مناسبة، …).
    يعرض حتى منتجين لكل رد؛ الباقي يُعرض عند طلب «غيره».
    """
    message = normalize_for_product_search((message or "").strip())
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
            rows, has_more_tier = r, h
            break
    # إذا لم يُرجع البحث المُرتّب صفوفاً، نجرب البحث الشامل (مثل التوصيات) قبل اعتبار «لا نتائج»
    if not rows:
        for n in all_product_search_needles(needle, (message or "").strip()):
            if len(n) < 2:
                continue
            alt = get_db().search_products(n, limit=30)
            if alt:
                rows, has_more_tier = alt, False
                break
    if not rows:
        session.pop("remaining_products", None)
        session.pop("remaining_products_intent", None)
        return dict(NO_PRODUCTS_PAYLOAD)

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
        return dict(NO_PRODUCTS_PAYLOAD)

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


def _build_search_text_from_llm(cleaned: str, keywords: list, original: str) -> str:
    parts = [cleaned] if (cleaned or "").strip() else []
    for k in keywords or []:
        t = str(k).strip()
        if t and t not in parts:
            parts.append(t)
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
    d = session.get("chat_dialect") or "default"
    rec_msg = dialect_message(d, "product_search_intro", name=_display_name())
    return jsonify(
        {
            "products": shown,
            "intent": "recommendation",
            "message": rec_msg,
        }
    )


def _try_last_section_product_followup(message: str):
    """بعد نجاح استعلام قسم: رد يُفسَّر كبحث منتج داخل آخر قسم (last_section)."""
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
        return jsonify(NO_PRODUCTS_PAYLOAD)
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
        return jsonify(NO_PRODUCTS_PAYLOAD)
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
    d = session.get("chat_dialect") or "default"
    msg = dialect_message(d, "product_search_intro", name=_display_name())
    return jsonify(
        {
            "products": shown,
            "intent": "product",
            "message": msg,
        }
    )
