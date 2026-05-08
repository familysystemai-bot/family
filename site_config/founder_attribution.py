"""
معلومات المؤسس للعرض في المحادثة الذكية فقط عند مطابقة عبارات محددة
(هوية النظام/المطور/المؤسس أو ذكر اسم كاظم صراحة).
لا تُعرض هذه البيانات تلقائياً في الترحيب أو الردود العامة.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

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

_ATTRIBUTION_BODY = (
    "تم بناء وتطوير هذا النظام بواسطة كاظم المطحني؛ لخدمتك وتسهيل التواصل وتتبع طلباتك. "
    "ونسعى لتطويره على ميزات مستقبلية ذكية تساعدك في اختيار ما يناسبك بدقة، مثل إمكانية تجربة المنتجات "
    "ومعاينتها في منزلك افتراضياً باستخدام الذكاء الاصطناعي قبل الشراء لضمان أنك تختار الأنسب لك وأنت في مكانك.\n\n"
    "ولو عندك أي ملاحظة أو تحسين، لا تتردد بالتواصل وإعطاء ملاحظتك مباشرة عبر:\n\n"
    "+966538344673\n"
    "+967773216649\n\n"
    "البريد الإلكتروني: Almthnyalkazm@gmail.com\n\n"
    "شكراً لك."
)


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
    """يُرجع True فقط عند عبارات محددة: مطوّر/مؤسس/من أنت… أو ذكر «كاظم» في الرسالة."""
    t = (message or "").strip()
    if not t:
        return False
    t_flat = _normalize_for_match(message)
    if len(t_flat) < 2:
        return False

    if "كاظم" in t_flat:
        return True

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
    """النص الكامل بعد التحقق من message_asks_for_creator_info.
    _display_name يُمرَّر من مسار الشات للتوافق؛ المحتوى ثابت كما طُلب.
    """
    parts: list[str] = []
    if prepend_llm_intro:
        parts.append(_LLM_WHO_PREFIX.rstrip())
    parts.append(_ATTRIBUTION_BODY)
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
