# -*- coding: utf-8 -*-
"""
AI Usage Tracker — متتبّع استخدام الذكاء الاصطناعي.
======================================================

الهدف:
    مراقبة نسبة الردود المحلية مقابل الردود التي تستهلك LLM
    (OpenAI / Gemini / غيرها)، لقياس فعالية تقليل التكلفة.

الاستخدام:
    from logic.ai_usage_tracker import track_local_response, track_llm_call

    # عند رد محلي:
    track_local_response(intent="greeting", source="rule_based")

    # عند استدعاء LLM:
    track_llm_call(provider="openai", model="gpt-4o-mini", tokens=120)

    # للحصول على الإحصائيات (للوحة المؤسس):
    from logic.ai_usage_tracker import get_usage_stats
    stats = get_usage_stats(days=7)

تخزين البيانات:
    يستخدم جدول analytics_ai_usage في نفس قاعدة البيانات.
    إن لم يكن الجدول موجوداً، تُسكَت الأخطاء بهدوء (لا يكسر التدفق).

ملاحظة:
    التتبع اختياري بالكامل — أي فشل في التسجيل لا يؤثر على رد العميل.
    الهدف: قياس وتحسين، ليس حظراً أو منع تنفيذ.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from logic.db_adapter import DBAdapter

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# DDL لإنشاء الجدول (يُستدعى مرة واحدة من _ensure_table)
# ═══════════════════════════════════════════════════════════════

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS analytics_ai_usage (
    id SERIAL PRIMARY KEY,
    day TEXT NOT NULL,
    response_type TEXT NOT NULL,
    intent TEXT,
    source TEXT,
    provider TEXT,
    model TEXT,
    tokens INTEGER DEFAULT 0,
    count INTEGER NOT NULL DEFAULT 1,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(day, response_type, intent, source, provider, model)
)
"""

_table_ensured = False

_CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS analytics_llm_events (
    id SERIAL PRIMARY KEY,
    day TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    session_key TEXT NOT NULL DEFAULT '',
    provider TEXT,
    model TEXT,
    intent TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_EVENTS_INDEX_DDL = (
    """
    CREATE INDEX IF NOT EXISTS idx_analytics_llm_events_day_ch
    ON analytics_llm_events (day, channel)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_analytics_llm_events_session
    ON analytics_llm_events (session_key)
    """,
)

_events_table_ensured = False


def _ensure_events_table(db: DBAdapter) -> bool:
    """جدول حدث لكل استدعاء LLM (قناة + جلسة + توكنز مدخلات/مخرجات)."""
    global _events_table_ensured
    if _events_table_ensured:
        return True
    try:
        from logic.sql_translator import translate_ddl

        ddl = translate_ddl(_CREATE_EVENTS_SQL, db.db_type)
        db.execute(ddl)
        for idx_sql in _EVENTS_INDEX_DDL:
            try:
                db.execute(translate_ddl(idx_sql.strip(), db.db_type))
            except Exception:
                pass
        _events_table_ensured = True
        return True
    except Exception as e:
        logger.warning("ai_usage_tracker: cannot ensure events table: %s", e)
        return False


def _ensure_table(db: DBAdapter) -> bool:
    """ينشئ الجدول إن لم يكن موجوداً (مرة واحدة فقط لكل عملية تشغيل)."""
    global _table_ensured
    if _table_ensured:
        return True
    try:
        # نستخدم SQL متوافق مع الاثنين عبر translate_ddl
        from logic.sql_translator import translate_ddl
        ddl = translate_ddl(_CREATE_TABLE_SQL, db.db_type)
        db.execute(ddl)
        _table_ensured = True
        return True
    except Exception as e:
        logger.warning("ai_usage_tracker: cannot ensure table: %s", e)
        return False


def infer_llm_session_context() -> Tuple[str, str]:
    """
    يحدّد مفتاح الجلسة والقناة من جلسة Flask (إن وُجدت).
    واتساب: user_id / sid يبدآن بـ wa_
    """
    try:
        from flask import has_request_context
        from flask import session as flask_session

        if not has_request_context():
            return "", "system"
        uid = (flask_session.get("user_id") or flask_session.get("sid") or "").strip()
        if uid.startswith("wa_"):
            return uid[:256], "wa"
        if uid:
            return uid[:256], "web"
        return "", "system"
    except Exception:
        return "", "system"


def _get_db() -> Optional[DBAdapter]:
    try:
        return DBAdapter()
    except Exception as e:
        logger.warning("ai_usage_tracker: cannot init DBAdapter: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════
# واجهات التتبع
# ═══════════════════════════════════════════════════════════════

def track_local_response(intent: str = "unknown", source: str = "rule_based") -> None:
    """
    يسجّل رد محلي (لم يستهلك LLM).

    المعاملات:
        intent: نوع النية (greeting, complaint, location, product...)
        source: مصدر القرار (rule_based, score_direct, semantic_match, db_match...)
    """
    db = _get_db()
    if db is None:
        return
    if not _ensure_table(db):
        return
    try:
        today = date.today().isoformat()
        intent = (intent or "unknown")[:64]
        source = (source or "")[:64]
        if db.db_type == "postgres":
            db.execute(
                """
                INSERT INTO analytics_ai_usage
                    (day, response_type, intent, source, provider, model, tokens, count)
                VALUES (%s, 'local', %s, %s, '', '', 0, 1)
                ON CONFLICT (day, response_type, intent, source, provider, model)
                DO UPDATE SET count = analytics_ai_usage.count + 1,
                              last_updated = CURRENT_TIMESTAMP
                """,
                (today, intent, source),
            )
        else:
            # SQLite path
            db.execute(
                """
                INSERT INTO analytics_ai_usage
                    (day, response_type, intent, source, provider, model, tokens, count)
                VALUES (%s, 'local', %s, %s, '', '', 0, 1)
                ON CONFLICT (day, response_type, intent, source, provider, model)
                DO UPDATE SET count = count + 1,
                              last_updated = CURRENT_TIMESTAMP
                """,
                (today, intent, source),
            )
    except Exception as e:
        logger.debug("ai_usage_tracker.track_local_response failed silently: %s", e)


def track_llm_call(
    provider: str = "openai",
    model: str = "",
    tokens: int = 0,
    intent: str = "unknown",
    *,
    session_key: str = "",
    channel: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    """
    يسجّل استدعاء LLM (استهلاك فعلي).

    المعاملات:
        provider: openai, gemini, anthropic...
        model: gpt-4o-mini, gemini-pro, gpt-3.5-turbo...
        tokens: عدد التوكنز المستهلكة (اختياري)
        intent: ما النية التي استدعت LLM
        session_key / channel: اختياري؛ إن تُركا يُستنتجان من جلسة الطلب (wa / web).
        prompt_tokens / completion_tokens: تفصيل المدخلات والمخرجات عند توفرها من المزوّد.
    """
    db = _get_db()
    if db is None:
        return
    if not _ensure_table(db):
        return
    if not _ensure_events_table(db):
        pass
    try:
        today = date.today().isoformat()
        intent = (intent or "unknown")[:64]
        provider = (provider or "")[:32]
        model = (model or "")[:64]
        try:
            tokens_int = max(0, int(tokens))
        except (TypeError, ValueError):
            tokens_int = 0
        try:
            pt_i = max(0, int(prompt_tokens))
        except (TypeError, ValueError):
            pt_i = 0
        try:
            ct_i = max(0, int(completion_tokens))
        except (TypeError, ValueError):
            ct_i = 0
        if pt_i + ct_i > 0 and tokens_int <= 0:
            tokens_int = pt_i + ct_i

        auto_sk, auto_ch = infer_llm_session_context()
        sk = ((session_key or "").strip() or auto_sk)[:256]
        ch = ((channel or "").strip() or auto_ch).strip() or "system"
        ch = ch[:16]

        if db.db_type == "postgres":
            db.execute(
                """
                INSERT INTO analytics_ai_usage
                    (day, response_type, intent, source, provider, model, tokens, count)
                VALUES (%s, 'llm', %s, 'llm_call', %s, %s, %s, 1)
                ON CONFLICT (day, response_type, intent, source, provider, model)
                DO UPDATE SET count = analytics_ai_usage.count + 1,
                              tokens = analytics_ai_usage.tokens + EXCLUDED.tokens,
                              last_updated = CURRENT_TIMESTAMP
                """,
                (today, intent, provider, model, tokens_int),
            )
        else:
            db.execute(
                """
                INSERT INTO analytics_ai_usage
                    (day, response_type, intent, source, provider, model, tokens, count)
                VALUES (%s, 'llm', %s, 'llm_call', %s, %s, %s, 1)
                ON CONFLICT (day, response_type, intent, source, provider, model)
                DO UPDATE SET count = count + 1,
                              tokens = tokens + excluded.tokens,
                              last_updated = CURRENT_TIMESTAMP
                """,
                (today, intent, provider, model, tokens_int),
            )
        try:
            db.execute(
                """
                INSERT INTO analytics_llm_events
                    (day, channel, session_key, provider, model, intent,
                     prompt_tokens, completion_tokens, total_tokens)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    today,
                    ch,
                    sk,
                    provider,
                    model,
                    intent,
                    pt_i,
                    ct_i,
                    max(tokens_int, pt_i + ct_i),
                ),
            )
        except Exception as ev_err:
            logger.debug("ai_usage_tracker llm event insert skipped: %s", ev_err)
    except Exception as e:
        logger.debug("ai_usage_tracker.track_llm_call failed silently: %s", e)


# ═══════════════════════════════════════════════════════════════
# واجهات قراءة الإحصائيات (للوحة المؤسس)
# ═══════════════════════════════════════════════════════════════

def get_usage_stats(days: int = 7) -> Dict[str, Any]:
    """
    يُرجع إحصائيات استخدام الذكاء الاصطناعي خلال آخر N أيام.

    البنية المُرجَعة:
        {
            "days": 7,
            "total_responses": 1234,
            "local_responses": 950,
            "llm_responses": 284,
            "local_percentage": 77.0,
            "llm_percentage": 23.0,
            "total_tokens": 45120,
            "by_provider": {"openai": {...}, "gemini": {...}},
            "by_intent": [{"intent": "greeting", "local": 200, "llm": 5}, ...],
            "daily": [{"day": "2025-01-15", "local": 120, "llm": 30}, ...]
        }
    """
    days = max(1, min(int(days or 7), 90))
    end = date.today()
    start = end - timedelta(days=days - 1)
    empty = {
        "days": days,
        "total_responses": 0,
        "local_responses": 0,
        "llm_responses": 0,
        "local_percentage": 0.0,
        "llm_percentage": 0.0,
        "total_tokens": 0,
        "by_provider": {},
        "by_intent": [],
        "daily": [],
    }

    db = _get_db()
    if db is None:
        return empty
    if not _ensure_table(db):
        return empty

    try:
        rows = db.fetch_all(
            """
            SELECT day, response_type, intent, provider, model,
                   SUM(count) AS cnt, SUM(tokens) AS toks
            FROM analytics_ai_usage
            WHERE day >= %s AND day <= %s
            GROUP BY day, response_type, intent, provider, model
            ORDER BY day
            """,
            (start.isoformat(), end.isoformat()),
        )
    except Exception as e:
        logger.warning("ai_usage_tracker.get_usage_stats query failed: %s", e)
        return empty

    if not rows:
        return empty

    local_total = 0
    llm_total = 0
    tokens_total = 0
    by_provider: Dict[str, Dict[str, Any]] = {}
    by_intent_acc: Dict[str, Dict[str, int]] = {}
    daily_acc: Dict[str, Dict[str, int]] = {}

    for r in rows:
        rtype = (r.get("response_type") or "").strip()
        cnt = int(r.get("cnt") or 0)
        toks = int(r.get("toks") or 0)
        intent = (r.get("intent") or "unknown") or "unknown"
        provider = (r.get("provider") or "").strip() or "n/a"
        day = (r.get("day") or "").strip()

        if rtype == "local":
            local_total += cnt
        elif rtype == "llm":
            llm_total += cnt
            tokens_total += toks
            if provider not in by_provider:
                by_provider[provider] = {"calls": 0, "tokens": 0, "models": {}}
            by_provider[provider]["calls"] += cnt
            by_provider[provider]["tokens"] += toks
            model = (r.get("model") or "").strip() or "default"
            by_provider[provider]["models"][model] = (
                by_provider[provider]["models"].get(model, 0) + cnt
            )

        if intent not in by_intent_acc:
            by_intent_acc[intent] = {"local": 0, "llm": 0}
        if rtype in ("local", "llm"):
            by_intent_acc[intent][rtype] += cnt

        if day:
            if day not in daily_acc:
                daily_acc[day] = {"local": 0, "llm": 0}
            if rtype in ("local", "llm"):
                daily_acc[day][rtype] += cnt

    total = local_total + llm_total
    if total > 0:
        local_pct = round(local_total * 100.0 / total, 1)
        llm_pct = round(llm_total * 100.0 / total, 1)
    else:
        local_pct = 0.0
        llm_pct = 0.0

    by_intent = [
        {"intent": k, "local": v["local"], "llm": v["llm"], "total": v["local"] + v["llm"]}
        for k, v in by_intent_acc.items()
    ]
    by_intent.sort(key=lambda x: -x["total"])

    daily = []
    d = start
    while d <= end:
        ds = d.isoformat()
        rec = daily_acc.get(ds, {"local": 0, "llm": 0})
        daily.append({"day": ds, "local": rec["local"], "llm": rec["llm"]})
        d += timedelta(days=1)

    return {
        "days": days,
        "total_responses": total,
        "local_responses": local_total,
        "llm_responses": llm_total,
        "local_percentage": local_pct,
        "llm_percentage": llm_pct,
        "total_tokens": tokens_total,
        "by_provider": by_provider,
        "by_intent": by_intent[:20],
        "daily": daily,
    }


def _empty_llm_ch() -> Dict[str, int]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def get_wa_contact_ai_usage_map(days: int = 90) -> Dict[str, Dict[str, int]]:
    """
    لكل رقم واتساب (أرقام فقط): عدد استدعاءات LLM ومجاميع التوكنز في الفترة.
    المفتاح = contact_number كما في جدول messages.
    """
    days = max(1, min(int(days or 90), 365))
    end = date.today()
    start = end - timedelta(days=days - 1)
    out: Dict[str, Dict[str, int]] = {}
    db = _get_db()
    if db is None or not _ensure_events_table(db):
        return out
    try:
        from logic.wa_inbox_repository import normalize_wa_contact_number

        rows = db.fetch_all(
            """
            SELECT session_key,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS pt,
                   COALESCE(SUM(completion_tokens), 0) AS ct,
                   COALESCE(SUM(total_tokens), 0) AS tt
            FROM analytics_llm_events
            WHERE channel = 'wa' AND day >= %s AND day <= %s
            GROUP BY session_key
            """,
            (start.isoformat(), end.isoformat()),
        )
        for r in rows or []:
            sk = (r.get("session_key") or "").strip()
            digits = sk[3:] if sk.startswith("wa_") else sk
            cn = normalize_wa_contact_number(digits)
            if not cn:
                continue
            out[cn] = {
                "ai_calls": int(r.get("calls") or 0),
                "ai_prompt_tokens": int(r.get("pt") or 0),
                "ai_completion_tokens": int(r.get("ct") or 0),
                "ai_total_tokens": int(r.get("tt") or 0),
            }
    except Exception as e:
        logger.warning("get_wa_contact_ai_usage_map failed: %s", e)
    return out


def get_founder_accounting(days: int = 30) -> Dict[str, Any]:
    """
    إحصاءات للمؤسس: رسائل واتساب (صندوق) + استهلاك LLM حسب القناة + أكثر جهات اتصال استهلاكاً.
    """
    days = max(1, min(int(days or 30), 90))
    end = date.today()
    start = end - timedelta(days=days - 1)
    ds0, ds1 = start.isoformat(), end.isoformat()

    base: Dict[str, Any] = {
        "days": days,
        "period_start": ds0,
        "period_end": ds1,
        "wa_messages_inbound": 0,
        "wa_messages_outbound": 0,
        "llm_wa": _empty_llm_ch(),
        "llm_web": _empty_llm_ch(),
        "llm_system": _empty_llm_ch(),
        "wa_top_contacts": [],
    }

    db = _get_db()
    if db is None:
        return base

    try:
        mrows = db.fetch_all(
            """
            SELECT direction, COUNT(*) AS c
            FROM messages
            WHERE COALESCE(msg_timestamp, '') >= %s
            GROUP BY direction
            """,
            (ds0,),
        )
        for mr in mrows or []:
            d = (mr.get("direction") or "").strip().lower()
            c = int(mr.get("c") or 0)
            if d == "inbound":
                base["wa_messages_inbound"] = c
            elif d == "outbound":
                base["wa_messages_outbound"] = c
    except Exception as e:
        logger.warning("get_founder_accounting messages: %s", e)

    if not _ensure_events_table(db):
        return base

    try:
        ch_rows = db.fetch_all(
            """
            SELECT channel,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS pt,
                   COALESCE(SUM(completion_tokens), 0) AS ct,
                   COALESCE(SUM(total_tokens), 0) AS tt
            FROM analytics_llm_events
            WHERE day >= %s AND day <= %s
            GROUP BY channel
            """,
            (ds0, ds1),
        )
        for row in ch_rows or []:
            ch = (row.get("channel") or "").strip().lower()
            bucket = (
                "llm_wa"
                if ch == "wa"
                else "llm_web"
                if ch == "web"
                else "llm_system"
            )
            if bucket not in base:
                continue
            b = base[bucket]
            b["calls"] = int(row.get("calls") or 0)
            b["prompt_tokens"] = int(row.get("pt") or 0)
            b["completion_tokens"] = int(row.get("ct") or 0)
            b["total_tokens"] = int(row.get("tt") or 0)
    except Exception as e:
        logger.warning("get_founder_accounting llm by channel: %s", e)

    try:
        from logic.wa_inbox_repository import normalize_wa_contact_number

        top_rows = db.fetch_all(
            """
            SELECT session_key,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS pt,
                   COALESCE(SUM(completion_tokens), 0) AS ct,
                   COALESCE(SUM(total_tokens), 0) AS tt
            FROM analytics_llm_events
            WHERE channel = 'wa' AND day >= %s AND day <= %s
            GROUP BY session_key
            ORDER BY tt DESC
            LIMIT 20
            """,
            (ds0, ds1),
        )
        ranked: List[Dict[str, Any]] = []
        for tr in top_rows or []:
            sk = (tr.get("session_key") or "").strip()
            digits = sk[3:] if sk.startswith("wa_") else sk
            cn = normalize_wa_contact_number(digits)
            if not cn:
                continue
            ranked.append(
                {
                    "contact_number": cn,
                    "ai_calls": int(tr.get("calls") or 0),
                    "ai_prompt_tokens": int(tr.get("pt") or 0),
                    "ai_completion_tokens": int(tr.get("ct") or 0),
                    "ai_total_tokens": int(tr.get("tt") or 0),
                }
            )
        base["wa_top_contacts"] = ranked
    except Exception as e:
        logger.warning("get_founder_accounting top wa: %s", e)

    return base