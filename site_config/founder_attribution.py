"""
معلومات عامة عن المنصّة للعرض في المحادثة الذكية فقط عند مطابقة عبارات محدّدة
(سؤال صريح عن هوية المحادث / من طور النظام / من أنت…).
لا تُعرض هذه البيانات تلقائياً في الترحيب أو الردود العامة.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

_COMPANY_CONTACT_EMAIL = (
    (os.getenv("COMPANY_CHAT_ATTRIBUTION_EMAIL") or "").strip()
    or (os.getenv("ADMIN_OFFICE_EMAIL") or "").strip()
    or (os.getenv("MAIN_RECEIVER_EMAIL") or "").strip()
    or "management@family-mall.com"
)
_FOUNDER_PUBLIC_PHONE = (os.getenv("FOUNDER_PUBLIC_PHONE") or "").strip()

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
    "مين طورك",
    "من اللي صممك",
    "من اللي طورك",
    "من اللي برمجك",
    "من اللي صمم هذا",
    "من صممك انت",
    "من صممك أنت",
    "من انت",
    "مين انت",
    "انت مين",
    "من تكون",
    "مين تكون",
)

_LLM_WHO_PREFIX = "أنا نموذج لغوي (Language Model).\n\n"


def _build_attribution_body() -> str:
    lines = [
        "هذه منصّة «مجمع العائلة» الرقمية — لخدمتكم وتيسير التواصل ومتابعة الطلبات.",
        "نسعى لتطويرها باستمرار بما يخدم تجربتكم.",
        "",
        "للملاحظات أو الاقتراحات يمكنكم التواصل عبر القنوات الرسمية لمجمع العائلة:",
    ]
    if _FOUNDER_PUBLIC_PHONE:
        lines.append(f"الهاتف: {_FOUNDER_PUBLIC_PHONE}")
    lines.append(f"البريد: {_COMPANY_CONTACT_EMAIL}")
    lines.extend(["", "شكراً لكم — مجمع العائلة."])
    return "\n".join(lines)


def _normalize_for_match(message: str) -> str:
    t = (message or "").strip()
    return (
        t.replace("؟", "")
        .replace("?", "")
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("طوّر", "طور")
        .replace("ى", "ي")
    )


def _is_who_are_you_question(t_flat: str) -> bool:
    """سؤال مباشر عن هوية المحادث (من أنت / مين أنت …) — يُضاف مقدمة نموذج لغوي."""
    needles = (
        "من انت",
        "مين انت",
        "انت مين",
        "من تكون",
        "مين تكون",
    )
    for n in needles:
        if n in t_flat:
            return True
    if t_flat in needles:
        return True
    return False


def message_asks_for_creator_info(message: str) -> bool:
    """يُرجع True عند عبارات محددة: مطوّر/مؤسس/من أنت… أو من صمم النظام…"""
    t = (message or "").strip()
    if not t:
        return False
    t_flat = _normalize_for_match(message)
    if len(t_flat) < 2:
        return False

    for phrase in _EXPLICIT_TRIGGERS:
        if phrase.replace("أ", "ا").replace("إ", "ا") in t_flat:
            return True

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

    if "من مؤسس" in t_flat and any(
        x in t_flat for x in ("المشروع", "المتجر", "العائلة", "هال", "هذا")
    ):
        return True
    return False


def build_founder_attribution_message(
    _display_name: str, *, prepend_llm_intro: bool = False
) -> str:
    """النص الكامل بعد التحقق من message_asks_for_creator_info."""
    parts: list[str] = []
    if prepend_llm_intro:
        parts.append(_LLM_WHO_PREFIX.rstrip())
    parts.append(_build_attribution_body())
    return "\n\n".join(parts)


def founder_attribution_payload_if_asked(message: str, display_name: str) -> Optional[Dict[str, Any]]:
    """إن وُجدت عبارة مطابقة، يُرجع dict جاهزاً لـ jsonify؛ وإلا None."""
    if not message_asks_for_creator_info(message):
        return None
    t_flat = _normalize_for_match(message)
    prepend = _is_who_are_you_question(t_flat)
    return {
        "products": [],
        "message": build_founder_attribution_message(
            display_name,
            prepend_llm_intro=prepend,
        ),
        "intent": "founder_attribution",
    }
