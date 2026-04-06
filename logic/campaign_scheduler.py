# -*- coding: utf-8 -*-
"""
مجدول حملات: يفحص الحملات المجدولة ويرسل البريد عند حلول الوقت.
يعمل في خيط خلفي اختياري؛ لا يمس مسارات الشات.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional

from config import (
    CAMPAIGN_SCHEDULER_ENABLED,
    CAMPAIGN_SCHEDULER_INTERVAL_SEC,
    FLASK_PORT,
    PUBLIC_BASE_URL,
)

logger = logging.getLogger(__name__)


def _default_url_root() -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/") + "/"
    return f"http://127.0.0.1:{FLASK_PORT}/"


def process_due_campaigns(db, request_url_root: Optional[str] = None) -> List[dict]:
    """
    معالجة جميع الحملات المستحقة دفعة واحدة.
    يعيد قائمة بنتائج send_campaign لكل معرف.
    """
    from logic import campaign_service as camp_svc

    root = request_url_root or _default_url_root()
    ids = camp_svc.get_due_scheduled_campaign_ids(db)
    out: List[dict] = []
    for cid in ids:
        try:
            res = camp_svc.send_campaign(db, cid, root)
            out.append(res)
            if res.get("ok"):
                logger.info(
                    "campaign_scheduler: sent campaign %s targeted=%s sent=%s",
                    cid,
                    res.get("targeted"),
                    res.get("sent"),
                )
        except Exception:
            logger.exception("campaign_scheduler: failed campaign %s", cid)
    return out


def _scheduler_loop(db_holder: dict) -> None:
    db = db_holder.get("db")
    if db is None:
        return
    root = _default_url_root()
    while True:
        try:
            process_due_campaigns(db, root)
        except Exception:
            logger.exception("campaign_scheduler loop")
        time.sleep(max(15, int(CAMPAIGN_SCHEDULER_INTERVAL_SEC)))


def start_campaign_scheduler_thread(db) -> None:
    """تشغيل خيط خفي يفحص الحملات بشكل دوري."""
    if not CAMPAIGN_SCHEDULER_ENABLED:
        logger.info("campaign_scheduler: disabled via CAMPAIGN_SCHEDULER_ENABLED")
        return
    holder = {"db": db}
    t = threading.Thread(target=_scheduler_loop, args=(holder,), daemon=True)
    t.start()
    logger.info(
        "campaign_scheduler: started (interval=%ss)",
        CAMPAIGN_SCHEDULER_INTERVAL_SEC,
    )
