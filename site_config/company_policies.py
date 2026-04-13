# -*- coding: utf-8 -*-
"""
سياسات الشركة — تعدّل القيم هنا فقط؛ الردود في الشات تُبنى من هذا الهيكل دون AI.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# الهيكل الرئيسي (تعديل مباشر)
return_policy: Dict[str, Any] = {
    "exchange_days": 7,
    "return_days": 3,
    "conditions": [
        "وجود الفاتورة الأصلية",
        "المنتج بحالته الأصلية (غير مستخدم / غير مغسول / غير معطر / غير معدل)",
        "وجود بطاقة الشراء",
    ],
    "same_branch_required": True,
    "payment_method_rules": {
        "cash": "استرجاع نقدي",
        "card": "استرجاع عبر نفس البطاقة",
    },
    "special_items_24h": [
        "فساتين السهرة",
        "الإلكترونيات",
        "الملابس الداخلية",
        "العطور",
        "منتجات التجميل",
        "الملابس الموسمية",
    ],
    "non_returnable": [
        "الأقمشة",
    ],
    "offers_days": 3,
}

# طلب صريح لعرض السياسة كاملة (نص طويل)
_FULL_POLICY_REQUEST_PHRASES = (
    "كل الشروط",
    "كل السياسة",
    "الشروط كاملة",
    "التفاصيل الكاملة",
    "بالتفصيل",
    "تفصيل",
    "شرح كامل",
    "شرح مفصل",
    "ويش الشروط",
    "وش الشروط",
    "اعطني التفاصيل",
    "أبغى التفاصيل",
    "ابغى التفاصيل",
    "سياسة كاملة",
)

# مطابقة رسالة المستخدم → تسمية قصيرة للرد (أصناف 24 ساعة من special_items_24h)
_QUICK_RETURN_24H_MATCHES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("الفساتين", ("فستان", "فساتين", "فساتين سهرة", "سهرة", "سواريه", "زفاف", "خطوبة")),
    ("الإلكترونيات", ("إلكتروني", "إلكترونيات", "جوال", "موبايل", "لابتوب", "تاب", "سماعة")),
    ("الملابس الداخلية", ("داخلي", "داخلية", "ملابس داخلية")),
    ("العطور", ("عطر", "عطور", "ميسك", "دهن")),
    ("منتجات التجميل", ("تجميل", "مكياج", "كريم", "سيروم")),
    ("الملابس الموسمية", ("موسمي", "موسمية", "موسم")),
)

# أصناف لا تُسترجع (non_returnable)
_NON_RETURN_MATCHES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("الأقمشة", ("قماش", "أقمشة", "قماشة", "متر", "أمتار")),
)


def _opener_for_addressing(addressing: str) -> str:
    name = (addressing or "").strip()
    if name and name not in ("أخوي", "حضرتك"):
        return f"حياك يا {name}، "
    return "حياك، "


def _user_wants_full_return_policy_text(message: str) -> bool:
    t = (message or "").strip()
    if not t:
        return False
    return any(p in t for p in _FULL_POLICY_REQUEST_PHRASES)


def _match_24h_category_label(message: str) -> Optional[str]:
    t = (message or "").strip()
    if not t:
        return None
    for label, keywords in _QUICK_RETURN_24H_MATCHES:
        if any(k in t for k in keywords):
            return label
    return None


def _match_non_returnable_label(message: str) -> Optional[str]:
    t = (message or "").strip()
    if not t:
        return None
    for label, keywords in _NON_RETURN_MATCHES:
        if any(k in t for k in keywords):
            return label
    return None


def _special_24h_item_in_policy(label_hint: str, p: Dict[str, Any]) -> bool:
    """يتحقق أن التصنيف مذكور في سياسة الأصناف ذات المهلة القصيرة."""
    special = [str(x).strip() for x in (p.get("special_items_24h") or []) if str(x).strip()]
    if not special:
        return False
    blob = " ".join(special)
    # فساتين السهرة ↔ الفساتين
    if label_hint == "الفساتين":
        return any("فستان" in x or "سهرة" in x for x in special) or "فساتين" in blob
    if label_hint == "الإلكترونيات":
        return any("إلكترون" in x for x in special)
    if label_hint == "الملابس الداخلية":
        return any("داخل" in x for x in special)
    if label_hint == "العطور":
        return any("عطر" in x for x in special)
    if label_hint == "منتجات التجميل":
        return any("تجميل" in x for x in special)
    if label_hint == "الملابس الموسمية":
        return any("موسم" in x for x in special)
    return True


def _conditions_to_natural_phrase(conditions: List[str]) -> str:
    """تحويل الشروط إلى جملة واحدة بلسان بشري دون تعداد رسمي."""
    bits: List[str] = []
    for raw in conditions or []:
        c = (raw or "").strip()
        if not c:
            continue
        if "فاتورة" in c:
            bits.append("مع الفاتورة الأصلية")
        elif "بطاقة" in c:
            bits.append("وبطاقة الشراء")
        elif "بحالته" in c or "مستخدم" in c or "مغسول" in c or "معطر" in c:
            bits.append("والمنتج بحالته الأصلية ما يكون مستخدم ولا مغسول ولا معطر ولا معدّل")
    # بدون تكرار مع الحفاظ على الترتيب
    out: List[str] = []
    seen = set()
    for b in bits:
        if b not in seen:
            seen.add(b)
            out.append(b)
    if not out:
        return "مع استيفاء الشروط المعتادة في الفرع"
    return " ".join(out)


def _build_return_policy_full_paragraph(addressing: str) -> str:
    """نص السياسة المفصل (يُعرض عند طلب المستخدم صراحة)."""
    p = return_policy
    ex = int(p.get("exchange_days") or 0)
    ret = int(p.get("return_days") or 0)
    offers_days = int(p.get("offers_days") or 0)
    same_branch = bool(p.get("same_branch_required"))
    conditions = list(p.get("conditions") or [])
    pm = dict(p.get("payment_method_rules") or {})
    cash_txt = (pm.get("cash") or "").strip()
    card_txt = (pm.get("card") or "").strip()
    special = [str(x).strip() for x in (p.get("special_items_24h") or []) if str(x).strip()]
    nonret = [str(x).strip() for x in (p.get("non_returnable") or []) if str(x).strip()]

    opener = _opener_for_addressing(addressing)
    cond_phrase = _conditions_to_natural_phrase(conditions)

    parts: List[str] = [
        f"{opener}تقدر تستبدل خلال {ex} أيام، والاسترجاع خلال {ret} أيام، "
        f"بشرط {cond_phrase}."
    ]

    if same_branch:
        parts.append("والاستبدال والاسترجاع من نفس فرعك اللي اشتريت منه.")

    if cash_txt and card_txt:
        parts.append(f"الدفع كاش يصير {cash_txt}، والبطاقة ترجع {card_txt}.")
    elif cash_txt:
        parts.append(f"الدفع كاش: {cash_txt}.")
    elif card_txt:
        parts.append(f"الدفع بالبطاقة: {card_txt}.")

    if special:
        head = "، ".join(special[:3])
        tail = " وغيرها" if len(special) > 3 else ""
        parts.append(f"بعض الأصناف لها مهلة أقصر (24 ساعة) زي {head}{tail}.")

    if nonret:
        parts.append(f"ولا يُسترجع غالباً: {'، '.join(nonret)}.")

    if offers_days > 0:
        parts.append(f"ومن ضمن العروض غالباً {offers_days} أيام للمراجعة حسب شرط العرض.")

    return " ".join(parts)


def _build_return_policy_short_general(addressing: str) -> str:
    """رد عام مختصر بدون تفاصيل الدفع والعروض، ما لم يطلبها المستخدم."""
    p = return_policy
    ex = int(p.get("exchange_days") or 0)
    ret = int(p.get("return_days") or 0)
    same_branch = bool(p.get("same_branch_required"))
    opener = _opener_for_addressing(addressing)
    mid = (
        f"{opener}تقدر تستبدل خلال {ex} أيام، والاسترجاع خلال {ret} أيام، "
        f"بشرط إن المنتج يكون بحالته الأصلية ومع الفاتورة."
    )
    if same_branch:
        mid += " الاستبدال والاسترجاع من نفس فرع الشراء."
    mid += " لو تحتاج الشروط كاملة اكتب «كل الشروط»."
    return mid


def build_return_policy_chat_message(addressing: str = "", user_message: str = "") -> str:
    """
    رد ذكي حسب سؤال المستخدم:
    - صنف محدد (مثل فستان) → مهلة 24 ساعة للأصناف الخاصة عند التطابق.
    - غير قابل للاسترجاع (مثل أقمشة) → جملة توضيحية قصيرة.
    - بدون تفاصيل إضافية → ملخص عام قصير.
    - طلب صريح للتفاصيل → نص السياسة الكامل كما في الإعدادات.
    """
    p = return_policy
    msg = (user_message or "").strip()

    if _user_wants_full_return_policy_text(msg):
        return _build_return_policy_full_paragraph(addressing)

    nr = _match_non_returnable_label(msg)
    if nr and any(
        w in msg
        for w in (
            "استرجاع",
            "أسترجع",
            "استرجع",
            "ارجاع",
            "أرجع",
            "أبدل",
            "استبدال",
        )
    ):
        return (
            f"{_opener_for_addressing(addressing)}"
            f"غالباً {nr} ما تُسترجع حسب سياسة المتجر. "
            f"لو تحتاج استثناءات أو تفاصيل اكتب «كل الشروط»."
        )

    cat = _match_24h_category_label(msg)
    if cat and _special_24h_item_in_policy(cat, p):
        if cat == "الفساتين":
            cond = "بشرط تكون بحالتها الأصلية ومع الفاتورة"
        else:
            cond = "بشرط إن المنتج يكون بحالته الأصلية ومع الفاتورة"
        return (
            f"{_opener_for_addressing(addressing)}"
            f"تقدر تسترجع {cat} خلال 24 ساعة {cond}."
        )

    return _build_return_policy_short_general(addressing)


def build_return_policy_complaint_precheck_summary(addressing: str = "") -> str:
    """
    ملخص قصير جداً قبل تسجيل شكوى مرتبطة بالاستبدال/الاسترجاع (ليس نفس رد السياسة الكامل في الشات).
    """
    p = return_policy
    ex = int(p.get("exchange_days") or 0)
    ret = int(p.get("return_days") or 0)
    same_branch = bool(p.get("same_branch_required"))
    cond_phrase = _conditions_to_natural_phrase(list(p.get("conditions") or []))
    special = [str(x).strip() for x in (p.get("special_items_24h") or []) if str(x).strip()]
    hint_24 = ""
    if special:
        hint_24 = f" وبعض الأصناف كـ {special[0]} و{special[1]} غالباً لها مهلة أقصر." if len(special) > 1 else f" وبعض الأصناف كـ {special[0]} غالباً لها مهلة أقصر."

    name = (addressing or "").strip()
    opener = (
        f"حياك يا {name}، "
        if name and name not in ("أخوي", "حضرتك")
        else "حياك، "
    )
    mid = (
        f"{opener}حسب سياسة الاستبدال والاسترجاع: تقدر تستبدل خلال {ex} أيام "
        f"وتسترجع خلال {ret} أيام، بشرط {cond_phrase}."
    )
    if same_branch:
        mid += " ولازم يكون من نفس فرع الشراء."
    mid += hint_24
    mid += (
        " إذا ما انطبق عليك الوضع أو حسيت إن ما تم الالتزام، قُل **نعم** أو **سجّل الشكوى** وأكمل لك."
    )
    return mid


def policies_text_for_ai_context(max_chars: int = 3500) -> str:
    """
    ملخص نصي من `return_policy` للمنسّق والنماذج (استبدال/استرجاع/شروط/أصناف خاصة).
    يُقرأ من نفس الهيكل الذي تُبنى منه ردود الشات — لا يُخترع من النموذج.
    """
    p = return_policy
    lines: List[str] = [
        f"الاستبدال خلال {int(p.get('exchange_days') or 0)} يوم تقويمي.",
        f"الاسترجاع خلال {int(p.get('return_days') or 0)} يوم تقويمي.",
        f"عروض/عروض موسمية غالباً {int(p.get('offers_days') or 0)} يوم حسب شرط العرض.",
        "نفس الفرع مطلوب للاستبدال/الاسترجاع: "
        + ("نعم" if p.get("same_branch_required") else "حسب تعليمات الفرع"),
    ]
    conds = [str(c).strip() for c in (p.get("conditions") or []) if str(c).strip()]
    if conds:
        lines.append("شروط عامة: " + "؛ ".join(conds))
    pm = dict(p.get("payment_method_rules") or {})
    if pm:
        lines.append(
            "قواعد طريقة الدفع: "
            + "؛ ".join(f"{k}: {v}" for k, v in pm.items() if (v or "").strip())
        )
    sp = [str(x).strip() for x in (p.get("special_items_24h") or []) if str(x).strip()]
    if sp:
        lines.append(
            "أصناف غالباً لها مهلة أقصر (مثلاً 24 ساعة): " + "، ".join(sp[:12])
        )
    nr = [str(x).strip() for x in (p.get("non_returnable") or []) if str(x).strip()]
    if nr:
        lines.append("غير قابل للاسترجاع غالباً: " + "، ".join(nr[:12]))
    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text
