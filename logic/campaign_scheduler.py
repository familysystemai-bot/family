# -*- coding: utf-8 -*-
"""
مجدول حملات: يفحص الحملات المجدولة ويرسل البريد عند حلول الوقت.
يعمل في خيط خلفي اختياري؛ لا يمس مسارات الشات.
"""
from __future__ import annotations

import atexit
import logging
import os
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

_LEASE_KEY = "campaign_scheduler_lease"
_LEASE_TTL_SEC = max(45, int(CAMPAIGN_SCHEDULER_INTERVAL_SEC) * 3)
_SCHEDULER_STATE: dict[str, Any] = {
    "thread": None,
    "stop_event": None,
    "owner_id": None,
    "registered_atexit": False,
}


def _default_url_root() -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/") + "/"
    return f"http://127.0.0.1:{FLASK_PORT}/"


def _lease_now() -> int:
    return int(time.time())


def _parse_lease(raw: Any) -> tuple[Optional[str], int]:
    text = (raw or "").strip()
    if not text or "|" not in text:
        return None, 0
    owner, _, ts_raw = text.partition("|")
    try:
        return owner or None, int(ts_raw)
    except ValueError:
        return owner or None, 0


def _build_lease(owner_id: str) -> str:
    return f"{owner_id}|{_lease_now()}"


def _acquire_or_refresh_scheduler_lease(db, owner_id: str) -> bool:
    current = db.get_system_setting(_LEASE_KEY, "") or ""
    current_owner, current_ts = _parse_lease(current)
    now = _lease_now()
    expired = not current_owner or current_ts <= 0 or (now - current_ts) > _LEASE_TTL_SEC
    if expired or current_owner == owner_id:
        if not db.set_system_setting(_LEASE_KEY, _build_lease(owner_id)):
            logger.error("campaign_scheduler: failed to write lease")
            return False
        return True
    return False


def _release_scheduler_lease(db, owner_id: Optional[str]) -> None:
    if not owner_id:
        return
    try:
        current = db.get_system_setting(_LEASE_KEY, "") or ""
        current_owner, _ = _parse_lease(current)
        if current_owner == owner_id:
            db.set_system_setting(_LEASE_KEY, "")
    except Exception:
        logger.exception("campaign_scheduler: failed to release lease")


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
                    "campaign_scheduler: sent campaign %s email_targeted=%s email_sent=%s wa_targeted=%s wa_sent=%s",
                    cid,
                    res.get("targeted"),
                    res.get("sent"),
                    res.get("wa_targeted"),
                    res.get("wa_sent"),
                )
        except Exception:
            logger.exception("campaign_scheduler: failed campaign %s", cid)
    return out


def _scheduler_loop(db_holder: dict, stop_event: threading.Event, owner_id: str) -> None:
    db = db_holder.get("db")
    if db is None:
        return
    root = _default_url_root()
    logger.info("campaign_scheduler: loop started owner=%s", owner_id)
    while not stop_event.is_set():
        try:
            if not _acquire_or_refresh_scheduler_lease(db, owner_id):
                logger.debug("campaign_scheduler: lease held by another worker")
                stop_event.wait(max(15, int(CAMPAIGN_SCHEDULER_INTERVAL_SEC)))
                continue
            process_due_campaigns(db, root)
        except Exception:
            logger.exception("campaign_scheduler loop")
        stop_event.wait(max(15, int(CAMPAIGN_SCHEDULER_INTERVAL_SEC)))
    _release_scheduler_lease(db, owner_id)
    logger.info("campaign_scheduler: loop stopped owner=%s", owner_id)


def stop_campaign_scheduler_thread() -> None:
    stop_event = _SCHEDULER_STATE.get("stop_event")
    thread = _SCHEDULER_STATE.get("thread")
    owner_id = _SCHEDULER_STATE.get("owner_id")
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)
    db = _SCHEDULER_STATE.get("db")
    if db is not None:
        _release_scheduler_lease(db, owner_id)
    _SCHEDULER_STATE["thread"] = None
    _SCHEDULER_STATE["stop_event"] = None
    _SCHEDULER_STATE["owner_id"] = None


def _register_scheduler_shutdown() -> None:
    if _SCHEDULER_STATE.get("registered_atexit"):
        return
    atexit.register(stop_campaign_scheduler_thread)
    _SCHEDULER_STATE["registered_atexit"] = True


def start_campaign_scheduler_thread(db) -> None:
    """تشغيل خيط خفي يفحص الحملات بشكل دوري."""
    if not CAMPAIGN_SCHEDULER_ENABLED:
        logger.info("campaign_scheduler: disabled via CAMPAIGN_SCHEDULER_ENABLED")
        return
    thread = _SCHEDULER_STATE.get("thread")
    if thread is not None and thread.is_alive():
        logger.info("campaign_scheduler: already running in this process")
        return
    owner_id = f"{os.getpid()}:{id(db)}"
    if not _acquire_or_refresh_scheduler_lease(db, owner_id):
        logger.info("campaign_scheduler: another worker already owns the lease")
        return
    holder = {"db": db}
    stop_event = threading.Event()
    t = threading.Thread(
        target=_scheduler_loop,
        args=(holder, stop_event, owner_id),
        daemon=True,
        name="campaign-scheduler",
    )
    _SCHEDULER_STATE["thread"] = t
    _SCHEDULER_STATE["stop_event"] = stop_event
    _SCHEDULER_STATE["owner_id"] = owner_id
    _SCHEDULER_STATE["db"] = db
    _register_scheduler_shutdown()
    t.start()
    logger.info(
        "campaign_scheduler: started owner=%s (interval=%ss)",
        owner_id,
        CAMPAIGN_SCHEDULER_INTERVAL_SEC,
    )
