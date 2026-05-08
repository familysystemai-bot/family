# -*- coding: utf-8 -*-
"""
رمز تحقق بريد لدخول واجهة الشات (OTP) — 4 أرقام، صلاحية 5 دقائق.

معطّل في التطبيق: دخول الشات أصبح مباشرة عبر /api/chat-login (بريد أو جوال) بدون OTP.
هذا الملف يُبقى للمرجع أو لاستخدام داخلي لاحق؛ لا يُستورد من app.py.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def is_plausible_email(email: str) -> bool:
    e = _normalize_email(email)
    if "@" not in e:
        return False
    left, right = e.rsplit("@", 1)
    return bool(left) and "." in right


def request_email_otp(db, email: str, name: str) -> Tuple[bool, str]:
    """يولد الرمز، يخزّنه، ويرسل البريد. يعيد (نجاح، رسالة خطأ)."""
    from logic.mail_service import send_email

    e = _normalize_email(email)
    n = (name or "").strip()[:200]
    if not is_plausible_email(e):
        return False, "يرجى إدخال بريد إلكتروني صالح."
    if len(n) < 2:
        return False, "يرجى إدخال الاسم."

    code = f"{random.randint(0, 9999):04d}"
    exp = datetime.now() + timedelta(minutes=5)
    exp_s = exp.strftime("%Y-%m-%d %H:%M:%S")

    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO email_verification_codes (email, code, name, expires_at, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(email) DO UPDATE SET
                code = excluded.code,
                name = excluded.name,
                expires_at = excluded.expires_at,
                created_at = datetime('now')
            """,
            (e, code, n, exp_s),
        )
        conn.commit()
    except sqlite3.Error:
        return False, "تعذر حفظ رمز التحقق."
    finally:
        conn.close()

    subj = "رمز التحقق — مجمع العائلة"
    body = f"رمز التحقق الخاص بك هو: {code}\n\nالصلاحية: 5 دقائق.\nإذا لم تطلب هذا الرمز فتجاهل الرسالة."
    if not send_email(e, subj, body):
        return False, "تعذر إرسال البريد. تحقق من إعدادات SMTP."

    return True, ""


def verify_email_otp(db, email: str, code: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """يعيد (نجاح، رسالة، {name} عند النجاح)."""
    e = _normalize_email(email)
    c = (code or "").strip().replace(" ", "")
    if not is_plausible_email(e) or not c.isdigit() or len(c) != 4:
        return False, "الكود غير صحيح", None

    conn = db._get_connection()
    try:
        cur = conn.execute(
            "SELECT code, name, expires_at FROM email_verification_codes WHERE email = ?",
            (e,),
        )
        row = cur.fetchone()
        if not row:
            return False, "الكود غير صحيح", None
        r = dict(row)
        if str(r.get("code")) != c:
            return False, "الكود غير صحيح", None
        exp = (r.get("expires_at") or "").strip()
        try:
            dt = datetime.strptime(exp[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False, "الكود غير صحيح", None
        if datetime.now() > dt:
            conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (e,))
            conn.commit()
            return False, "انتهت صلاحية الرمز. اطلب رمزاً جديداً.", None

        name = (r.get("name") or "").strip() or "ضيف"
        conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (e,))
        conn.commit()
        return True, "", {"email": e, "name": name}
    except sqlite3.Error:
        return False, "تعذر التحقق.", None
    finally:
        conn.close()
