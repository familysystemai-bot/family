# -*- coding: utf-8 -*-
"""محلل مالي بالذكاء الاصطناعي — سياق داخلي فقط؛ يستخدم مفتاح المؤسس المخزَّن بتشفير."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

SYSTEM_AR = """أنت «مانوس» — محلل مالي استراتيجي لمؤسسة «المناخ / مجمع العائلة».

الشروط:
- أجِب بالعربية وباختصار عملي؛ ركّز على الأرقام المرسلة في JSON ولا تخترع مبيعات وهمية.
- إذا كانت بعض الحقول مقدّرة (mode=internal_fallback)، صرّح بلطف أن هذا مؤشر داخلي حتى توصَّل الواجهة المحاسبية الكاملة.
- لا تنصح بتجاوز الأنظمة؛ التزم بتحليل التشغيل والاتجاه واقتراحات تحسين.
- تجنّب الإفصاح عن أي مفتاح أو بيانات اعتماد."""


def build_financial_context_payload(db: Any, dashboard: Dict[str, Any]) -> str:
    extra: Dict[str, Any] = {}
    try:
        extra["products_total"] = int(db.count_products_total())
    except Exception:
        extra["products_total"] = None
    try:
        extra["pending_inquiries"] = len(db.get_all_pending_inquiries(limit=200) or [])
    except Exception:
        extra["pending_inquiries"] = None

    envelope = {"dashboard_metrics": dashboard, "internal_signals": extra}
    return json.dumps(envelope, ensure_ascii=False)


def run_financial_llm(
    *,
    payload_text: str,
    user_question: str,
    provider: str,
    api_key: str,
    model: str,
) -> tuple:
    """يعيد (نصّ الرد أو None، رسالة خطأ أو None)."""
    key = (api_key or "").strip()
    q = (user_question or "").strip()
    prov = (provider or "openai").strip().lower()
    md = (model or "").strip()
    if len(q) > 3500:
        q = q[:3490] + "…"
    user_block = payload_text[:12000] + "\n---\nسؤال التنفيذي:\n" + q

    try:
        import requests as rq
    except ImportError:
        return None, "مكتبة requests غير متاحة"

    if prov == "anthropic":
        m = md or "claude-3-5-sonnet-20240620"
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
                    "system": SYSTEM_AR,
                    "messages": [{"role": "user", "content": user_block}],
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
            return ("\n".join(parts).strip() or None, None)
        except Exception as ex:
            logger.exception("anthropic analyst")
            return None, str(ex)[:380]

    m = md or "gpt-4o-mini"
    try:
        r = rq.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": m,
                "temperature": 0.2,
                "max_tokens": 900,
                "messages": [
                    {"role": "system", "content": SYSTEM_AR},
                    {"role": "user", "content": user_block},
                ],
            },
            timeout=120,
        )
        if r.status_code != 200:
            return None, (r.text or "")[:380]
        data = r.json()
        ch = data.get("choices") or [{}]
        msg = (ch[0].get("message") or {}).get("content") or ""
        return (msg.strip() or None, None)
    except Exception as ex:
        logger.exception("openai analyst")
        return None, str(ex)[:380]


def generate_insights_brief(
    db: Any, dashboard: Dict[str, Any], provider: str, api_key: str, model: str
) -> tuple:
    txt = build_financial_context_payload(db, dashboard)
    q = """الملخص: اكتب خمس نقاط تنفيذية (تبويب نقاط مختصرة) عن أداء المبيعات اليوم وأكبر فرع نشاطاً،
مقابل حجم استفسارات العملاء؛ واذكر إن كانت المصادر خارجية حقيقية أو مؤشر داخلي (حسب dashboard_metrics.mode).

لا تطرح أسئلة مفتوحة — أعط ملخصاً فقط."""
    return run_financial_llm(
        payload_text=txt, user_question=q, provider=provider, api_key=api_key, model=model
    )
