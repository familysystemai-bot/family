# -*- coding: utf-8 -*-
"""
قوالب ردود الشات حسب اللهجة — بدون AI.
المفتاح "default": لهجة سعودية عامة (fallback).
"""
from __future__ import annotations

from typing import Any, Dict

# كل مفتاح لهجة يحوي نفس المفاتيح؛ القيم تُنسَّق بـ .format عند الحاجة (مثل {name}).
RESPONSES: Dict[str, Dict[str, str]] = {
    "default": {
        "campaign_opening": "حياك الله يا {name} 👋",
        "campaign_product_teaser": "المنتج اللي سألت عنه ({product}) صار متوفر 🔥",
        "greeting": (
            "حياك الله يا {name}، كيف أقدر أخدمك؟ "
            "تقدر تسأل عن منتج، موقع فرع، أو لو عندك ملاحظة على خدمتنا."
        ),
        "thanks": "الله يسعدك يا {name}، واجبنا.",
        "goodbye": "مع السلامة يا {name}، وشرفتنا بخدمتك.",
        "general": (
            "يا {name}، تقدر تسأل عن منتج، موقع فرع، أو تسجّل شكوى إذا احتجت."
        ),
        "unknown_fallback": (
            "أقدر أساعدك بمنتج، بقسم، بموقع فرع، أو بشكوى لو احتجت. "
            "ذكّرني باسم المنتج أو نوع اللبس اللي تدور عليه وأرشدك."
        ),
        "product_found_soft": "يا {name}، نعم متوفر 👍 هل تبحث عن شيء معين؟",
        "product_found_soft_more": "عندي خيارات أكثر إذا حاب أعرض لك.",
        "product_branch_prompt": "يا {name}، أي فرع أقرب لك؟ متوفر عندنا في:",
        "product_fallback": (
            "ما طلع عندي في البيانات الحالية مطابقة قوية لطلبك، "
            "لكن نقدر نقرب الموضوع: جرّب اسم أقرب للمنتج، أو قلّي المناسبة "
            "(زواج، سهرة، طلعة…) أو اسأل عن قسم معيّن."
        ),
        "collect_name_declined": "تمام، كيف أقدر أخدمك؟",
        "after_optional_name": "تمام يا {name}، كيف أقدر أخدمك؟",
    },
    "hijazi": {
        "greeting": "أهلين يا {name}، كيف أقدر أساعدك؟ تقدر تسأل عن منتج، موقع فرع، أو أي استفسار.",
        "thanks": "الله يحييك يا {name}، خدمتك أولى.",
        "goodbye": "مع السلامة يا {name}، بالتوفيق.",
        "general": "يا {name}، تفضل اسأل عن منتج، موقع فرع، أو شكوى إن احتجت.",
        "unknown_fallback": "أقدر أساعدك بمنتج أو فرع؛ وضّح لي وش تبي بالضبط.",
        "product_fallback": "ما لقيت مطابقة قوية؛ جرّب تصف المنتج أو المناسبة وأنا أقرب لك الخيار.",
        "collect_name_declined": "تمام، كيف أقدر أساعدك؟",
        "after_optional_name": "تمام يا {name}، كيف أقدر أساعدك؟",
    },
    "najdi": {
        "campaign_opening": "هلا والله يا {name} 👋",
        "campaign_product_teaser": "المنتج اللي سألت عنه ({product}) توفّر 🔥",
        "greeting": "هلا والله يا {name}، كيف أقدر أخدمك؟ تقدر تسأل عن منتج أو موقع فرع.",
        "thanks": "الله يعطيك العافية يا {name}.",
        "goodbye": "في أمان الله يا {name}، نورتنا.",
        "general": "يا {name}، اسأل عن منتج، موقع فرع، أو شكوى إذا تحتاج.",
        "unknown_fallback": "أقدر أساعدك؛ قلّي وش تبي بالضبط — منتج، قسم، أو فرع.",
        "product_fallback": "ما طلع عندي مطابقة واضحة؛ جرّب كلمة أوضح للمنتج أو المناسبة.",
        "collect_name_declined": "تمام، كيف أقدر أخدمك؟",
        "after_optional_name": "تمام يا {name}، كيف أقدر أخدمك؟",
    },
    "janoubi": {
        "greeting": "هلا يا {name}، كيف أقدر أخدمك؟ تقدر تسأل عن منتج أو فرع.",
        "thanks": "يعطيك العافية يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — منتج، موقع فرع، أو شكوى.",
        "unknown_fallback": "أقدر أساعدك؛ وضّح لي ذا وش تبغاه بالضبط.",
        "product_fallback": "ما لقيت مطابقة قوية؛ جرّب توصف المنتج أو المناسبة أكثر.",
        "collect_name_declined": "تمام، كيف أقدر أخدمك؟",
        "after_optional_name": "تمام يا {name}، كيف أقدر أخدمك؟",
    },
    "sharqi": {
        "greeting": "هلا يا {name}، شلون أقدر أساعدك؟ تقدر تسأل عن منتج أو فرع.",
        "thanks": "الله يخليك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، اسأل عن منتج، موقع فرع، أو شكوى.",
        "unknown_fallback": "أقدر أساعدك؛ وضّح شلون أقدر أخدمك.",
        "product_fallback": "ما طلع عندي مطابقة زينة؛ جرّب توصف المنتج أكثر.",
        "collect_name_declined": "تمام، شلون أقدر أساعدك؟",
        "after_optional_name": "تمام يا {name}، شلون أقدر أساعدك؟",
    },
    "shamali": {
        "greeting": "هلا يا {name}، كيف أقدر أخدمك؟ تقدر تسأل عن منتج أو فرع.",
        "thanks": "الله يسعدك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تقدر تسأل عن منتج، موقع فرع، أو شكوى.",
        "unknown_fallback": "أقدر أساعدك؛ وشلون أقدر أخدمك بالضبط؟",
        "product_fallback": "ما لقيت مطابقة قوية؛ جرّب توضح الطلب أكثر.",
        "collect_name_declined": "تمام، كيف أقدر أخدمك؟",
        "after_optional_name": "تمام يا {name}، كيف أقدر أخدمك؟",
    },
    "yemeni": {
        "greeting": "أهلاً يا {name}، كيف أقدر أساعدك؟ منتج، فرع، أو استفسار؟",
        "thanks": "الله يبارك فيك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، اسأل عن منتج، موقع فرع، أو شكوى.",
        "unknown_fallback": "أقدر أساعدك؛ وضّح لي ايش تبي بالضبط.",
        "product_fallback": "ما لقيت مطابقة واضحة؛ جرّب توصف المنتج أو المناسبة.",
        "collect_name_declined": "تمام، كيف أقدر أساعدك؟",
        "after_optional_name": "تمام يا {name}، كيف أقدر أساعدك؟",
    },
    "masri": {
        "greeting": "أهلاً بيك يا {name}، تحب أساعدك في إيه؟ منتج، فرع، أو أي سؤال.",
        "thanks": "الله يخليك يا {name}.",
        "goodbye": "مع السلامة يا {name}، نورتنا.",
        "general": "يا {name}، تقدر تسأل عن منتج، موقع فرع، أو شكوى.",
        "unknown_fallback": "أقدر أساعدك؛ قولّي عايز إيه بالظبط.",
        "product_fallback": "مفيش مطابقة قوية في البيانات؛ جرّب توصف المنتج أو المناسبة.",
        "collect_name_declined": "تمام، تحب أساعدك في إيه؟",
        "after_optional_name": "تمام يا {name}، تحب أساعدك في إيه؟",
    },
    "jordani": {
        "greeting": "أهلاً يا {name}، كيف بقدر أساعدك؟ منتج، فرع، أو استفسار؟",
        "thanks": "الله يعطيك العافية يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — منتج، موقع فرع، أو شكوى.",
        "unknown_fallback": "بقدر أساعدك؛ شو بدك بالضبط؟",
        "product_fallback": "ما لقيت مطابقة قوية؛ جرّب توضح شو عم تدور عليه.",
        "collect_name_declined": "تمام، كيف بقدر أساعدك؟",
        "after_optional_name": "تمام يا {name}، كيف بقدر أساعدك؟",
    },
    "iraqi": {
        "greeting": "هلا يا {name}، شلون أقدر أساعدك؟ منتج، فرع، أو سؤال؟",
        "thanks": "الله يوفقك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تقدر تسأل عن منتج، موقع فرع، أو شكوى.",
        "unknown_fallback": "أقدر أساعدك؛ وضّح شلون تريد.",
        "product_fallback": "ما طلع عندي مطابقة قوية؛ جرّب توصف المنتج أو المناسبة.",
        "collect_name_declined": "تمام، شلون أقدر أساعدك؟",
        "after_optional_name": "تمام يا {name}، شلون أقدر أساعدك؟",
    },
}


def dialect_message(dialect: str, key: str, **fmt: Any) -> str:
    """
    يرجع النص المناسب للهجة؛ أي مفتاح ناقص يُستبدل من "default".
    """
    d = (dialect or "").strip() or "default"
    block = RESPONSES.get(d) or RESPONSES["default"]
    tpl = block.get(key)
    if tpl is None:
        tpl = RESPONSES["default"].get(key, "")
    try:
        return tpl.format(**fmt) if fmt else tpl
    except KeyError:
        fallback = RESPONSES["default"].get(key, "")
        return fallback.format(**fmt) if fmt else fallback
