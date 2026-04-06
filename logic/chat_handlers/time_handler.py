# -*- coding: utf-8 -*-
"""
تصنيف فرعي لأسئلة الوقت/الدوام/«الساعة كم» — يُكمّل intent_handler.location_reply_kind.
"""
from __future__ import annotations

from logic.intent_handler import location_reply_kind as _base_location_reply_kind


def enhanced_location_reply_kind(user_message: str) -> str:
    """
    يرجع: phone | open_now | location_link | when_open | hours | clock_now |
          opening_clock_explain | default
    """
    t = (user_message or "").strip()
    if not t:
        return "default"
    tn = t.replace("؟", " ").replace("?", " ").replace("ٱ", "ا").replace("ى", "ي")

    # رقم + فرع
    if "رقم" in tn and "فرع" in tn:
        return "phone"

    # الساعة الآن (بدون ربط بافتتاح)
    if any(
        x in tn
        for x in (
            "كم الساعة",
            "كم الساعه",
            "الساعة كم",
            "الساعه كم",
            "كم الساعة الحين",
            "كم الساعه الحين",
        )
    ):
        if "نفتح" not in tn and "بعد" not in tn and "يفتح" not in tn:
            return "clock_now"

    # متابعة بعد رد «نفتح بعد X»
    if any(
        x in tn
        for x in (
            "يعني الساعة كم",
            "يعني الساعه كم",
            "الساعة كم يعني",
            "طيب الساعة كم",
            "كم يعني",
        )
    ):
        return "opening_clock_explain"

    # دوام / صباح / بكرة / اليوم
    time_ctx = any(
        k in tn
        for k in (
            "الصباح",
            "صباح",
            "بكرة",
            "غدا",
            "غداً",
            "اليوم",
            "الليل",
            "الظهر",
            "المغرب",
        )
    )
    if time_ctx and any(
        k in tn for k in ("متى", "تفتح", "تفتحون", "دوام", "ساعات", "وقت", "ينفتح", "تقفل")
    ):
        return "hours"

    if "تفتحون" in tn and "صباح" in tn:
        return "hours"

    return _base_location_reply_kind(user_message)
