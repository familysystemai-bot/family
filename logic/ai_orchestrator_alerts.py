# -*- coding: utf-8 -*-
"""
تنبيهات فشل منسّق الشات: رسالة للعميل + بريد (SYSTEM_ALERTS_EMAIL) + تسجيل في trend_data.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from config import DATA_DIR, ORCHESTRATOR_FAILURE_ALERT_COOLDOWN_SECONDS, SYSTEM_ALERTS_EMAIL

logger = logging.getLogger(__name__)

_STATE_PATH: Path = DATA_DIR / "orchestrator_failure_alert_state.json"

ORCHESTRATOR_FAILURE_USER_MESSAGE = (
    "عذراً سيدي، واجهت خطأ في فهم طلبك. "
    "جرّب مرة ثانية بعد قليل، أو تواصل مع الفرع مباشرة."
)


def orchestrator_failure_plan(*, reason: str = "") -> Dict[str, Any]:
    """خطة JSON تُرجع للعميل عند فشل المنسّق بعد محاولة استدعاء فعلية."""
    return {
        "action": "general_response",
        "message": ORCHESTRATOR_FAILURE_USER_MESSAGE,
        "filters": {},
        "needs_branch": False,
        "orchestrator_error": True,
        "orchestrator_error_reason": (reason or "")[:120],
    }


def _cooldown_allows_email() -> bool:
    if ORCHESTRATOR_FAILURE_ALERT_COOLDOWN_SECONDS <= 0:
        return True
    now = time.time()
    try:
        if _STATE_PATH.is_file():
            raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            last = float(raw.get("last_email_ts", 0))
            if (now - last) < float(ORCHESTRATOR_FAILURE_ALERT_COOLDOWN_SECONDS):
                return False
    except Exception:
        pass
    return True


def _record_email_sent_ts() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps({"last_email_ts": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("orchestrator alert state write failed: %s", e)


def _record_trend(reason: str) -> None:
    code = (reason or "unknown").strip()[:120] or "unknown"
    try:
        from logic import chat_service as cs

        db = cs.get_db()
        db.upsert_trend("orchestrator_fail", code, increment=1)
    except Exception:
        logger.debug("orchestrator_fail trend skipped", exc_info=True)


def _send_alert_email_sync(
    reason: str,
    detail: str = "",
    user_message_preview: str = "",
) -> None:
    to = (SYSTEM_ALERTS_EMAIL or "").strip()
    if not to:
        logger.info("orchestrator failure alert: SYSTEM_ALERTS_EMAIL غير مضبوط — تخطي البريد")
        return
    if not _cooldown_allows_email():
        logger.debug("orchestrator failure alert: فترة تهدئة بريد نشطة")
        return
    from logic.mail_service import send_email

    subj = "[مجمع العائلة] تنبيه: فشل استدعاء ذكاء الشات (OpenAI)"
    body_lines = [
        "حدث خطأ أثناء استدعاء منسّق المحادثة (OpenAI).",
        "",
        f"السبب: {reason or 'غير محدد'}",
    ]
    if (detail or "").strip():
        body_lines.extend(["", f"التفاصيل: {detail.strip()[:2000]}"])
    if (user_message_preview or "").strip():
        body_lines.extend(
            ["", f"مقتطف من رسالة العميل: {user_message_preview.strip()[:800]}"]
        )
    body_lines.extend(["", "— رسالة آلية من النظام"])
    body = "\n".join(body_lines)
    try:
        if send_email([to], subj, body):
            _record_email_sent_ts()
    except Exception as e:
        logger.warning("orchestrator failure alert email error: %s", e)


def notify_orchestrator_failure(
    reason: str,
    *,
    detail: str = "",
    user_message_preview: str = "",
) -> None:
    """
    يسجّل في trend_data فوراً، ويُرسل بريداً (مع تهدئة) في خيط خلفي دون تعطيل الرد.
    """
    _record_trend(reason)

    def _run() -> None:
        try:
            _send_alert_email_sync(reason, detail=detail, user_message_preview=user_message_preview)
        except Exception:
            logger.debug("orchestrator notify background failed", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
