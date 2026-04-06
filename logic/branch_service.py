# -*- coding: utf-8 -*-
"""
ردود الفروع في الشات (موقع، دوام، مفتوح الآن، رقم) — من قاعدة البيانات.
يُستدعى من chat_service؛ get_db عبر استيراد كسول لتفادي الدائرة.
"""
from __future__ import annotations

from logic.branch_hours import (
    format_current_time_clock_ar,
    format_minutes_until_open_ar,
    format_next_opening_clock_ar,
    format_working_hours_brief_ar,
    is_branch_open_now_from_db_rows,
    minutes_until_next_opening,
    next_opening_datetime_after,
)
from logic.chat_handlers.location_handler import build_location_messages
from logic.chat_handlers.time_handler import enhanced_location_reply_kind
from logic import chat_context as chat_ctx


def _cs():
    import logic.chat_service as m

    return m


def _branch_base_payload(
    branch_display_name: str,
    addr: str,
    map_link: str,
    phone: str,
    message: str,
    **extra,
):
    out = {
        "products": [],
        "intent": "location",
        "message": message,
        "branches": [
            {
                "name": branch_display_name,
                "location": addr,
                "map_link": map_link,
                "phone": phone,
            }
        ],
    }
    out.update(extra)
    return out


def branch_phone_payload(branch_name: str) -> dict:
    """
    رد مخصص لرقم الجوال فقط — intent: branch_phone (قواعد مباشرة، بلا AI).
    """
    cs = _cs()
    get_db = cs.get_db
    bid = get_db().get_branch_id_by_city_name(branch_name)
    info = get_db().get_branch_info(bid) if bid else None
    br = get_db().get_branch_row(bid) if bid else None
    phone = ((br or {}).get("phone") or "").strip()
    display = (info.get("city_name") if info else None) or branch_name
    nm = display
    addr = ((info or {}).get("address") or "").strip()
    map_link = ((info or {}).get("google_maps_url") or "").strip()

    if not bid:
        return {
            "products": [],
            "intent": "branch_phone",
            "message": "ما لقيت بيانات الفرع في النظام حالياً.",
            "branches": [
                {"name": nm, "location": addr, "map_link": map_link, "phone": phone}
            ],
        }
    if phone:
        msg = f"رقم الفرع: {phone}"
    else:
        msg = "حالياً ما عندي رقم هذا الفرع"
    return {
        "products": [],
        "intent": "branch_phone",
        "message": msg,
        "branches": [{"name": nm, "location": addr, "map_link": map_link, "phone": phone}],
    }


