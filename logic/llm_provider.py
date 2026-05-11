# -*- coding: utf-8 -*-
"""
llm_provider — طبقة تجريد موحّدة لمزوّدات الذكاء الاصطناعي.
==============================================================================
المزوّدات المدعومة:
    • OpenAI (GPT-4o, GPT-4.1, …)
    • Anthropic (Claude)
    • Google Gemini
    • Mistral AI
    • Groq (Llama / Mixtral سريع جداً)
    • OpenRouter (آلاف النماذج عبر مفتاح واحد)
    • Cohere (Command-R)
    • Manus AI (واجهة متوافقة OpenAI)

اختيار المزوّد من جدول system_settings:
    ai_provider = "openai" | "anthropic" | "gemini" | "mistral" | ...
    ai_model    = اسم النموذج (مثل "gpt-4o" أو "manus-pro")

المفاتيح:
    تُحفظ في system_settings باسم {PROVIDER}_API_KEY (مثل MISTRAL_API_KEY).
    تُستخدم متغيرات البيئة كـ fallback تلقائي.

الواجهة:
    chat(messages, max_tokens, temperature, json_mode) → ChatResult
    is_available(provider) → bool
    get_active_provider() → str
    get_active_model() → str
    list_supported_providers() → list[dict]
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


# المزوّدات المدعومة رسمياً — إذا أضفت مزوّداً جديداً، أضِف الـ adapter في dispatcher أدناه.
SUPPORTED_PROVIDERS = (
    "openai",
    "anthropic",
    "gemini",
    "mistral",
    "cohere",
    "groq",
    "openrouter",
    "manus",
)

# الافتراضات لكل مزوّد (model name)
_DEFAULT_MODELS: Dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
    "gemini": "gemini-1.5-flash",
    "mistral": "mistral-large-latest",
    "cohere": "command-r-plus",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openai/gpt-4o-mini",
    "manus": "manus-pro",
}


def get_active_provider() -> str:
    """المزوّد المختار من لوحة التحكم. الافتراضي: openai."""
    val = _read_setting("ai_provider", "openai").lower().strip()
    if val == "ollama":
        return "openai"
    if val in SUPPORTED_PROVIDERS:
        return val
    return "openai"


def get_active_model() -> str:
    """النموذج المختار من لوحة التحكم. الافتراضي حسب المزوّد."""
    val = _read_setting("ai_model", "").strip()
    if val:
        return val
    return _DEFAULT_MODELS.get(get_active_provider(), "gpt-4o")


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
    d = _DEFAULT_MODELS.get(p, "gpt-4o")
    # Strict validators: openai/anthropic/gemini نعرف أنماط النماذج
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
    # المزوّدات الأخرى: لا نحاكم اسم النموذج — نحفظه كما هو إلا عند الفراغ.
    return m or d


def _resolve_provider_with_fallback(requested: str) -> str:
    """إذا المزوّد المختار غير متاح نجرّب OpenAI ثم بقية المزوّدات."""
    p = (requested or "openai").lower().strip()
    if p == "ollama":
        p = "openai"
    if is_available(p):
        return p
    # Order: نجرّب المزوّدات الشائعة أولاً
    order = ("openai", "anthropic", "gemini", "groq", "mistral", "openrouter", "cohere", "manus")
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


# الأسماء البيئية / مفاتيح system_settings لكل مزوّد
_PROVIDER_API_KEY_VAR: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "manus": "MANUS_API_KEY",
}


def _get_api_key(provider: str) -> str:
    """
    مفتاح API للمزوّد. تُحفظ المفاتيح في لوحة التكاملات داخل system_settings
    بنفس أسماء متغيرات البيئة (OPENAI_API_KEY، ANTHROPIC_API_KEY، …).

    OpenAI: الأولوية لمتغير البيئة OPENAI_API_KEY، ثم لوحة التحكم.
    باقي المزوّدات: قاعدة البيانات ثم البيئة (كما كان).
    """
    env_var = _PROVIDER_API_KEY_VAR.get(provider, "")
    if not env_var:
        return ""
    if provider == "openai":
        cands = _openai_api_key_candidates()
        return cands[0] if cands else ""
    db_val = _read_setting_db_only(env_var).strip()
    if db_val:
        return db_val
    return (os.environ.get(env_var) or "").strip()


# ─── مزوّدات إضافية متوافقة مع OpenAI Chat-Completions ──────────────
#
# Mistral / Groq / OpenRouter / Manus تستخدم جميعاً مخطّط OpenAI نفسه
# (chat/completions JSON)؛ ما يختلف فقط هو Base URL.
_OPENAI_COMPATIBLE_BASE_URL: Dict[str, str] = {
    "mistral": "https://api.mistral.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    # Manus: واجهة متوافقة OpenAI — قابلة للتخصيص عبر MANUS_BASE_URL في system_settings
    "manus": "",
}


def _manus_base_url() -> str:
    """يسمح بتخصيص نقطة منس عبر إعداد MANUS_BASE_URL — وإلا يستخدم Default RestAPI."""
    custom = _read_setting("MANUS_BASE_URL", "").strip().rstrip("/")
    return custom or "https://api.manus.im/v1"


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

    # المزوّدات المتوافقة مع OpenAI HTTP API — يكفي وجود requests + المفتاح.
    if p in ("mistral", "groq", "openrouter", "manus"):
        if not _get_api_key(p):
            return False
        try:
            import requests  # noqa: F401
            return True
        except ImportError:
            return False

    if p == "cohere":
        if not _get_api_key("cohere"):
            return False
        try:
            import requests  # noqa: F401
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


# ─── Adapter عام لمزوّدات OpenAI-Compatible (Mistral / Groq / OpenRouter / Manus) ──

def _chat_openai_compatible(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
    extra_headers: Optional[Dict[str, str]] = None,
) -> ChatResult:
    """استدعاء أي مزوّد متوافق مع OpenAI chat-completions عبر HTTP."""
    try:
        import requests
    except ImportError:
        return ChatResult(
            success=False,
            error="requests library not installed",
            provider=provider,
            model=model,
        )
    if not api_key:
        return ChatResult(
            success=False,
            error=f"{provider.upper()}_API_KEY missing",
            provider=provider,
            model=model,
        )
    url = (base_url or "").strip().rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = requests.post(url, headers=headers, json=body, timeout=120)
        if r.status_code != 200:
            return ChatResult(
                success=False,
                error=(r.text or "")[:380],
                provider=provider,
                model=model,
            )
        data = r.json()
        choices = data.get("choices") or [{}]
        msg = (choices[0].get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        return ChatResult(
            text=str(msg).strip(),
            raw_response=data,
            tokens_used=int(usage.get("total_tokens") or (pt + ct)),
            prompt_tokens=pt,
            completion_tokens=ct,
            provider=provider,
            model=model,
            success=True,
        )
    except Exception as e:
        logger.exception("%s call failed", provider)
        return ChatResult(success=False, error=str(e)[:380], provider=provider, model=model)


def _chat_mistral(messages, model, max_tokens, temperature, json_mode):
    return _chat_openai_compatible(
        provider="mistral",
        base_url=_OPENAI_COMPATIBLE_BASE_URL["mistral"],
        api_key=_get_api_key("mistral"),
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
    )


def _chat_groq(messages, model, max_tokens, temperature, json_mode):
    return _chat_openai_compatible(
        provider="groq",
        base_url=_OPENAI_COMPATIBLE_BASE_URL["groq"],
        api_key=_get_api_key("groq"),
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
    )


def _chat_openrouter(messages, model, max_tokens, temperature, json_mode):
    extras = {
        # تعريف اختياري لتطبيقك في لوحة OpenRouter
        "HTTP-Referer": _read_setting("PUBLIC_BASE_URL", "https://family-mall.com"),
        "X-Title": "Family-Mall AI Console",
    }
    return _chat_openai_compatible(
        provider="openrouter",
        base_url=_OPENAI_COMPATIBLE_BASE_URL["openrouter"],
        api_key=_get_api_key("openrouter"),
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
        extra_headers=extras,
    )


def _chat_manus(messages, model, max_tokens, temperature, json_mode):
    return _chat_openai_compatible(
        provider="manus",
        base_url=_manus_base_url(),
        api_key=_get_api_key("manus"),
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
    )


def _chat_cohere(messages, model, max_tokens, temperature, json_mode):
    """Cohere v2 chat — مختلف عن OpenAI لكنه قريب."""
    try:
        import requests
    except ImportError:
        return ChatResult(success=False, error="requests not installed", provider="cohere", model=model)
    key = _get_api_key("cohere")
    if not key:
        return ChatResult(success=False, error="COHERE_API_KEY missing", provider="cohere", model=model)
    # Cohere chat v2 endpoint
    url = "https://api.cohere.com/v2/chat"
    # تحويل messages → نفس الصيغة المتوقّعة من Cohere
    cohere_msgs = []
    for m in messages:
        role = m.get("role", "user")
        if role == "system":
            cohere_msgs.append({"role": "system", "content": m.get("content", "")})
        elif role == "assistant":
            cohere_msgs.append({"role": "assistant", "content": m.get("content", "")})
        else:
            cohere_msgs.append({"role": "user", "content": m.get("content", "")})
    body = {
        "model": model,
        "messages": cohere_msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=120)
        if r.status_code != 200:
            return ChatResult(success=False, error=(r.text or "")[:380], provider="cohere", model=model)
        data = r.json()
        # Cohere response: {message: {content: [{text: ...}]}}
        msg_obj = data.get("message") or {}
        parts = msg_obj.get("content") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        usage = (data.get("usage") or {}).get("tokens") or {}
        pt = int(usage.get("input_tokens") or 0)
        ct = int(usage.get("output_tokens") or 0)
        return ChatResult(
            text=text.strip(),
            raw_response=data,
            tokens_used=pt + ct,
            prompt_tokens=pt,
            completion_tokens=ct,
            provider="cohere",
            model=model,
            success=True,
        )
    except Exception as e:
        logger.exception("Cohere call failed")
        return ChatResult(success=False, error=str(e)[:380], provider="cohere", model=model)


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
        "mistral": _chat_mistral,
        "groq": _chat_groq,
        "openrouter": _chat_openrouter,
        "manus": _chat_manus,
        "cohere": _chat_cohere,
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
    تُستخدم لعرض الحالة في صفحة التكاملات (Integrations / LLM).
    """
    return [
        {
            "id": "openai",
            "name": "OpenAI (GPT)",
            "available": is_available("openai"),
            "default_model": _DEFAULT_MODELS["openai"],
            "models": ["gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        },
        {
            "id": "anthropic",
            "name": "Anthropic (Claude)",
            "available": is_available("anthropic"),
            "default_model": _DEFAULT_MODELS["anthropic"],
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
            "default_model": _DEFAULT_MODELS["gemini"],
            "models": [
                "gemini-1.5-pro",
                "gemini-1.5-flash",
                "gemini-2.0-flash-exp",
                "gemini-2.5-flash",
                "gemini-2.5-pro",
            ],
        },
        {
            "id": "mistral",
            "name": "Mistral AI",
            "available": is_available("mistral"),
            "default_model": _DEFAULT_MODELS["mistral"],
            "models": [
                "mistral-large-latest",
                "mistral-medium-latest",
                "mistral-small-latest",
                "open-mistral-nemo",
            ],
        },
        {
            "id": "groq",
            "name": "Groq (Llama / Mixtral)",
            "available": is_available("groq"),
            "default_model": _DEFAULT_MODELS["groq"],
            "models": [
                "llama-3.3-70b-versatile",
                "llama-3.1-70b-versatile",
                "llama-3.1-8b-instant",
                "mixtral-8x7b-32768",
                "gemma2-9b-it",
            ],
        },
        {
            "id": "openrouter",
            "name": "OpenRouter (Multi-Model)",
            "available": is_available("openrouter"),
            "default_model": _DEFAULT_MODELS["openrouter"],
            "models": [
                "openai/gpt-4o-mini",
                "openai/gpt-4o",
                "anthropic/claude-3.5-sonnet",
                "google/gemini-pro-1.5",
                "meta-llama/llama-3.3-70b-instruct",
                "qwen/qwen-2.5-72b-instruct",
            ],
        },
        {
            "id": "cohere",
            "name": "Cohere (Command-R)",
            "available": is_available("cohere"),
            "default_model": _DEFAULT_MODELS["cohere"],
            "models": ["command-r-plus", "command-r", "command-r-08-2024"],
        },
        {
            "id": "manus",
            "name": "Manus AI",
            "available": is_available("manus"),
            "default_model": _DEFAULT_MODELS["manus"],
            "models": ["manus-pro", "manus-lite", "manus-coder"],
        },
    ]