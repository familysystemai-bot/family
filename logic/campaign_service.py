# -*- coding: utf-8 -*-
"""
حملات إعلانية: تخزين، جدولة، استهداف ذكي (موافقون + فترة تهدئة 24 ساعة)، إرسال بريد.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from logic.dialect_responses import dialect_message
from logic.mail_service import send_email


def resolve_public_url_root(request_url_root: Optional[str]) -> str:
    """رابط عام للصور: من الطلب، أو PUBLIC_BASE_URL، أو localhost."""
    from config import FLASK_PORT, PUBLIC_BASE_URL

    if (request_url_root or "").strip():
        r = str(request_url_root).strip()
        return r if r.endswith("/") else r + "/"
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL + "/"
    return f"http://127.0.0.1:{FLASK_PORT}/"


def parse_schedule_input(raw: Optional[str]) -> Optional[str]:
    """قيمة datetime-local → نص SQLite قابل للمقارنة."""
    t = (raw or "").strip()
    if not t:
        return None
    t = t.replace("T", " ", 1)
    if len(t) == 16:
        t += ":00"
    if len(t) >= 19:
        t = t[:19]
    try:
        datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return t


def schedule_is_future(scheduled_sqlite: Optional[str]) -> bool:
    if not scheduled_sqlite or not str(scheduled_sqlite).strip():
        return False
    s = str(scheduled_sqlite).strip().replace("T", " ")
    if len(s) == 16:
        s += ":00"
    s = s[:19]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return dt > datetime.now()


def get_target_customers(db, branch_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    عملاء prefers_marketing = 1 مع بريد (بدون فلتر التهدئة — للإحصاءات).
    """
    conn = db._get_connection()
    try:
        if branch_id is not None:
            cur = conn.execute(
                """
                SELECT id, name, email, phone, branch_id, prefers_marketing, created_at
                FROM customers
                WHERE prefers_marketing = 1
                  AND branch_id = ?
                  AND email IS NOT NULL
                  AND TRIM(email) != ''
                ORDER BY id
                """,
                (int(branch_id),),
            )
        else:
            cur = conn.execute(
                """
                SELECT id, name, email, phone, branch_id, prefers_marketing, created_at
                FROM customers
                WHERE prefers_marketing = 1
                  AND email IS NOT NULL
                  AND TRIM(email) != ''
                ORDER BY id
                """
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_eligible_campaign_recipients(
    db, branch_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    مستهدفون فعليون: موافقة تسويق + بريد + عدم إرسال حملة خلال آخر 24 ساعة.
    """
    conn = db._get_connection()
    try:
        if branch_id is not None:
            cur = conn.execute(
                """
                SELECT id, name, email, phone, branch_id, prefers_marketing, created_at,
                       dialect, last_product_interest, last_product_interest_at,
                       last_campaign_sent_at
                FROM customers
                WHERE prefers_marketing = 1
                  AND branch_id = ?
                  AND email IS NOT NULL
                  AND TRIM(email) != ''
                  AND (
                    last_campaign_sent_at IS NULL
                    OR datetime(last_campaign_sent_at) < datetime('now', '-1 day')
                  )
                ORDER BY id
                """,
                (int(branch_id),),
            )
        else:
            cur = conn.execute(
                """
                SELECT id, name, email, phone, branch_id, prefers_marketing, created_at,
                       dialect, last_product_interest, last_product_interest_at,
                       last_campaign_sent_at
                FROM customers
                WHERE prefers_marketing = 1
                  AND email IS NOT NULL
                  AND TRIM(email) != ''
                  AND (
                    last_campaign_sent_at IS NULL
                    OR datetime(last_campaign_sent_at) < datetime('now', '-1 day')
                  )
                ORDER BY id
                """
            )
        rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
    return _sort_recipients_by_interest(rows)


def _sort_recipients_by_interest(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    with_i: List[Dict[str, Any]] = []
    without: List[Dict[str, Any]] = []
    for r in rows:
        if (r.get("last_product_interest") or "").strip():
            with_i.append(r)
        else:
            without.append(r)
    with_i.sort(
        key=lambda x: (x.get("last_product_interest_at") or ""),
        reverse=True,
    )
    return with_i + without


def get_target_customers_with_phone(db, branch_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = db._get_connection()
    try:
        if branch_id is not None:
            cur = conn.execute(
                """
                SELECT id, name, email, phone, branch_id
                FROM customers
                WHERE prefers_marketing = 1
                  AND branch_id = ?
                  AND phone IS NOT NULL
                  AND TRIM(phone) != ''
                ORDER BY id
                """,
                (int(branch_id),),
            )
        else:
            cur = conn.execute(
                """
                SELECT id, name, email, phone, branch_id
                FROM customers
                WHERE prefers_marketing = 1
                  AND phone IS NOT NULL
                  AND TRIM(phone) != ''
                ORDER BY id
                """
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_campaigns(db, branch_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = db._get_connection()
    try:
        if branch_id is not None:
            cur = conn.execute(
                """
                SELECT * FROM campaigns
                WHERE branch_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(branch_id), int(limit)),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM campaigns
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_due_scheduled_campaign_ids(db) -> List[int]:
    """حملات مجدولة لم تُرسَل بعد ووقتها الحالي أو مضى."""
    conn = db._get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id FROM campaigns
            WHERE sent_at IS NULL
              AND scheduled_at IS NOT NULL
              AND TRIM(scheduled_at) != ''
              AND datetime(scheduled_at) <= datetime('now', 'localtime')
            ORDER BY id
            """
        )
        return [int(r["id"]) for r in cur.fetchall()]
    finally:
        conn.close()


def get_campaign_by_id(db, campaign_id: int) -> Optional[Dict[str, Any]]:
    conn = db._get_connection()
    try:
        cur = conn.execute("SELECT * FROM campaigns WHERE id = ?", (int(campaign_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_campaign_dispatch_completed(db, campaign_id: int) -> None:
    conn = db._get_connection()
    try:
        conn.execute(
            "UPDATE campaigns SET sent_at = datetime('now') WHERE id = ?",
            (int(campaign_id),),
        )
        conn.commit()
    finally:
        conn.close()


def create_campaign(
    db,
    *,
    title: str,
    message: str,
    whatsapp_message: Optional[str],
    image_url: Optional[str],
    branch_id: Optional[int],
    created_by: str,
    scheduled_at: Optional[str] = None,
) -> Optional[int]:
    title = (title or "").strip()
    if not title:
        return None
    msg = (message or "").strip()
    wa = (whatsapp_message or "").strip() or None
    img = (image_url or "").strip() or None
    cb = (created_by or "branch").strip()[:32]
    sch = (scheduled_at or "").strip() or None
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO campaigns
            (title, message, whatsapp_message, image_url, branch_id, created_by,
             scheduled_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (title[:500], msg[:20000], wa[:20000] if wa else None, img[:2000], branch_id, cb, sch),
        )
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _public_image_url(image_url: Optional[str], url_root: str) -> Optional[str]:
    if not image_url:
        return None
    p = str(image_url).lstrip("/").replace("\\", "/")
    if p.startswith("static/"):
        path = p
    else:
        path = "static/" + p
    root = (url_root or "").rstrip("/")
    return f"{root}/{path}" if root else path


def build_campaign_email_body(
    *,
    message: str,
    image_url: Optional[str],
    request_url_root: str,
    customer_name: str,
    dialect: Optional[str],
    product_interest: Optional[str],
) -> str:
    root = resolve_public_url_root(request_url_root)
    name = (customer_name or "").strip() or "عميلنا"
    d = (dialect or "default").strip() or "default"
    lines: List[str] = [dialect_message(d, "campaign_opening", name=name), ""]
    prod = (product_interest or "").strip()
    if prod:
        lines.append(
            dialect_message(d, "campaign_product_teaser", product=prod)
        )
        lines.append("")
    msg = (message or "").strip()
    if msg:
        lines.append(msg)
        lines.append("")
    lines.append("📍 العرض متوفر الآن")
    pub = _public_image_url(image_url, root)
    if pub:
        lines.append("")
        lines.append(pub)
    return "\n".join(lines).strip()


def send_campaign(
    db, campaign_id: int, request_url_root: Optional[str] = None
) -> Dict[str, Any]:
    """
    إرسال حملة محفوظة: مستهدفون مؤهلون + ترتيب حسب الاهتمام، نص مخصص بالاسم واللهجة.
    """
    row = get_campaign_by_id(db, campaign_id)
    if not row:
        return {
            "ok": False,
            "error": "الحملة غير موجودة.",
            "campaign_id": campaign_id,
            "targeted": 0,
            "sent": 0,
            "failed": 0,
        }

    title = (row.get("title") or "").strip()
    message = (row.get("message") or "").strip()
    image_raw = (row.get("image_url") or "").strip() or None
    bid = row.get("branch_id")
    branch_scope: Optional[int]
    try:
        branch_scope = int(bid) if bid is not None else None
    except (TypeError, ValueError):
        branch_scope = None

    targets = get_eligible_campaign_recipients(db, branch_id=branch_scope)
    subject = title or "حملة من العائلة FAMILY"

    sent = 0
    failed = 0
    for cust in targets:
        em = (cust.get("email") or "").strip()
        if not em:
            continue
        body = build_campaign_email_body(
            message=message,
            image_url=image_raw,
            request_url_root=request_url_root,
            customer_name=str(cust.get("name") or ""),
            dialect=str(cust.get("dialect") or "default"),
            product_interest=str(cust.get("last_product_interest") or "") or None,
        )
        try:
            ok = send_email(em, subject, body)
            if ok:
                sent += 1
                try:
                    cid = int(cust["id"])
                    db.customer_mark_last_campaign_sent(cid)
                except (KeyError, TypeError, ValueError):
                    pass
            else:
                failed += 1
        except Exception:
            failed += 1

    targeted = len(targets)
    mark_campaign_dispatch_completed(db, int(campaign_id))
    return {
        "ok": True,
        "campaign_id": int(campaign_id),
        "targeted": targeted,
        "sent": sent,
        "failed": failed,
    }


def send_campaign_now(
    db,
    *,
    title: str,
    message: str,
    whatsapp_message: Optional[str],
    image_url: Optional[str],
    branch_scope: Optional[int],
    created_by: str,
    request_url_root: str,
    scheduled_at: Optional[str] = None,
) -> Dict[str, Any]:
    cid = create_campaign(
        db,
        title=title,
        message=message,
        whatsapp_message=whatsapp_message,
        image_url=image_url,
        branch_id=branch_scope,
        created_by=created_by,
        scheduled_at=scheduled_at,
    )
    if cid is None:
        return {"ok": False, "error": "لم يُحفظ سجل الحملة.", "campaign_id": None}

    wa_targets = get_target_customers_with_phone(db, branch_id=branch_scope)

    if schedule_is_future(scheduled_at):
        return {
            "ok": True,
            "campaign_id": cid,
            "scheduled_only": True,
            "email_targets": 0,
            "whatsapp_targets": len(wa_targets),
            "emails_sent": 0,
            "emails_failed": 0,
        }

    send_result = send_campaign(db, cid, request_url_root)

    if not send_result.get("ok"):
        return {
            "ok": False,
            "error": send_result.get("error"),
            "campaign_id": cid,
            "email_targets": 0,
            "whatsapp_targets": len(wa_targets),
            "emails_sent": 0,
            "emails_failed": 0,
        }

    return {
        "ok": True,
        "campaign_id": cid,
        "email_targets": send_result["targeted"],
        "whatsapp_targets": len(wa_targets),
        "emails_sent": send_result["sent"],
        "emails_failed": send_result["failed"],
        "scheduled_only": False,
    }
