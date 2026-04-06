# -*- coding: utf-8 -*-
"""
منطق الأقسام في الشات (عرض أقسام، اختيار قسم، last_section / pending_section_choices).
يُستدعى من chat_service — استيراد chat_service داخل الدوال فقط لتفادي الدائرة.
"""
from __future__ import annotations

from flask import jsonify, session


def _cs():
    import logic.chat_service as m

    return m


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


def _section_city_list(branch_labels: set) -> list:
    """أسماء مدن قصيرة لصياغة الرد (بدون تكرار)."""
    cities = []
    seen = set()
    for lb in sorted(branch_labels):
        raw = (lb or "").strip()
        city = raw.replace("فرع ", "").strip()
        if city and city not in seen:
            seen.add(city)
            cities.append(city)
    return cities


def _section_chat_response(message: str):
    cs = _cs()
    get_db = cs.get_db
    _branch_label_for_chat = cs._branch_label_for_chat
    rows = get_db().get_sections_by_name(message)
    if not rows:
        session.pop("last_section", None)
        session.pop("pending_section_choices", None)
        return jsonify(
            {
                "products": [],
                "message": (
                    "حالياً مو لقينا قسم مطابق بالاسم اللي ذكرته ضمن أقسام فروعنا، "
                    "لكن أقدر أساعدك بخيارات قريبة 👍 جرّب تذكر نوع اللبس أو المناسبة "
                    "ولو باختصار."
                ),
                "intent": "section",
                "sections": [],
            }
        )
    by_section = {}
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
    if len(names) > 1:
        session.pop("last_section", None)
        session["pending_section_choices"] = names
        lines = [
            "لقيت أكثر من قسم يطابق طلبك — اختار اللي تقصده بالاسم:",
        ]
        for n in names:
            cities = _section_city_list(by_section[n]["branches"])
            if len(cities) >= 2:
                loc = f" (متوفر في: {cities[0]} و{cities[1]})" if len(cities) == 2 else f" (متوفر بعدة فروع)"
            elif len(cities) == 1:
                loc = f" (متوفر في: {cities[0]})"
            else:
                loc = ""
            lines.append(f"• {n}{loc}")
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
    cities = _section_city_list(by_section[sn]["branches"])
    if len(cities) >= 3:
        loc_block = "\n".join(f"• {c}" for c in cities)
        msg = (
            f"قسم «{sn}» موجود عندنا 👍 وتلقاه حالياً في أكثر من فرع:\n"
            f"{loc_block}\n\n"
            f"تبغى موديل أو لون معيّن؟ أذكر لي أي نوع يناسبك."
        )
    elif len(cities) == 2:
        msg = (
            f"قسم «{sn}» موجود عندنا في فرع {cities[0]} وكمان {cities[1]} 👍\n\n"
            f"تبغى موديل أو لون معيّن؟ أذكر لي أي نوع يناسبك."
        )
    elif len(cities) == 1:
        msg = (
            f"قسم «{sn}» موجود حالياً في فرع {cities[0]} 👍\n\n"
            f"تبغى موديل أو لون معيّن؟ أذكر لي أي نوع يناسبك."
        )
    else:
        msg = (
            f"قسم «{sn}» مسجل عندنا في النظام 👍\n\n"
            f"تبغى موديل أو لون معيّن؟ أذكر لي أي نوع يناسبك."
        )
    return jsonify(
        {
            "products": [],
            "message": msg,
            "intent": "section",
            "sections": sections_payload,
        }
    )
