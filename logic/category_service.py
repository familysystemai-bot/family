# -*- coding: utf-8 -*-
"""
منطق الأقسام في الشات (عرض أقسام، اختيار قسم، last_section / pending_section_choices).
يُستدعى من chat_service — استيراد chat_service داخل الدوال فقط لتفادي الدائرة.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import jsonify, session

from logic import keywords as kw


def _cs():
    import logic.chat_service as m

    return m


def _normalize_chat_text(message: str) -> str:
    from logic.chat_service import normalize_message_for_branch_search

    return normalize_message_for_branch_search((message or "").strip())


def message_asks_full_category_catalog(message: str) -> bool:
    """سؤال عن كل أقسام المجمع — من الجدول محلياً دون مسار منتج."""
    t = _normalize_chat_text(message)
    if not t:
        return False
    if any(p in t for p in kw.STORE_CATEGORY_CATALOG_STRONG_PHRASES):
        return True
    if any(p in t for p in ("وش تبيعون", "وش تبعون", "ايش تبيعون", "ايش تبعون")):
        return True
    if ("اشرح" in t or "اشرحلي" in t) and any(
        x in t for x in ("عندكم", "عندك", "المجمع", "قسم", "أقسام", "الأقسام")
    ):
        return True
    if any(x in t for x in ("وش عندكم", "ايش عندكم", "وش عندك", "ايش عندك")):
        if any(x in t for x in ("قسم", "أقسام", "الأقسام", "المجمع", "كل ", "كلّ")):
            return True
        if "من" in t and any(x in t for x in ("قسم", "اقسام", "أقسام", "الاقسام", "الأقسام")):
            return True
    if any(x in t for x in ("من اقسام", "من الاقسام", "من الأقسام", "من القسم")):
        if any(x in t for x in ("وش", "ايش", "شنو", "وشو", "كم", "شلون", "ايه", "إيه")):
            return True
    if "كم قسم" in t:
        return True
    if "كم عدد" in t and any(
        x in t for x in ("قسم", "اقسام", "أقسام", "الأقسام", "الاقسام")
    ):
        return True
    tn = (
        t.replace("؟", "")
        .replace("?", "")
        .replace(" ", "")
        .replace("ٱ", "ا")
    )
    if tn in ("وشعندكم", "ايشعندكم", "وشعندك", "ايشعندك"):
        return True
    return False


def message_asks_clothing_departments_overview(message: str) -> bool:
    """
    استفسار إن كان عندنا أقسام ملابس (ميتا) — ليس طلب منتج محدد.
    """
    if message_asks_full_category_catalog(message):
        return False
    t = _normalize_chat_text(message)
    if not t:
        return False
    meta = (
        "ما عندكم" in t
        or "ما عندك" in t
        or "وش عندكم" in t
        or "ايش عندكم" in t
        or "هل عندكم" in t
        or "في عندكم" in t
        or "فيه عندكم" in t
    )
    if not meta:
        return False
    return any(x in t for x in ("ملابس", "ثياب", "لبس"))


def section_all_main_categories_response():
    """رد محلي: أسماء الفئات الرئيسية من قاعدة البيانات."""
    cs = _cs()
    rows = cs.get_db().get_main_categories() or []
    names: List[str] = []
    seen = set()
    for r in rows:
        n = (r.get("name") or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    names.sort()
    if not names:
        return jsonify(
            {
                "products": [],
                "message": "ما قدرت أطلع قائمة الأقسام الحين، جرّب بعد لحظات.",
                "intent": "section",
                "sections": [],
            }
        )
    listed = "، ".join(names)
    msg = f"عندنا في المجمع هالأقسام: {listed} 🛍️ أي قسم يهمك؟"
    session.pop("pending_section_choices", None)
    session.pop("last_section", None)
    session.pop("pending_intent", None)
    session["chat_current_intent"] = "section"
    try:
        from logic import chat_context as _cx

        _cx.set_last_intent("section")
    except Exception:
        pass
    payload_sections = [{"name": n, "branches": []} for n in names]
    return jsonify(
        {
            "products": [],
            "message": msg,
            "intent": "section",
            "sections": payload_sections,
        }
    )


def section_clothing_main_categories_response():
    """رد محلي: أقسام الملابس/الثياب المسجّلة في الفئات الرئيسية."""
    cs = _cs()
    rows = cs.get_db().get_main_categories() or []
    names: List[str] = []
    seen = set()
    for r in rows:
        n = (r.get("name") or "").strip()
        if not n or n in seen:
            continue
        blob = n.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").lower()
        if "ملابس" in blob or "ثياب" in blob or "لبس" in blob:
            seen.add(n)
            names.append(n)
    names.sort()
    session.pop("pending_section_choices", None)
    session.pop("last_section", None)
    session.pop("pending_intent", None)
    session["chat_current_intent"] = "section"
    try:
        from logic import chat_context as _cx

        _cx.set_last_intent("section")
    except Exception:
        pass
    if not names:
        return jsonify(
            {
                "products": [],
                "message": "ما ظهر عندنا حالياً أقسام ملابس مسمّاة بهالشكل في النظام.",
                "intent": "section",
                "sections": [],
            }
        )
    listed = "، ".join(names)
    msg = f"عندنا أقسام ملابس زي كذا: {listed} 🛍️ أي قسم يهمك؟"
    payload_sections = [{"name": n, "branches": []} for n in names]
    return jsonify(
        {
            "products": [],
            "message": msg,
            "intent": "section",
            "sections": payload_sections,
        }
    )


_FRIENDLY_MAIN_CATEGORY_LABEL = {
    "ملابس رجالي": "الملابس الرجالية",
    "ملابس نسائي": "الملابس النسائية",
    "ملابس ولادي": "ملابس الأولاد",
    "ملابس بناتي": "ملابس البنات",
    "ملابس مواليد": "ملابس المواليد",
}


def _apparel_gender_bucket(message: str) -> Optional[str]:
    """
    تمييز ملابس رجالية vs نسائية لطلبات مثل «بناطيل» (الافتراضي رجالي حسب أسلوب العملاء).
    """
    t = _normalize_chat_text(message)
    if not t:
        return None
    women = (
        "نسائي",
        "نساء",
        "نسوان",
        "حريم",
        "بناتي",
        "البنات",
        "للنساء",
        "نسا",
    )
    men = (
        "رجالي",
        "رجال",
        "رجالية",
        "رجاليه",
        "للرجال",
        "ملابس رجال",
        "ثياب رجال",
        "غتر",
        "شماغ",
        "عقال",
        "بشت",
        "ثوب",
    )
    has_w = any(w in t for w in women)
    has_m = any(m in t for m in men)
    if has_w and not has_m:
        return "women"
    if has_m:
        return "men"
    pants = ("بناطيل", "بنطلون", "بنطلونات", "سروال", "سراويل")
    if any(p in t for p in pants):
        return "men"
    return None


def _filter_rows_by_apparel_bucket(
    rows: List[Dict[str, Any]], bucket: Optional[str]
) -> List[Dict[str, Any]]:
    if not rows or not bucket:
        return rows
    out: List[Dict[str, Any]] = []
    for r in rows:
        mc = (r.get("category_name") or "").strip()
        if bucket == "men" and mc == "ملابس رجالي":
            out.append(r)
        elif bucket == "women" and "نسائي" in mc:
            out.append(r)
    return out if out else rows


def _unique_main_categories(rows: List[Dict[str, Any]]) -> List[str]:
    s = {(r.get("category_name") or "").strip() for r in rows if (r.get("category_name") or "").strip()}
    return sorted(s)


def _preferred_sub_name_for_main(main_cat: str, section_names: List[str]) -> str:
    sn = sorted(set(section_names))
    if main_cat == "ملابس رجالي":
        for prefer in ("ثياب رجالي", "بناطيل رجالي", "تشيرتات رجالي"):
            if prefer in sn:
                return prefer
    if main_cat == "ملابس نسائي":
        for prefer in ("فساتين", "بناطيل نسائي"):
            if prefer in sn:
                return prefer
    return sn[0] if sn else ""


def _department_ack_message(main_cat: str, section_names: List[str]) -> str:
    label = _FRIENDLY_MAIN_CATEGORY_LABEL.get(main_cat, main_cat)
    if main_cat == "ملابس رجالي":
        return f"نعم، عندنا قسم {label} 👔 وش تبي منه بالضبط؟"
    if main_cat == "ملابس نسائي":
        return f"نعم، عندنا قسم {label} 🌸 وش تبين منه بالضبط؟"
    return f"نعم، عندنا قسم {label} ✨ وش تبي منه بالضبط؟"


def _looks_like_section_stock_question(message: str) -> bool:
    """
    أسئلة مثل «عندكم فساتين؟» — استفسار عن توفر قسم/فئة دون طلب شراء صريح (أبغى/أريد).
    """
    cs = _cs()
    t = (message or "").strip()
    if not t:
        return False
    if any(
        k in t
        for k in (
            "أبغى",
            "ابغى",
            "أبغا",
            "أريد",
            "اريد",
            "ابي",
            "أبي",
            "ودي",
            "اطلب",
            "أطلب",
        )
    ):
        return False
    if any(
        k in t
        for k in (
            "شكوى",
            "شكاوى",
            "توصيل",
            "شحن",
            "موقع الفرع",
            "عنوان",
            "رقم",
            "اتصال",
        )
    ):
        return False
    if not cs.get_db()._extract_section_search_words(message):
        return False
    if any(k in t for k in ("قسم", "أقسام", "اقسام", "الأقسام", "الاقسام", "القسم")):
        return True
    markers = (
        "عندكم",
        "عندك",
        "هل عندكم",
        "في عندكم",
        "فيه عندكم",
        "تبيعون",
        "متوفرة",
        "متوفر",
        "فيه",
        "فيها",
    )
    return any(m in t for m in markers) and len(t) < 160


def _try_resolve_pending_section_choice(message: str):
    """عند وجود أكثر من قسم: يطابق اختيار المستخدم ويحفظ last_section."""
    choices = session.get("pending_section_choices")
    if not choices:
        return None
    t = (message or "").strip()
    if not t:
        return None
    if any(s in t for s in kw.CATEGORY_BROWSE_CORRECTION_STRONG):
        session.pop("pending_section_choices", None)
        return None
    _sec_kw = ("قسم", "أقسام", "اقسام", "الأقسام", "الاقسام", "القسم")
    if any(k in t for k in _sec_kw):
        session.pop("pending_section_choices", None)
        return None

    hits = [c for c in choices if c in t]
    picked = None
    if len(hits) == 1:
        picked = hits[0]
    elif len(hits) > 1:
        picked = max(hits, key=len)
    else:
        sub = [c for c in choices if len(t) >= 2 and t in c]
        if len(sub) == 1:
            picked = sub[0]
        elif len(sub) > 1:
            return jsonify(
                {
                    "products": [],
                    "message": "حدد أي قسم تقصد بالاسم الكامل.",
                    "intent": "section",
                }
            )

    if not picked:
        return None

    session.pop("pending_section_choices", None)
    session["last_section"] = picked
    return jsonify(
        {
            "products": [],
            "message": (
                f"تم اختيار قسم «{picked}» 👍 اذكر لي نوع الموديل أو اللون اللي تبغاه "
                f"وأعرض لك اللي يناسبك من نفس القسم."
            ),
            "intent": "section",
            "sections": [{"name": picked, "branches": []}],
        }
    )


def _section_chat_response(message: str):
    cs = _cs()
    get_db = cs.get_db
    _branch_label_for_chat = cs._branch_label_for_chat
    if message_asks_full_category_catalog(message):
        return section_all_main_categories_response()
    if message_asks_clothing_departments_overview(message):
        return section_clothing_main_categories_response()

    bucket = _apparel_gender_bucket(message)
    rows = get_db().get_sections_by_name(message)
    rows = _filter_rows_by_apparel_bucket(rows, bucket)
    if not rows and bucket == "men":
        rows = get_db().get_sections_by_name("ثياب رجالي")
    if not rows and bucket == "men":
        rows = get_db().get_sections_by_name("ملابس رجالي")
    if not rows:
        session.pop("last_section", None)
        session.pop("pending_section_choices", None)
        return jsonify(
            {
                "products": [],
                "message": "",
                "intent": "section",
                "sections": [],
            }
        )
    by_section: dict[str, dict] = {}
    for r in rows:
        sn = (r.get("section_name") or "").strip() or "القسم"
        if sn not in by_section:
            by_section[sn] = {"branches": set()}
        lbl = _branch_label_for_chat(r.get("branch_city_name") or "")
        if lbl:
            by_section[sn]["branches"].add(lbl)
    names = sorted(by_section.keys())
    sections_payload = [
        {"name": k, "branches": sorted(v["branches"])} for k, v in sorted(by_section.items())
    ]
    mains = _unique_main_categories(rows)
    if len(mains) == 1 and len(names) > 1:
        main_only = mains[0]
        session.pop("pending_section_choices", None)
        pick = _preferred_sub_name_for_main(main_only, names)
        session["last_section"] = pick or names[0]
        try:
            from logic import chat_context as _cx

            _cx.set_last_intent("section")
            _cx.set_last_product_or_section(session["last_section"])
            if rows:
                _cn = (rows[0].get("branch_city_name") or "").strip()
                if _cn:
                    _cx.remember_branch_by_name(_cn)
        except Exception:
            pass
        msg = _department_ack_message(main_only, names)
        return jsonify(
            {
                "products": [],
                "message": msg,
                "intent": "section",
                "sections": sections_payload,
            }
        )

    if len(names) > 1:
        session.pop("last_section", None)
        session["pending_section_choices"] = names
        lines = [
            "في أكثر من قسم قريب من طلبك — وش بالضبط يهمك؟ اختَر من القائمة أو اكتب الاسم كامل:",
        ]
        for n in names:
            lines.append(f"• {n}")
        msg = "\n".join(lines)
        return jsonify(
            {
                "products": [],
                "message": msg,
                "intent": "section",
                "sections": sections_payload,
            }
        )

    session.pop("pending_section_choices", None)
    session["last_section"] = names[0]
    try:
        from logic import chat_context as _cx

        _cx.set_last_intent("section")
        _cx.set_last_product_or_section(names[0])
        if rows:
            _cn = (rows[0].get("branch_city_name") or "").strip()
            if _cn:
                _cx.remember_branch_by_name(_cn)
    except Exception:
        pass
    sn = names[0]
    main_one = (rows[0].get("category_name") or "").strip() if rows else ""
    if main_one == "ملابس رجالي" and bucket == "men":
        msg = _department_ack_message(main_one, [sn])
    else:
        msg = f"نعم، عندنا قسم {sn} 🌸 تبي أشوف لك شي معين؟"
    return jsonify(
        {
            "products": [],
            "message": msg,
            "intent": "section",
            "sections": sections_payload,
        }
    )
