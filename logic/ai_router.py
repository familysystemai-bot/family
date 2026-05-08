# -*- coding: utf-8 -*-
"""
موزّع مهام الذكاء الاصطناعي — يقرر متى يستخدم OpenAI ومتى يستخدم Gemini.

توزيع المهام:
┌─────────────────────────────┬──────────────────────────┬────────────────────────────┐
│ المهمة                      │ النموذج                  │ السبب                      │
├─────────────────────────────┼──────────────────────────┼────────────────────────────┤
│ قيادة المحادثة (Intent)     │ OpenAI GPT-4o-mini       │ دقيق في JSON والعربية      │
│ توليد الردود النصية         │ OpenAI GPT-4o-mini       │ أفضل فهم للسياق            │
│ تحويل الصوت (Whisper)       │ OpenAI Whisper           │ الأفضل للعربية             │
│ تحليل الصور من العميل       │ Gemini Flash             │ رخيص + سريع + متعدد الوسائط│
│ استنتاج الفئة من نص         │ قواعد (بدون AI)          │ تكلفة صفر                 │
│ طلب استفسار الفرع           │ بدون AI (منطق أعمال)     │ تكلفة صفر                 │
└─────────────────────────────┴──────────────────────────┴────────────────────────────┘

مبدأ التوفير: الكلمات والسياق أولاً، الذكاء الاصطناعي آخر خيار.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ─── تحليل الصور ───────────────────────────────────────────────────────────

def analyze_customer_image(abs_path: str, ext: str) -> Optional[str]:
    """
    يحلّل صورة أرسلها العميل ويعيد وصفاً نصياً بالعربية.
    يستخدم Gemini Vision (رخيص + متعدد الوسائط).
    يعيد None إذا Gemini غير مفعّل أو فشل التحليل.
    """
    try:
        from logic.gemini_service import analyze_image_for_product
        result = analyze_image_for_product(abs_path, ext)
        if result:
            logger.debug("ai_router: image analyzed via Gemini → %s", result[:80])
        return result
    except Exception:
        logger.exception("ai_router: Gemini image analysis failed")
        return None


# ─── تحويل الصوت ───────────────────────────────────────────────────────────

def transcribe_customer_audio(abs_path: str) -> Optional[str]:
    """
    يحوّل تسجيل صوتي من العميل إلى نص عربي.
    يستخدم OpenAI Whisper (الأفضل للعربية).
    """
    try:
        from logic.attachment_openai import _transcribe_audio
        return _transcribe_audio(abs_path)
    except Exception:
        logger.exception("ai_router: Whisper transcription failed")
        return None


# ─── استنتاج مُنقّح للقصد (نادر الاستخدام) ───────────────────────────────

def run_orchestrator(message: str, context: dict, history: list) -> dict:
    """
    يشغّل منسّق OpenAI للقرار عند الحاجة فقط (غير محدد القصد).
    يعيد خطة JSON أو fallback آمن.
    """
    try:
        from logic.ai_fallback import run_chat_orchestrator_openai
        return run_chat_orchestrator_openai(message, context, history=history)
    except Exception:
        logger.exception("ai_router: orchestrator failed")
        return {"action": "general_response", "message": "", "filters": {}}


# ─── فحص التوفر ────────────────────────────────────────────────────────────

def openai_available() -> bool:
    """هل OpenAI API مفعّل؟"""
    try:
        from logic.llm_provider import is_available

        return bool(is_available("openai"))
    except Exception:
        pass
    try:
        from config import OPENAI_API_KEY

        return bool((OPENAI_API_KEY or "").strip())
    except Exception:
        return False


def gemini_available() -> bool:
    """هل Gemini API مفعّل؟"""
    try:
        from logic.gemini_service import gemini_enabled
        return gemini_enabled()
    except Exception:
        return False


def get_ai_status() -> dict:
    """ملخص حالة الـ AI للتشخيص."""
    return {
        "openai": openai_available(),
        "gemini": gemini_available(),
        "image_analysis": gemini_available(),   # Gemini للصور
        "audio_transcription": openai_available(),  # Whisper للصوت
        "chat_orchestration": openai_available(),   # GPT للنصوص
    }
