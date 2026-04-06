# -*- coding: utf-8 -*-
"""دوال مساعدة لأسماء الفروع (دمج/تطبيع) — مستخدمة من branch_repository و database."""
from __future__ import annotations

from typing import Optional


def _normalize_branch_city_label(name: str) -> str:
    """تطبيع اسم المدينة للمقارنة (مسافات، قص)."""
    return " ".join((name or "").split()).strip()


def _canonical_branch_city_from_input(city_name: str) -> Optional[str]:
    """
    إرجاع مفتاح BRANCHES القياسي (مثل «فرع جدة») عند التعرف على المدينة من الإعدادات.
    يُستخدم لمنع تكرار «جدة» و«فرع جدة» كفرعين منفصلين.
    """
    try:
        from site_config.branches import BRANCHES, CITY_ALIASES, _resolve_db_branch_key
    except ImportError:
        return None
    t = _normalize_branch_city_label(city_name)
    if not t:
        return None
    if t in BRANCHES:
        return t
    if t in CITY_ALIASES:
        return CITY_ALIASES[t]
    r = _resolve_db_branch_key(t)
    if r and r in BRANCHES:
        return r
    return None


def _branch_dedupe_key(city_name: str) -> str:
    """مفتاح تجميع للدمج: قياسي من config أو الاسم المطبّع."""
    c = _canonical_branch_city_from_input(city_name)
    if c:
        return f"c:{c}"
    return f"n:{_normalize_branch_city_label(city_name)}"
