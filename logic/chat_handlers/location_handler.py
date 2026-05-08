# -*- coding: utf-8 -*-
"""
تنسيق ردود الموقع (رابط + عنوان) لعرض الواجهة.
"""


def build_location_messages(map_link: str, address: str) -> tuple[str, str]:
    """
    رسالة 1: الرابط فقط (قابل للنقر في الواجهة).
    رسالة 2: العنوان مع أيقونة.
    """
    m1 = (map_link or "").strip()
    addr = (address or "").strip()
    m2 = f"📍 العنوان: {addr}" if addr else ""
    return m1, m2
