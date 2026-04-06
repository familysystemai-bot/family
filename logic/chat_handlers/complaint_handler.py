# -*- coding: utf-8 -*-
"""
نصوص ثابتة لمسار الشكوى — صيغ اعتذار ورسالة نجاح.
"""
from __future__ import annotations

import random

APOLOGY_VARIANTS = (
    "نعتذر لك عن اللي صار 🙏 وإن شاء الله نحلها بأسرع وقت.",
    "نعتذر منك جداً على اللي صار، ونسعى نخدمك بأسرع ما يمكن 🙏",
    "نأسف على التجربة السيئة، وإن شاء الله نتابع موضوعك باهتمام 🙏",
)

SUCCESS_REGISTERED = (
    "تم تسجيل شكواك ورفعها للإدارة ✅ "
    "وراح يتم متابعتها بإذن الله."
)


def random_opening_apology() -> str:
    return random.choice(APOLOGY_VARIANTS)


def success_message() -> str:
    return SUCCESS_REGISTERED
