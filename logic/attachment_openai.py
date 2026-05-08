# -*- coding: utf-8 -*-
"""
استخراج نص من مرفقات الشات عبر OpenAI: Whisper (صوت) ورؤية (صورة).
يُعطّل بالكامل بدون OPENAI_API_KEY أو عند OPENAI_ATTACHMENTS=false.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional, Union

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


def _resolved_openai_key() -> str:
    try:
        from logic.llm_provider import get_openai_api_key

        k = (get_openai_api_key() or "").strip()
        if k:
            return k
    except Exception:
        pass
    return (OPENAI_API_KEY or "").strip()


def attachments_ai_enabled() -> bool:
    if not OPENAI_ATTACHMENTS_ENABLED:
        return False
    return bool(_resolved_openai_key())


def text_from_saved_file(abs_path: str, ext: str) -> Optional[Union[str, dict]]:
    """
    يعيد نصاً عربياً مستخرجاً من الملف، أو dict fallback للصوت، أو None عند التعطيل/الفشل. # FIXED

    توزيع المهام:
    - الصور  → Gemini Flash (رخيص + متعدد الوسائط) — Fallback: OpenAI Vision
    - الصوت  → OpenAI Whisper (الأفضل للعربية)
    """
    ext_clean = (ext or "").lower().strip(".")

    if ext_clean in _IMAGE_MIME:
        # أولوية: Gemini للصور (أرخص وأسرع)
        gemini_result = _describe_image_gemini(abs_path, ext_clean)
        if gemini_result:
            return gemini_result
        # Fallback: OpenAI Vision إذا Gemini غير متاح
        if attachments_ai_enabled():
            return _describe_image(abs_path, ext_clean)
        return None

    if ext_clean in {"webm", "wav", "mp3", "ogg", "m4a"}:
        # الصوت → OpenAI Whisper دائماً (الأفضل للعربية)
        if attachments_ai_enabled():
            tr = _transcribe_audio(abs_path)
            if tr:
                return tr
        return {  # FIXED
            "fallback": True,  # FIXED
            "message": "وصلتني رسالتك الصوتية 🎤 ممكن تكتب طلبك نصاً؟",  # FIXED
        }  # FIXED

    return None


def _describe_image_gemini(abs_path: str, ext: str) -> Optional[str]:
    """
    يحلّل الصورة عبر Gemini Flash (أرخص بكثير من OpenAI Vision).
    يعيد None إذا Gemini غير مفعّل أو فشل.
    """
    try:
        from logic.gemini_service import analyze_image_for_product
        return analyze_image_for_product(abs_path, ext)
    except Exception:
        logger.debug("attachment: Gemini image analysis unavailable, will try OpenAI fallback")
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
    try:
        from logic.llm_provider import openai_api_key_candidates
    except Exception:
        openai_api_key_candidates = None  # type: ignore
    keys = []
    if openai_api_key_candidates:
        try:
            keys = openai_api_key_candidates() or []
        except Exception:
            keys = []
    if not keys:
        k = _resolved_openai_key()
        keys = [k] if k else []
    if not keys:
        return None
    model = (OPENAI_WHISPER_MODEL or "whisper-1").strip() or "whisper-1"
    for idx, key in enumerate(keys):
        if not key:
            continue
        try:
            client = OpenAI(api_key=key)
            with open(abs_path, "rb") as audio_f:
                tr = client.audio.transcriptions.create(
                    model=model,
                    file=audio_f,
                )
            text = (getattr(tr, "text", None) or "").strip()
            if text:
                return text
        except Exception:
            if idx + 1 < len(keys):
                logger.warning("attachment_openai: Whisper failed with primary key; retrying")
            else:
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
    try:
        from logic.llm_provider import openai_api_key_candidates
    except Exception:
        openai_api_key_candidates = None  # type: ignore
    keys = []
    if openai_api_key_candidates:
        try:
            keys = openai_api_key_candidates() or []
        except Exception:
            keys = []
    if not keys:
        k = _resolved_openai_key()
        keys = [k] if k else []
    if not keys:
        return None
    model = (OPENAI_VISION_MODEL or "gpt-4o").strip() or "gpt-4o"
    prompt = (
        "أعد صياغة ما يهم العميل في هذه الصورة كجملة أو جملتين بالعربية، "
        "كأنه يطلب شيئاً في متجر ملابس. لا تخترع منتجات غير ظاهرة. "
        "في سطر جديد اكتب بالضبط: SEARCH: ثم 4-10 كلمات مفتاحية بالعربية للبحث في المتجر. "
        "إن لم يكن هناك معنى واضح للطلب قل فقط: وضّح طلبك بالنص."
    )
    for idx, key in enumerate(keys):
        if not key:
            continue
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
            if text:
                return text
        except Exception:
            if idx + 1 < len(keys):
                logger.warning("attachment_openai: vision failed with primary key; retrying")
            else:
                logger.exception("attachment_openai: vision description failed")
    return None