def _branch_location_json(branch_name: str, user_message: str = ""):
    """
    رد عن موقع/دوام/مفتوح/رقم — من قاعدة البيانات فقط (بدون اختراع معلومات).
    """
    cs = _cs()
    get_db = cs.get_db
    bid = get_db().get_branch_id_by_city_name(branch_name)
    info = get_db().get_branch_info(bid) if bid else None
    br = get_db().get_branch_row(bid) if bid else None
    wh_rows = get_db().get_working_hours(bid) if bid else []

    addr = ((info or {}).get("address") or "").strip()
    map_link = ((info or {}).get("google_maps_url") or "").strip()
    phone = ((br or {}).get("phone") or "").strip()
    display = (info.get("city_name") if info else None) or branch_name
    nm = display

    kind = enhanced_location_reply_kind(user_message or "")

    if not bid:
        return _branch_base_payload(nm, addr, map_link, phone, "ما لقيت بيانات الفرع في النظام حالياً.")

    # --- رقم الفرع ---
    if kind == "phone":
        if phone:
            return _branch_base_payload(nm, addr, map_link, phone, f"رقم الفرع: {phone}")
        return _branch_base_payload(nm, addr, map_link, phone, "حالياً ما عندي رقم هذا الفرع في النظام.")

    # --- الساعة الآن ---
    if kind == "clock_now":
        clk = format_current_time_clock_ar()
        return _branch_base_payload(nm, addr, map_link, phone, f"الساعة الآن تقريباً {clk} ⏰")

    # --- متابعة: يعني الساعة كم (بعد رد «نفتح بعد») ---
    if kind == "opening_clock_explain":
        nxt = chat_ctx.get_next_opening_dt()
        if nxt is not None:
            from logic.branch_hours import _fmt_time_ar

            t = nxt.time()
            msg = f"نفتح الساعة {_fmt_time_ar(t)} إن شاء الله"
            return _branch_base_payload(nm, addr, map_link, phone, msg)
        clk = format_next_opening_clock_ar(wh_rows)
        if clk:
            return _branch_base_payload(nm, addr, map_link, phone, f"نفتح الساعة {clk} إن شاء الله")
        return _branch_base_payload(nm, addr, map_link, phone, "ما قدرت أحدد الساعة بدقة من بيانات الدوام.")

    # --- مفتوح الآن ---
    if kind == "open_now":
        op = is_branch_open_now_from_db_rows(wh_rows) if wh_rows else None
        if op is True:
            return _branch_base_payload(nm, addr, map_link, phone, "ايه فاتحين حالياً ونخدمك 🙏")
        if op is False:
            clk = format_next_opening_clock_ar(wh_rows)
            nxt = next_opening_datetime_after(wh_rows)
            chat_ctx.set_next_opening_from_dt(nxt)
            if clk:
                return _branch_base_payload(nm, addr, map_link, phone, f"حالياً مغلقين، ونفتح الساعة {clk} إن شاء الله")
            return _branch_base_payload(nm, addr, map_link, phone, "حالياً مغلقين.")
        return _branch_base_payload(
            nm,
            addr,
            map_link,
            phone,
            "ما عندي أوقات دوام مسجّلة للفرع في النظام، ما أقدر أحدد إذا فاتحين ولا لا.",
        )

    # --- متى تفتحون / كم باقي ---
    if kind == "when_open":
        op = is_branch_open_now_from_db_rows(wh_rows) if wh_rows else None
        nxt = next_opening_datetime_after(wh_rows)
        chat_ctx.set_next_opening_from_dt(nxt)
        if op is False:
            mins = minutes_until_next_opening(wh_rows)
            if mins is not None:
                return _branch_base_payload(nm, addr, map_link, phone, format_minutes_until_open_ar(mins))
            clk = format_next_opening_clock_ar(wh_rows)
            if clk:
                return _branch_base_payload(nm, addr, map_link, phone, f"نفتح الساعة {clk} إن شاء الله")
            brief_fb = format_working_hours_brief_ar(wh_rows)
            if brief_fb:
                return _branch_base_payload(nm, addr, map_link, phone, brief_fb)
            return _branch_base_payload(nm, addr, map_link, phone, "ما عندي أوقات دوام مسجّلة للفرع في النظام.")
        if op is True:
            brief = format_working_hours_brief_ar(wh_rows)
            if brief:
                return _branch_base_payload(nm, addr, map_link, phone, f"{brief} وحنا فاتحين الحين.")
            return _branch_base_payload(nm, addr, map_link, phone, "حنا فاتحين الحين.")
        brief = format_working_hours_brief_ar(wh_rows)
        if brief:
            return _branch_base_payload(nm, addr, map_link, phone, brief)
        return _branch_base_payload(nm, addr, map_link, phone, "ما عندي أوقات دوام مسجّلة للفرع في النظام.")

    # --- أوقات الدوام فقط ---
    if kind == "hours":
        brief = format_working_hours_brief_ar(wh_rows)
        if brief:
            return _branch_base_payload(nm, addr, map_link, phone, brief)
        return _branch_base_payload(nm, addr, map_link, phone, "ما عندي أوقات دوام مسجّلة للفرع في النظام.")

    # --- موقع: رابط ثم عنوان منفصلان ---
    if not map_link and not addr:
        return _branch_base_payload(nm, addr, map_link, phone, "ما عندي موقع محدّث للفرع في النظام حالياً.")

    m1, m2 = build_location_messages(map_link, addr)
    payload = _branch_base_payload(
        nm,
        addr,
        map_link,
        phone,
        m1 or m2 or "ما عندي موقع محدّث.",
        followup_message=(m2 if m1 and m2 else None),
        messages=[x for x in (m1, m2) if x],
    )
    return payload
