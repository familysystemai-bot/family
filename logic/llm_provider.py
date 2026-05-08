# -*- coding: utf-8 -*-
"""
llm_provider — طبقة تجريد للمزوّدات (OpenAI / Anthropic / Gemini).
==============================================================================

الغرض:
    توفير واجهة موحّدة لجميع استدعاءات LLM بحيث:
    - يمكن للمؤسس اختيار المزوّد ونموذجه من لوحة التحكم
    - تكيّف الكود تلقائياً مع كل مزوّد
    - تتبّع التكلفة (tokens) لكل مزوّد على حدة
    - مرن للإضافة المستقبلية

كيفية اختيار المزوّد:
    من جدول system_settings:
        ai_provider = "openai" | "anthropic" | "gemini"
        ai_model    = "gpt-4o" | "claude-3-5-sonnet-20241022" | "gemini-1.5-flash"

    أو من متغيرات البيئة (fallback):
        AI_PROVIDER, AI_MODEL, OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY

ملاحظة:
    في الدفعة 5، المفاتيح تُقرأ من Secrets Vault بدل المتغيرات البيئية مباشرة.
    حالياً نقرأها من os.environ كـ fallback.

الواجهة:
    chat(messages, max_tokens, temperature, json_mode) → ChatResult
    is_available(provider) → bool
    get_active_provider() → "openai" | ... | None
    get_active_model() → str
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── أنواع البيانات ────────────────────────────────────────────────────

@dataclass
class ChatResult:
    """نتيجة استدعاء LLM موحّدة لكل المزوّدات."""
    text: str = ""
    raw_response: Any = None
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    provider: str = ""
    model: str = ""
    success: bool = True
    error: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success and bool((self.text or "").strip())


# ─── قراءة الإعدادات ──────────────────────────────────────────────────

def _read_setting(key: str, default: str = "") -> str:
    """يقرأ إعداداً من قاعدة البيانات (system_settings) ثم من البيئة."""
    db_val = _read_setting_db_only(key)
    if db_val:
        return db_val
    env_key = key.upper()
    val = (os.environ.get(env_key) or "").strip()
    return val or default


def _read_setting_db_only(key: str) -> str:
    """قيمة المفتاح من system_settings فقط (لوحة التحكم) — بلا قراءة من البيئة."""
    try:
        from logic.db_adapter import DBAdapter

        db = DBAdapter()
        row = db.fetch_one(
            "SELECT value FROM system_settings WHERE key = %s",
            (key,),
        )
        if row and (row.get("value") or "").strip():
            return str(row["value"]).strip()
    except Exception:
        pass
    return ""


def _openai_api_key_candidates() -> List[str]:
    """
    ترتيب مفاتيح OpenAI: متغير البيئة أولاً، ثم المفتاح المسجّل في لوحة التحكم.
    بدون تكرار. يُستخدم للاستدعاء مع إعادة المحاولة عند فشل المفتاح الأساسي.
    """
    env_k = (os.environ.get("OPENAI_API_KEY") or "").strip()
    db_k = _read_setting_db_only("OPENAI_API_KEY").strip()
    out: List[str] = []
    if env_k:
        out.append(env_k)
    if db_k and db_k not in out:
        out.append(db_k)
    return out


def get_active_provider() -> str:
    """المزوّد المختار من لوحة التحكم. الافتراضي: openai."""
    val = _read_setting("ai_provider", "openai").lower().strip()
    if val == "ollama":
        return "openai"
    if val in ("openai", "anthropic", "gemini"):
        return val
    return "openai"


def get_active_model() -> str:
    """النموذج المختار من لوحة التحكم. الافتراضي حسب المزوّد."""
    val = _read_setting("ai_model", "").strip()
    if val:
        return val
    # افتراضات لكل مزوّد
    defaults = {
        "openai": "gpt-4o",
        "anthropic": "claude-3-5-sonnet-20241022",
        "gemini": "gemini-1.5-flash",
    }
    return defaults.get(get_active_provider(), "gpt-4o")


def _looks_like_openai_model(model: str) -> bool:
    low = (model or "").strip().lower()
    if not low:
        return False
    return low.startswith("gpt-") or low.startswith("o1") or low.startswith("o3") or low.startswith("ft:")


def _looks_like_gemini_model(model: str) -> bool:
    return "gemini" in (model or "").lower()


def _looks_like_claude_model(model: str) -> bool:
    low = (model or "").strip().lower()
    return low.startswith("claude")


def _normalize_model_for_provider(provider: str, model: str) -> str:
    """
    يمنع إرسال اسم نموذج مزوّد آخر (مثل gpt-4o لـ Gemini أو llama لـ OpenAI)
    — سبب شائع لفشل كل استدعاءات المنسّق.
    """
    p = (provider or "").lower().strip()
    m = (model or "").strip()
    defaults = {
        "openai": "gpt-4o",
        "anthropic": "claude-3-5-sonnet-20241022",
        "gemini": "gemini-1.5-flash",
    }
    d = defaults.get(p, "gpt-4o")
    if p == "openai":
        if not m or not _looks_like_openai_model(m):
            if m and m != d:
                logger.warning(
                    "llm_provider: نموذج «%s» لا يصلح لـ OpenAI — استخدام %s",
                    m,
                    d,
                )
            return d
        return m
    if p == "anthropic":
        if not m or not _looks_like_claude_model(m):
            if m and m != d:
                logger.warning(
                    "llm_provider: نموذج «%s» لا يصلح لـ Anthropic — استخدام %s",
                    m,
                    d,
                )
            return d
        return m
    if p == "gemini":
        if not m or not _looks_like_gemini_model(m):
            if m and m != d:
                logger.warning(
                    "llm_provider: نموذج «%s» لا يصلح لـ Gemini — استخدام %s",
                    m,
                    d,
                )
            return d
        return m
    return m or d


def _resolve_provider_with_fallback(requested: str) -> str:
    """إذا المزوّد المختار غير متاح نجرّب OpenAI ثم بقية المزوّدات."""
    p = (requested or "openai").lower().strip()
    if p == "ollama":
        p = "openai"
    if is_available(p):
        return p
    order = ("openai", "anthropic", "gemini")
    for alt in order:
        if alt != p and is_available(alt):
            logger.warning(
                "llm_provider: المزوّد المختار «%s» غير متاح — التبديل إلى «%s»",
                p,
                alt,
            )
            return alt
    return p


def openai_api_key_candidates() -> List[str]:
    """قائمة مفاتيح OpenAI بالترتيب: البيئة ثم لوحة التحكم (للاستدعاء مع إعادة المحاولة)."""
    return _openai_api_key_candidates()


def _get_api_key(provider: str) -> str:
    """
    مفتاح API للمزوّد. تُحفظ المفاتيح في لوحة التكاملات داخل system_settings
    بنفس أسماء متغيرات البيئة (OPENAI_API_KEY، ANTHROPIC_API_KEY، …).

    OpenAI: الأولوية لمتغير البيئة OPENAI_API_KEY، ثم لوحة التحكم.
    باقي المزوّدات: قاعدة البيانات ثم البيئة (كما كان).
    """
    keys_env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    env_var = keys_env.get(provider, "")
    if not env_var:
        return ""
    if provider == "openai":
        cands = _openai_api_key_candidates()
        return cands[0] if cands else ""
    db_val = _read_setting_db_only(env_var).strip()
    if db_val:
        return db_val
    return (os.environ.get(env_var) or "").strip()


def get_provider_api_key(provider: str) -> str:
    """مفتاح المزوّد (للاستدعاء من وحدات أخرى دون الاعتماد على os.environ فقط)."""
    return _get_api_key(provider)


def get_openai_api_key() -> str:
    """مفتاح OpenAI: البيئة أولاً، ثم لوحة التكاملات."""
    return _get_api_key("openai")


def is_available(provider: Optional[str] = None) -> bool:
    """هل المزوّد المعطى متاح للاستخدام (مفاتيح موجودة + مكتبة مثبتة)؟"""
    p = (provider or get_active_provider()).lower()

    if p == "openai":
        if not _openai_api_key_candidates():
            return False
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False

    if p == "anthropic":
        if not _get_api_key("anthropic"):
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except ImportError:
            return False

    if p == "gemini":
        if not _get_api_key("gemini"):
            return False
        try:
            import google.generativeai  # noqa: F401
            return True
        except ImportError:
            return False

    return False


# ─── Adapters لكل مزوّد ──────────────────────────────────────────────

def _chat_openai(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
) -> ChatResult:
    """استدعاء OpenAI API."""
    try:
        from openai import OpenAI
    except ImportError:
        return ChatResult(success=False, error="openai library not installed", provider="openai", model=model)

    keys_to_try = _openai_api_key_candidates()
    if not keys_to_try:
        return ChatResult(success=False, error="OPENAI_API_KEY missing", provider="openai", model=model)

    last_err = ""
    for i, key in enumerate(keys_to_try):
        try:
            client = OpenAI(api_key=key)
            kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**kwargs)
            text = (response.choices[0].message.content or "").strip()
            # tokens
            tokens = 0
            pt = 0
            ct = 0
            try:
                u = response.usage
                if u is not None:
                    tokens = int(u.total_tokens or 0)
                    pt = int(getattr(u, "prompt_tokens", 0) or 0)
                    ct = int(getattr(u, "completion_tokens", 0) or 0)
            except Exception:
                pass
            return ChatResult(
                text=text,
                raw_response=response,
                tokens_used=tokens,
                prompt_tokens=pt,
                completion_tokens=ct,
                provider="openai",
                model=model,
                success=True,
            )
        except Exception as e:
            last_err = str(e)
            if i + 1 < len(keys_to_try):
                logger.warning(
                    "OpenAI call failed with primary key; retrying with dashboard key: %s",
                    e,
                )
            else:
                logger.exception("OpenAI call failed")
    return ChatResult(
        success=False, error=last_err or "OpenAI call failed", provider="openai", model=model
    )


def _chat_anthropic(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
) -> ChatResult:
    """استدعاء Anthropic Claude API."""
    try:
        import anthropic
    except ImportError:
        return ChatResult(success=False, error="anthropic library not installed", provider="anthropic", model=model)

    key = _get_api_key("anthropic")
    if not key:
        return ChatResult(success=False, error="ANTHROPIC_API_KEY missing", provider="anthropic", model=model)

    # Anthropic يفصل system message في حقل منفصل
    system_msg = ""
    user_msgs: List[Dict[str, str]] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            system_msg += (content + "\n").strip()
        elif role in ("user", "assistant"):
            user_msgs.append({"role": role, "content": content})

    # Anthropic لا يدعم json_mode رسمياً — نحقنه في system
    if json_mode:
        system_msg = (system_msg or "") + "\n\nرد بصيغة JSON صالح فقط بدون أي شرح إضافي."

    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_msg.strip(),
            messages=user_msgs,
        )
        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        text = "\n".join(text_blocks).strip()
        inp_t = out_t = 0
        tokens = 0
        try:
            inp_t = int(response.usage.input_tokens or 0)
            out_t = int(response.usage.output_tokens or 0)
            tokens = inp_t + out_t
        except Exception:
            pass
        return ChatResult(
            text=text,
            raw_response=response,
            tokens_used=tokens,
            prompt_tokens=inp_t,
            completion_tokens=out_t,
            provider="anthropic",
            model=model,
            success=True,
        )
    except Exception as e:
        logger.exception("Anthropic call failed")
        return ChatResult(success=False, error=str(e), provider="anthropic", model=model)


def _chat_gemini(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
) -> ChatResult:
    """استدعاء Google Gemini API."""
    try:
        import google.generativeai as genai
    except ImportError:
        return ChatResult(success=False, error="google-generativeai library not installed", provider="gemini", model=model)

    key = _get_api_key("gemini")
    if not key:
        return ChatResult(success=False, error="GEMINI_API_KEY missing", provider="gemini", model=model)

    # Gemini يدمج system في user message
    system_msg = ""
    chat_history: List[Dict[str, Any]] = []
    user_text = ""
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            system_msg += (content + "\n").strip()
        elif role == "user":
            user_text = content
            chat_history.append({"role": "user", "parts": [content]})
        elif role == "assistant":
            chat_history.append({"role": "model", "parts": [content]})

    # دمج system في user message
    full_user = (system_msg + "\n\n" + user_text).strip() if system_msg else user_text
    if not full_user:
        return ChatResult(success=False, error="empty user message", provider="gemini", model=model)

    try:
        genai.configure(api_key=key)
        gen_config: Dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            gen_config["response_mime_type"] = "application/json"
        gm = genai.GenerativeModel(model)
        response = gm.generate_content(full_user, generation_config=gen_config)
        text = (response.text or "").strip()
        tokens = 0
        pt = 0
        ct = 0
        try:
            um = response.usage_metadata
            if um is not None:
                tokens = int(getattr(um, "total_token_count", 0) or 0)
                pt = int(getattr(um, "prompt_token_count", 0) or 0)
                ct = int(getattr(um, "candidates_token_count", 0) or 0)
                if not tokens and (pt or ct):
                    tokens = pt + ct
        except Exception:
            pass
        return ChatResult(
            text=text,
            raw_response=response,
            tokens_used=tokens,
            prompt_tokens=pt,
            completion_tokens=ct,
            provider="gemini",
            model=model,
            success=True,
        )
    except Exception as e:
        logger.exception("Gemini call failed")
        return ChatResult(success=False, error=str(e), provider="gemini", model=model)


# ─── الواجهة الرئيسية ─────────────────────────────────────────────────

def chat(
    messages: List[Dict[str, str]],
    max_tokens: int = 500,
    temperature: float = 0.3,
    json_mode: bool = False,
    *,
    intent_label: str = "unknown",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    track_usage: bool = True,
) -> ChatResult:
    """
    استدعاء LLM موحّد.

    المعاملات:
        messages: قائمة [{"role": "system|user|assistant", "content": "..."}]
        max_tokens: الحد الأقصى للتوكنز المُولَّدة
        temperature: درجة العشوائية (0.0 = حتمي، 1.0 = إبداعي)
        json_mode: لو True، نطلب من المزوّد JSON صالح
        intent_label: للتسجيل في analytics (سبب الاستدعاء)
        provider: لتجاوز المزوّد المختار (للاختبار)
        model: لتجاوز النموذج المختار (للاختبار)
        track_usage: لو True، يسجّل في ai_usage_tracker

    يُرجع: ChatResult.
        لو ChatResult.success == False → استخدم النص الافتراضي/fallback.
    """
    p = _resolve_provider_with_fallback((provider or get_active_provider()).lower())
    m = _normalize_model_for_provider(p, (model or get_active_model()).strip())

    # Dispatcher
    fn = {
        "openai": _chat_openai,
        "anthropic": _chat_anthropic,
        "gemini": _chat_gemini,
    }.get(p)
    if fn is None:
        return ChatResult(success=False, error=f"unknown provider: {p}", provider=p, model=m)

    result = fn(messages, m, max_tokens, temperature, json_mode)

    # تسجيل الاستخدام
    if track_usage:
        try:
            from logic.ai_usage_tracker import track_llm_call
            track_llm_call(
                provider=result.provider or p,
                model=result.model or m,
                tokens=result.tokens_used,
                intent=intent_label,
                prompt_tokens=int(getattr(result, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(result, "completion_tokens", 0) or 0),
            )
        except Exception:
            pass

    return result


def list_supported_providers() -> List[Dict[str, Any]]:
    """
    للوحة المؤسس: قائمة المزوّدات وحالة توفر كل واحد.
    """
    return [
        {
            "id": "openai",
            "name": "OpenAI (GPT)",
            "available": is_available("openai"),
            "default_model": "gpt-4o",
            "models": ["gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        },
        {
            "id": "anthropic",
            "name": "Anthropic (Claude)",
            "available": is_available("anthropic"),
            "default_model": "claude-3-5-sonnet-20241022",
            "models": [
                "claude-opus-4-5",
                "claude-sonnet-4-5",
                "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022",
            ],
        },
        {
            "id": "gemini",
            "name": "Google Gemini",
            "available": is_available("gemini"),
            "default_model": "gemini-1.5-flash",
            "models": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash-exp"],
        },
    ]