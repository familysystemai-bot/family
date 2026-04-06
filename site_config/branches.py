# -*- coding: utf-8 -*-
"""
بيانات الفروع الثابتة (عناوين، خرائط، دوام للشات، وبذور قاعدة البيانات).
مفاتيح BRANCHES تطابق branches.city_name في قاعدة البيانات.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# يطابق INSERT في logic/database.py — لا تغيّر المفاتيح دون تعديل قاعدة البيانات
BRANCHES: Dict[str, Dict[str, Any]] = {
    "فرع جدة": {
        "city_key": "jeddah",
        "city_ar": "جدة",
        "address": "جدة، مدائن الفهد",
        "google_maps_url": "https://maps.app.goo.gl/CHPbZzNTDpsabgWdA",
        "hours_chat_ar": (
            "الدوام يومياً (عدا الجمعة) على فترتين: من 8:30 صباحاً إلى 12 ظهراً، ومن 4 عصراً إلى 12 ليلاً.\n"
            "يوم الجمعة: من 4 عصراً إلى 12 ليلاً.\n"
            "يتغير الدوام في رمضان."
        ),
        "working_hours": {
            "weekday_open": "08:30",
            "weekday_close": "00:00",
            "friday_open": "16:00",
            "friday_close": "00:00",
        },
    },
    "فرع مكة": {
        "city_key": "makkah",
        "city_ar": "مكة",
        "address": "مكة، طريق المسجد الحرام، العزيزية",
        "google_maps_url": "https://maps.app.goo.gl/df2AMkx8rhWvs2xw5",
        "hours_chat_ar": (
            "الدوام يومياً (عدا الجمعة): من 8:30 صباحاً إلى 12 ليلاً.\n"
            "يوم الجمعة: من 4 عصراً إلى 12 ليلاً.\n"
            "يتغير الدوام في رمضان والحج."
        ),
        "working_hours": {
            "weekday_open": "08:30",
            "weekday_close": "00:00",
            "friday_open": "16:00",
            "friday_close": "00:00",
        },
    },
    "فرع خميس مشيط": {
        "city_key": "khamis",
        "city_ar": "خميس مشيط",
        "address": "خميس مشيط، طريق المدينة العسكرية",
        "google_maps_url": "https://maps.app.goo.gl/dpPNSNk1UBBmuPz99",
        "hours_chat_ar": (
            "الدوام يومياً (عدا الجمعة): من 8:30 صباحاً إلى 12 ليلاً.\n"
            "يوم الجمعة: من 4 عصراً إلى 12 ليلاً.\n"
            "يتغير الدوام في رمضان."
        ),
        "working_hours": {
            "weekday_open": "08:30",
            "weekday_close": "00:00",
            "friday_open": "16:00",
            "friday_close": "00:00",
        },
    },
    "فرع المدينة": {
        "city_key": "madinah",
        "city_ar": "المدينة المنورة",
        "address": "المدينة المنورة، طريق الملك عبدالله (الدائري)",
        "google_maps_url": "https://maps.app.goo.gl/HLiiGuXJBNysZnZt7",
        "hours_chat_ar": (
            "الدوام يومياً (عدا الجمعة): من 8:30 صباحاً إلى 12 ليلاً.\n"
            "يوم الجمعة: من 4 عصراً إلى 12 ليلاً.\n"
            "يتغير الدوام في رمضان."
        ),
        "working_hours": {
            "weekday_open": "08:30",
            "weekday_close": "00:00",
            "friday_open": "16:00",
            "friday_close": "00:00",
        },
    },
    "فرع قلوة": {
        "city_key": "qilwah",
        "city_ar": "قلوة",
        "address": "قلوة، السوق الشعبي",
        "google_maps_url": "https://maps.app.goo.gl/6LkoQ3oJG9tV26gn9",
        "hours_chat_ar": (
            "الدوام يومياً (عدا الجمعة) على فترتين: من 8:30 صباحاً إلى 12 ظهراً، ومن 4 عصراً إلى 12 ليلاً.\n"
            "يوم الجمعة: من 4 عصراً إلى 12 ليلاً.\n"
            "يتغير الدوام في رمضان."
        ),
        "working_hours": {
            "weekday_open": "08:30",
            "weekday_close": "00:00",
            "friday_open": "16:00",
            "friday_close": "00:00",
        },
    },
}

# للتطابق من نص حر (أسئلة العملاء)
CITY_ALIASES: Dict[str, str] = {
    "جدة": "فرع جدة",
    "جده": "فرع جدة",
    "مكة": "فرع مكة",
    "مكه": "فرع مكة",
    "مكة المكرمة": "فرع مكة",
    "خميس مشيط": "فرع خميس مشيط",
    "خميس": "فرع خميس مشيط",
    "المدينة": "فرع المدينة",
    "المدينه": "فرع المدينة",
    "المدينة المنورة": "فرع المدينة",
    "المدينه المنوره": "فرع المدينة",
    "قلوة": "فرع قلوة",
    "قلوه": "فرع قلوة",
}


def _resolve_db_branch_key(text: str) -> Optional[str]:
    """أطول تطابق لاسم مدينة في النص → مفتاح BRANCHES."""
    if not text:
        return None
    t = text.strip()
    best: Optional[str] = None
    best_len = 0
    for name, db_key in CITY_ALIASES.items():
        if name in t and len(name) > best_len:
            best_len = len(name)
            best = db_key
    return best


def get_branch(branch_label: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    branch_label: كما في قاعدة البيانات (مثل فرع جدة) أو نص يحتوي اسم مدينة.
    """
    if not branch_label:
        return None
    s = branch_label.strip()
    if s in BRANCHES:
        return BRANCHES[s]
    dbk = _resolve_db_branch_key(s)
    if dbk and dbk in BRANCHES:
        return BRANCHES[dbk]
    return None


def branch_list_lines() -> List[str]:
    lines: List[str] = []
    for _k, data in BRANCHES.items():
        ca = data.get("city_ar") or ""
        if ca:
            lines.append(f"• {ca}")
    return lines


def get_management_emails() -> List[str]:
    raw = os.getenv("MANAGEMENT_EMAILS") or os.getenv("ADMIN_CC_EMAILS") or ""
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


