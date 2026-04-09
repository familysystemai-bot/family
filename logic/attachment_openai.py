# -*- coding: utf-8 -*-
"""
استخراج نص من مرفقات الشات عبر OpenAI: Whisper (صوت) ورؤية (صورة).
يُعطّل بالكامل بدون OPENAI_API_KEY أو عند OPENAI_ATTACHMENTS=false.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from config import (
    OPENAI_API_KEY,
    OPENAI_ATTACHMENTS_ENABLED,
    OPENAI_VISION_MODEL,
    OPENAI_WHISPER_MODEL,
)

logger = logging.getLogger(__name__)

_MAX_WHISPER_BYTES = 24 * 1024 * 1024
_MAX_VISION_BYTES = 8 * 1024 * 1024

_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def attachments_ai_enabled() -> bool:
    if not OPENAI_ATTACHMENTS_ENABLED:
        return False
    return bool((OPENAI_API_KEY or "").strip())


def text_from_saved_file(abs_path: str, ext: str) -> Optional[str]:
    """
    يعيد نصاً عربياً مستخرجاً من الملف، أو None عند التعطيل/الفشل.
    """
    if not attachments_ai_enabled():
        return None
    ext = (ext or "").lower().strip(".")
    if ext in _IMAGE_MIME:
        return _describe_image(abs_path, ext)
    if ext in {"webm", "wav", "mp3", "ogg", "m4a"}:
        return _transcribe_audio(abs_path)
    return None


def _transcribe_audio(abs_path: str) -> Optional[str]:
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return None
    if size > _MAX_WHISPER_BYTES:
        logger.warning("attachment_openai: audio file too large for Whisper")
        return None
    if size < 80:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("attachment_openai: openai package not installed")
        return None
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    model = (OPENAI_WHISPER_MODEL or "whisper-1").strip() or "whisper-1"
    try:
        client = OpenAI(api_key=key)
        with open(abs_path, "rb") as audio_f:
            tr = client.audio.transcriptions.create(
                model=model,
                file=audio_f,
            )
        text = (getattr(tr, "text", None) or "").strip()
        return text or None
    except Exception:
        logger.exception("attachment_openai: Whisper transcription failed")
        return None


def _describe_image(abs_path: str, ext: str) -> Optional[str]:
    mime = _IMAGE_MIME.get(ext)
    if not mime:
        return None
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return None
    if size > _MAX_VISION_BYTES:
        logger.warning("attachment_openai: image too large for vision API")
        return None
    if size < 32:
        return None
    try:
        with open(abs_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    b64 = base64.standard_b64encode(raw).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("attachment_openai: openai package not installed")
        return None
    key = (OPENAI_API_KEY or "").strip()
    if not key:
        return None
    model = (OPENAI_VISION_MODEL or "gpt-4o-mini").strip() or "gpt-4o-mini"
    prompt = (
        "أعد صياغة ما يهم العميل في هذه الصورة كجملة أو جملتين بالعربية، "
        "كأنه يطلب شيئاً في متجر ملابس. لا تخترع منتجات غير ظاهرة. "
        "إن لم يكن هناك معنى واضح للطلب قل عبارة قصيرة: وضّح طلبك بالنص."
    )
    try:
        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=220,
            temperature=0.2,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                }
            ],
        )
        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        return text or None
    except Exception:
        logger.exception("attachment_openai: vision description failed")
        return None
