# -*- coding: utf-8 -*-
"""
قوالب ردود الشات حسب اللهجة — بدون AI.
المفتاح "default": لهجة سعودية عامة (fallback).
"""
from __future__ import annotations

from typing import Any, Dict

# كل مفتاح لهجة يحوي نفس المفاتيح؛ القيم تُنسَّق بـ .format عند الحاجة (مثل {name}).
# ملاحظة: unknown_fallback و product_fallback تؤدي إلى التصعيد للفرع — لا كلام فاضي.
RESPONSES: Dict[str, Dict[str, str]] = {
    "default": {
        "campaign_opening": "هلا يا {name} 👋",
        "campaign_product_teaser": "المنتج اللي سألت عنه ({product}) صار متوفر 🔥",
        "greeting": "هلا يا {name}، تفضل.",
        "thanks": "الله يسعدك يا {name}، واجبنا.",
        "goodbye": "مع السلامة يا {name}، وشرفتنا.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وش أقدر أخدمك؟",
        "server_error": "آسفين، حصل خلل بسيط. جرّب مرة ثانية.",
        "product_found_soft": "يا {name}، نعم متوفر 👍 وش تبي بالضبط؟",
        "product_search_intro": "يا {name}، هذا اللي عندنا 👇",
        "product_available_ack": "نعم متوفر 👍\nتبغى موديل معين؟",
        "product_found_soft_more": "إذا حاب تشوف أكثر قلّي «غيره» أو «ورّني زيادة».",
        "product_branch_prompt": "يا {name}، أي فرع أقرب لك؟ متوفر عندنا في:",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "hijazi": {
        "greeting": "أهلين يا {name}، تفضل.",
        "thanks": "الله يحييك يا {name}، خدمتك أولى.",
        "goodbye": "مع السلامة يا {name}، بالتوفيق.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وش تحتاج؟",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "najdi": {
        "campaign_opening": "هلا والله يا {name} 👋",
        "campaign_product_teaser": "المنتج اللي سألت عنه ({product}) توفّر 🔥",
        "greeting": "هلا والله يا {name}، تفضل.",
        "thanks": "الله يعطيك العافية يا {name}.",
        "goodbye": "في أمان الله يا {name}، نورتنا.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وش تحتاج؟",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "janoubi": {
        "greeting": "هلا يا {name}، تفضل.",
        "thanks": "يعطيك العافية يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وش تحتاج؟",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "sharqi": {
        "greeting": "هلا يا {name}، تفضل.",
        "thanks": "الله يخليك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "تفضل.",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "shamali": {
        "greeting": "هلا يا {name}، تفضل.",
        "thanks": "الله يسعدك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "وش تحتاج؟",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "yemeni": {
        "greeting": "أهلاً يا {name}، تفضل.",
        "thanks": "الله يبارك فيك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "تفضل.",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "masri": {
        "greeting": "أهلاً يا {name}، تفضل.",
        "thanks": "الله يخليك يا {name}.",
        "goodbye": "مع السلامة يا {name}، نورتنا.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "إيه اللي تحتاجه؟",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "jordani": {
        "greeting": "أهلاً يا {name}، تفضل.",
        "thanks": "الله يعطيك العافية يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "شو بدك؟",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
    },
    "iraqi": {
        "greeting": "هلا يا {name}، تفضل.",
        "thanks": "الله يوفقك يا {name}.",
        "goodbye": "مع السلامة يا {name}.",
        "general": "يا {name}، تفضل — وش تحتاج؟",
        "unknown_fallback": "تفضل.",
        "product_fallback": "ما لقيت هذا المنتج حالياً، تبغى شيء ثاني؟",
        "collect_name_declined": "تمام، تفضل.",
        "after_optional_name": "تمام يا {name}، تفضل.",
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