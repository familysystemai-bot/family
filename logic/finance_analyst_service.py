# -*- coding: utf-8 -*-
"""
محلل مالي ذكي — يفهم سياق المنصّة الكاملة ويستجيب لأسئلة المسؤول الحرّة.
================================================================
يستقبل لوحة KPIs الديناميكية + بيانات الفروع + الذروات + المرتجعات،
ثم يصيغ سياقاً مكثّفاً للـ LLM ليُجيب على أي سؤال:
    - "أيُّ فرع حقق أكثر مبيعات هذا الشهر؟"
    - "متى تكون ذروة المبيعات في فرع جدة؟"
    - "ما نسبة المرتجعات؟ وأي فرع أعلى استرجاعاً؟"
    - "اقترح خطة تحسين لرفع الهامش."

يدعم: OpenAI, Anthropic, Gemini, Mistral, Groq, OpenRouter, Cohere, Manus.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_AR = """أنت «مانوس» — كبير المحللين الماليين لمؤسسة «مجمع العائلة».
دورك أن تساعد المسؤول التنفيذي على فهم أداء الفروع واتخاذ قرارات سريعة وذكية.

قواعد الردّ:
• أجِب بالعربية الفصحى الموجزة، نبرة استشارية احترافية.
• استند إلى الأرقام المرفقة في JSON فقط — لا تخترع أرقاماً.
• إذا كانت البيانات داخلية تقديرية (mode=internal_fallback) صرّح بذلك بلطف.
• لكل توصية اذكر السبب من الأرقام (مثلاً: «لأن نسبة الاسترجاع 8.2%»).
• استخدم التنسيق التالي عندما يناسب: عناوين فرعية قصيرة + نقاط مختصرة.
• إذا سُئلت عن مقارنة شهر بشهر استعمل mom_comparison.
• إذا سُئلت عن ساعات الذروة استعمل branch_peak_hours.
• إذا سُئلت عن الاسترجاع استعمل returns_signal.
• لا تكشف عن مفاتيح API أو أسرار التكامل أبداً.
• تجنّب التحذيرات القانونية الطويلة؛ كن عملياً ومركّزاً على الأرقام."""


def build_financial_context_payload(db: Any, dashboard: Dict[str, Any]) -> str:
    """يبني سياقاً متماسكاً للمحلل — يدمج لوحة المؤشرات + مؤشرات داخلية إضافية."""
    extra: Dict[str, Any] = {}
    try:
        extra["products_total"] = int(db.count_products_total())
    except Exception:
        extra["products_total"] = None
    try:
        extra["pending_inquiries"] = len(db.get_all_pending_inquiries(limit=200) or [])
    except Exception:
        extra["pending_inquiries"] = None
    try:
        # تاريخ بيانات الواتساب للمؤسس — يفيد المحلل في فهم حجم التفاعل
        from logic.ai_usage_tracker import get_founder_accounting

        acct = get_founder_accounting() or {}
        extra["wa_messages_30d"] = {
            "inbound": int(acct.get("wa_messages_inbound") or 0),
            "outbound": int(acct.get("wa_messages_outbound") or 0),
        }
        # نجمع توكنز LLM من كل القنوات (web / wa / system)
        total_tokens = 0
        for ch in ("llm_web", "llm_wa", "llm_system"):
            ch_data = acct.get(ch) or {}
            try:
                total_tokens += int(ch_data.get("total_tokens") or 0)
            except (TypeError, ValueError):
                pass
        extra["llm_tokens_30d"] = total_tokens
    except Exception:
        extra["wa_messages_30d"] = None
        extra["llm_tokens_30d"] = None

    envelope = {
        "brand": "مجمع العائلة",
        "dashboard_metrics": dashboard,
        "internal_signals": extra,
    }
    # نقطع الـ JSON إلى حجم مناسب لتجنب تجاوز التوكنز
    raw = json.dumps(envelope, ensure_ascii=False, default=str)
    return raw[:16000]


def _build_chat_messages(payload_text: str, user_question: str) -> list:
    q = (user_question or "").strip()
    if len(q) > 3500:
        q = q[:3490] + "…"
    user_block = (
        "السياق المالي والتشغيلي الحالي (JSON):\n"
        + payload_text
        + "\n---\nسؤال التنفيذي:\n"
        + q
    )
    return [
        {"role": "system", "content": SYSTEM_AR},
        {"role": "user", "content": user_block},
    ]


def run_financial_llm(
    *,
    payload_text: str,
    user_question: str,
    provider: str,
    api_key: str,
    model: str,
) -> Tuple[Optional[str], Optional[str]]:
    """يعيد (نص الرد أو None، رسالة الخطأ أو None).

    يستخدم منظومة llm_provider الموحّدة بحيث يعمل تلقائياً مع
    OpenAI / Anthropic / Gemini / Mistral / Groq / OpenRouter / Cohere / Manus.
    """
    prov = (provider or "openai").strip().lower()
    md = (model or "").strip()

    # نستخدم الطبقة الموحّدة — تستعيد المفاتيح من system_settings تلقائياً.
    # ملاحظة: مفتاح المؤسس الخاص بـ Finance قد يختلف عن مفتاح اللوحة العامة،
    # لذا نسمح بتمرير api_key يدوياً ونحقنه عبر متغير بيئة للاستدعاء الواحد.
    messages = _build_chat_messages(payload_text, user_question)

    # ── المسار الخاص: عند تمرير مفتاح مخصّص للمؤسس ──
    if (api_key or "").strip():
        return _direct_call_with_key(prov, md, api_key, messages)

    # ── المسار الافتراضي: استخدم llm_provider.chat (يقرأ من system_settings) ──
    try:
        from logic.llm_provider import chat as _chat

        res = _chat(
            messages=messages,
            max_tokens=900,
            temperature=0.2,
            provider=prov,
            model=md or None,
            intent_label="finance_analyst",
        )
        if not res or not res.success:
            return None, (res.error or "تعذّر الاستدعاء")[:380]
        return (res.text or "").strip() or None, None
    except Exception as ex:
        logger.exception("finance llm via central provider")
        return None, str(ex)[:380]


def _direct_call_with_key(
    provider: str, model: str, api_key: str, messages: list
) -> Tuple[Optional[str], Optional[str]]:
    """استدعاء مباشر بمفتاح مخصّص — يدعم نفس المزوّدات المتوافقة مع OpenAI/Anthropic."""
    try:
        import requests as rq
    except ImportError:
        return None, "مكتبة requests غير متاحة"

    key = (api_key or "").strip()

    # خرائط Base URL للمزوّدات المتوافقة OpenAI
    openai_compat = {
        "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
        "mistral": ("https://api.mistral.ai/v1", "mistral-large-latest"),
        "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
        "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
        "manus": ("https://api.manus.im/v1", "manus-pro"),
    }
    if provider in openai_compat:
        base, default_model = openai_compat[provider]
        m = model or default_model
        try:
            r = rq.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": m,
                    "temperature": 0.2,
                    "max_tokens": 900,
                    "messages": messages,
                },
                timeout=120,
            )
            if r.status_code != 200:
                return None, (r.text or "")[:380]
            data = r.json()
            ch = data.get("choices") or [{}]
            txt = (ch[0].get("message") or {}).get("content") or ""
            return (txt.strip() or None), None
        except Exception as ex:
            logger.exception("%s analyst direct", provider)
            return None, str(ex)[:380]

    if provider == "anthropic":
        m = model or "claude-3-5-sonnet-20240620"
        # Anthropic يفصل system عن messages
        system = ""
        user_msgs = []
        for msg in messages:
            if msg.get("role") == "system":
                system += msg.get("content", "") + "\n"
            else:
                user_msgs.append(msg)
        try:
            r = rq.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": m,
                    "max_tokens": 900,
                    "system": system.strip(),
                    "messages": user_msgs,
                    "temperature": 0.2,
                },
                timeout=120,
            )
            if r.status_code != 200:
                return None, (r.text or "")[:380]
            data = r.json()
            parts = []
            for bl in data.get("content") or []:
                if isinstance(bl, dict) and bl.get("type") == "text":
                    parts.append(bl.get("text") or "")
            return ("\n".join(parts).strip() or None), None
        except Exception as ex:
            logger.exception("anthropic analyst direct")
            return None, str(ex)[:380]

    if provider == "gemini":
        m = model or "gemini-1.5-flash"
        # Gemini يحتاج دمج system + user
        system = ""
        user_text = ""
        for msg in messages:
            if msg.get("role") == "system":
                system += msg.get("content", "") + "\n"
            elif msg.get("role") == "user":
                user_text = msg.get("content", "")
        full = (system + "\n\n" + user_text).strip()
        try:
            r = rq.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"role": "user", "parts": [{"text": full}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 900},
                },
                timeout=120,
            )
            if r.status_code != 200:
                return None, (r.text or "")[:380]
            data = r.json()
            cands = data.get("candidates") or [{}]
            parts = ((cands[0].get("content") or {}).get("parts") or [])
            txt = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            return (txt.strip() or None), None
        except Exception as ex:
            logger.exception("gemini analyst direct")
            return None, str(ex)[:380]

    return None, f"المزوّد «{provider}» غير مدعوم في المحلل المالي."


def generate_insights_brief(
    db: Any,
    dashboard: Dict[str, Any],
    provider: str,
    api_key: str,
    model: str,
) -> Tuple[Optional[str], Optional[str]]:
    """ملخّص تنفيذي تلقائي يُعرض عند فتح اللوحة."""
    txt = build_financial_context_payload(db, dashboard)
    q = """اكتب ملخصاً تنفيذياً مختصراً (٥-٦ نقاط) عن:
1) أداء اليوم — مبيعات، عمليات، هامش، وصافي ربح.
2) مقارنة الشهر الحالي بالشهر السابق (mom_comparison).
3) أعلى وأدنى فرع نشاطاً، وساعة الذروة الأبرز.
4) إشارات الاسترجاع (returns_signal) وما إذا كانت ضمن الصحي.
5) توصية واحدة قابلة للتنفيذ خلال الأسبوع.
6) اذكر إن كان المصدر داخلياً تقديرياً (internal_fallback) أم خارجياً (remote)."""
    return run_financial_llm(
        payload_text=txt,
        user_question=q,
        provider=provider,
        api_key=api_key,
        model=model,
    )
