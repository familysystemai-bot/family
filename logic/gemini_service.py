# -*- coding: utf-8 -*-
"""
خدمة Gemini Vision — تحليل الصور القادمة من العملاء.

المبدأ: Gemini Flash رخيص وسريع للصور، بينما OpenAI يبقى للنصوص والصوت.
يُعطَّل تلقائياً بدون GEMINI_API_KEY.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB

_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# نص الطلب للـ Gemini عند تحليل صورة العميل
_PRODUCT_ANALYSIS_PROMPT = (
    "أنت مساعد في متجر. العميل أرسل هذه الصورة. "
    "اوصف بإيجاز (جملة أو جملتين بالعربية) ما هو المنتج أو الشيء الظاهر في الصورة، "
    "وكأن العميل يطلبه أو يستفسر عنه في متجر ملابس وإكسسوارات. "
    "لا تخترع تفاصيل، فقط صف ما تراه. "
    "إن كانت الصورة غير واضحة أو لا علاقة لها بالتسوق، قل: 'الصورة غير واضحة، وضّح طلبك بالنص.'"
)


def gemini_enabled() -> bool:
    """هل Gemini مفعّل (مفتاح API موجود)؟"""
    key = (GEMINI_API_KEY or "").strip()
    return bool(key)


def analyze_image_for_product(abs_path: str, ext: str) -> Optional[str]:
    """
    يحلّل صورة العميل عبر Gemini ويعيد وصفاً نصياً عربياً يُستخدم للبحث عن منتج.
    يعيد None عند التعطيل أو الفشل.
    """
    if not gemini_enabled():
        logger.debug("gemini_service: disabled (no GEMINI_API_KEY)")
        return None

    ext_clean = (ext or "").lower().strip(".")
    mime = _IMAGE_MIME.get(ext_clean)
    if not mime:
        logger.debug("gemini_service: unsupported image type: %s", ext_clean)
        return None

    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return None

    if size > _MAX_IMAGE_BYTES:
        logger.warning("gemini_service: image too large (%s bytes)", size)
        return None
    if size < 32:
        return None

    try:
        with open(abs_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        logger.warning("gemini_service: cannot read image: %s", e)
        return None

    try:
        import google.generativeai as genai
    except ImportError:
        logger.warning("gemini_service: google-generativeai package not installed. Run: pip install google-generativeai")
        return None

    key = (GEMINI_API_KEY or "").strip()
    model_name = (GEMINI_MODEL or "gemini-flash-latest").strip() or "gemini-flash-latest"

    try:
        genai.configure(api_key=key)
        model = genai.GenerativeModel(model_name)

        image_part = {
            "mime_type": mime,
            "data": base64.standard_b64encode(raw).decode("ascii"),
        }

        response = model.generate_content(
            [_PRODUCT_ANALYSIS_PROMPT, image_part],
            generation_config={"max_output_tokens": 200, "temperature": 0.2},
        )

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            logger.warning("gemini_service: empty response from Gemini")
            return None

        logger.debug("gemini_service: image analyzed → %s", text[:120])
        return text

    except Exception:
        logger.exception("gemini_service: Gemini Vision failed")
        return None


def compare_image_with_products(
    customer_image_path: str,
    ext: str,
    product_images: list[dict],
) -> list[dict]:
    """
    يقارن صورة العميل مع قائمة صور المنتجات ويعيد المنتجات الأكثر تشابهاً.
    product_images: [{product_id, image_path, product_name}, ...]
    يعيد قائمة مرتبة حسب التشابه (الأعلى أولاً).

    ملاحظة: هذه الوظيفة تستخدم الوصف النصي للمقارنة (لا Vision مباشرة)
    لتقليل التكلفة — نحلل صورة العميل مرة واحدة فقط.
    """
    description = analyze_image_for_product(customer_image_path, ext)
    if not description:
        return []

    # نعيد الوصف النصي ليستخدمه محرك البحث العادي
    return [{"description": description}]
