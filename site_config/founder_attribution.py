"""
معلومات المؤسس للعرض في المحادثة الذكية فقط عند سؤال المستخدم صراحة عن المصمم/المطور/المؤسس.
لا تُعرض هذه البيانات تلقائياً في الترحيب أو الردود العامة.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from config import FOUNDER_PUBLIC_FULL_NAME, FOUNDER_PUBLIC_PHONE

# عبارات صريحة تُفسَّر كسؤال عن هوية منصّب النظام/المحادثة (وليس استفساراً عاماً عن المنتجات)
_EXPLICIT_TRIGGERS = (
    "من صممك",
    "من مطورك",
    "من مؤسسك",
    "من برمجك",
    "من طورك",
    "من طوّرك",
    "مين صممك",
    "مين مطورك",
    "مين مؤسسك",
    "مين برمجك",
    "من اللي صممك",
    "من اللي طورك",
    "من اللي برمجك",
    "من اللي صمم هذا",
    "من صممك انت",
    "من صممك أنت",
)


def message_asks_for_creator_info(message: str) -> bool:
    """يُرجع True فقط عند صياغة واضحة تسأل عن المطور/المصمم/المؤسس (أنت/هذا النظام)."""
    t = (message or "").strip()
    if len(t) < 4:
        return False
    t_flat = (
        t.replace("؟", "")
        .replace("?", "")
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("طوّر", "طور")
    )
    for phrase in _EXPLICIT_TRIGGERS:
        if phrase.replace("أ", "ا").replace("إ", "ا") in t_flat:
            return True
    # من صمم/طور/برمج + هذا النظام / البوت / المحادثة / الموقع / الشات
    starts_dev = any(
        t_flat.startswith(prefix) or f" {prefix}" in t_flat
        for prefix in ("من صمم ", "من طور ", "من برمج ")
    )
    embed_dev = any(p in t_flat for p in ("من صمم هذا", "من طور هذا", "من برمج هذا"))
    if (starts_dev or embed_dev) and any(
        x in t_flat
        for x in (
            "النظام",
            "البوت",
            "المحادث",
            "الشات",
            "الموقع",
            "هذا ال",
            "هالنظام",
            "هالموقع",
        )
    ):
        return True
    # من مؤسس + المشروع / المتجر / العائلة (في سياق الهوية وليس منتجاً)
    if "من مؤسس" in t_flat and any(
        x in t_flat for x in ("المشروع", "المتجر", "العائلة", "هال", "هذا")
    ):
        return True
    return False


def build_founder_attribution_message(display_name: str) -> str:
    """نص احترافي وموجز؛ يُستخدم فقط بعد التحقق من message_asks_for_creator_info."""
    name = (display_name or "").strip() or "حضرتك"
    founder = (FOUNDER_PUBLIC_FULL_NAME or "").strip()
    phone = (FOUNDER_PUBLIC_PHONE or "").strip()
    return (
        f"نشكرك على اهتمامك يا {name}.\n\n"
        f"منظومة «العائلة FAMILY» والمحادثة الذكية هنا تنطلق من رؤية المؤسس **{founder}**، "
        f"وهو صاحب المشروع والمحور الفكري له. "
        f"للتواصل المباشر: **{phone}**.\n\n"
        f"نحن نركّز في المحادثة على خدمتك كمنتجات ومواقع وشكاوى؛ "
        f"هذه المعلومات تُذكر عندما تسأل عنها صراحةً كما فعلت الآن."
    )


def founder_attribution_payload_if_asked(message: str, display_name: str) -> Optional[Dict[str, Any]]:
    """إن وُجد سؤال صريح، يُرجع dict جاهزاً لـ jsonify؛ وإلا None."""
    if not message_asks_for_creator_info(message):
        return None
    return {
        "products": [],
        "message": build_founder_attribution_message(display_name),
        "intent": "founder_attribution",
    }
