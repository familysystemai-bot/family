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
        "greeting": "حياك الله يا {name}، بوش تامرني؟",
        "thanks": "الله يسعدك يا {name}، واجبنا.",
        "goodbye": "مع السلامة يا {name}، وشرفتنا.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "ما لقطت عليك… وضّح لي وش تدور بالضبط؟",
        "server_error": (
            "صار عندنا بطء بالنظام يا {name}، جرّب بعد لحظة."
        ),
        "product_found_soft": "يا {name}، نعم متوفر 👍 وش تبي بالضبط؟",
        "product_search_intro": "يا {name}، هذا اللي عندنا 👇",
        "product_available_ack": "نعم متوفر 👍\nتبغى موديل معين؟",
        "product_found_soft_more": "إذا حاب تشوف أكثر قلّي «غيره» أو «ورّني زيادة».",
        "product_branch_prompt": "يا {name}، أي فرع أقرب لك؟ متوفر عندنا في:",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، بوش تامرني؟",
        "after_optional_name": "تمام يا {name}، بوش تامرني؟",
    },
    "hijazi": {
        "greeting": "أهلين يا {name}، بوش تامرني؟",
        "thanks": "الله يحييك يا {name}، خدمتك أولى.",
        "goodbye": "مع السلامة يا {name}، بالتوفيق.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "ما فهمت ولا زين… قلّي وش تبي.",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، بوش تامرني؟",
        "after_optional_name": "تمام يا {name}، بوش تامرني؟",
    },
    "najdi": {
        "campaign_opening": "هلا والله يا {name} 👋",
        "campaign_product_teaser": "المنتج اللي سألت عنه ({product}) توفّر 🔥",
        "greeting": "هلا والله يا {name}، بوش تامرني؟",
        "thanks": "الله يعطيك العافية يا {name}.",
        "goodbye": "في أمان الله يا {name}، نورتنا.",
        "general": "يا {name}، تفضل — وش أقدر أخدمك فيه؟",
        "unknown_fallback": "ما لقطتها… وضّح أكثر؟",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، بوش تامرني؟",
        "after_optional_name": "تمام يا {name}، بوش تامرني؟",
    },
    "janoubi": {
        "greeting": "هلا يا {name}، بوش تامرني؟",
        "thanks": "يعطيك العافية يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وضّح لي وش تبغاه بالضبط؟",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، بوش تامرني؟",
        "after_optional_name": "تمام يا {name}، بوش تامرني؟",
    },
    "sharqi": {
        "greeting": "هلا يا {name}، كيف أقدر أساعدك؟",
        "thanks": "الله يخليك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "ما فهمت… شلون أقدر أخدمك؟",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، شلون أقدر أساعدك؟",
        "after_optional_name": "تمام يا {name}، شلون أقدر أساعدك؟",
    },
    "shamali": {
        "greeting": "هلا يا {name}، بوش تامرني؟",
        "thanks": "الله يسعدك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وضّح لي أكثر؟",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، بوش تامرني؟",
        "after_optional_name": "تمام يا {name}، بوش تامرني؟",
    },
    "yemeni": {
        "greeting": "أهلاً يا {name}، كيف أقدر أساعدك؟",
        "thanks": "الله يبارك فيك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وضّح لي ايش تبي بالضبط؟",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، كيف أقدر أساعدك؟",
        "after_optional_name": "تمام يا {name}، كيف أقدر أساعدك؟",
    },
    "masri": {
        "greeting": "أهلاً يا {name}، بوش تامرني؟",
        "thanks": "الله يخليك يا {name}.",
        "goodbye": "مع السلامة يا {name}، نورتنا.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "ما فهمت… وضّح أكثر؟",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، بوش تامرني؟",
        "after_optional_name": "تمام يا {name}، بوش تامرني؟",
    },
    "jordani": {
        "greeting": "أهلاً يا {name}، كيف أقدر أساعدك؟",
        "thanks": "الله يعطيك العافية يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "شو بدك بالضبط؟",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
        "collect_name_declined": "تمام، كيف بقدر أساعدك؟",
        "after_optional_name": "تمام يا {name}، كيف بقدر أساعدك؟",
    },
    "iraqi": {
        "greeting": "هلا يا {name}، شلون أقدر أساعدك؟",
        "thanks": "الله يوفقك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وضّح شلون تريد؟",
        "product_fallback": (
            "ما لقينا نفس الطلب حرفياً 🙏\nتبغى نشيّك لك على شي قريب؟"
        ),
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
