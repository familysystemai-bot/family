"""
تشخيص إعدادات البريد ومسار مستلمي الشكاوى (بدون إرسال فعلي).
"""
from __future__ import annotations

import os
import smtplib
import traceback
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from config import (
    ADMIN_EMAIL,
    BASE_DIR,
    MAIN_RECEIVER_EMAIL,
    SENDER_EMAIL,
)
from site_config.branches import get_branch, get_management_emails

if TYPE_CHECKING:
    from logic.database import DatabaseManager

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587
MIN_APP_PASSWORD_LEN = 12


def _env_file_exists() -> bool:
    return (BASE_DIR / ".env").is_file()


def _password_status_and_secret() -> Tuple[str, Optional[str]]:
    """
    يُرجع (password_status, password_for_login).
    password_for_login = None إذا لا يجب محاولة SMTP.
    """
    raw = os.getenv("SENDER_PASSWORD")
    if raw is None or raw == "":
        return "empty", None
    if raw != raw.strip():
        return "invalid_format", None
    stripped = raw.strip()
    if len(stripped) < MIN_APP_PASSWORD_LEN:
        return "invalid_format", None
    return "ok", stripped


def _smtp_login_starttls(
    user: str, password: str
) -> Tuple[str, str, Optional[str]]:
    """
    اتصال SMTP حقيقي: المنفذ 587 + STARTTLS + login فقط.
    يُرجع (smtp_connection, smtp_error, exception_type_name).
    """
    try:
        with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=25) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(user, password)
        return "success", "", None
    except smtplib.SMTPAuthenticationError as e:
        return "failed", _full_error_text(e), type(e).__name__
    except smtplib.SMTPException as e:
        return "failed", _full_error_text(e), type(e).__name__
    except OSError as e:
        return "failed", _full_error_text(e), type(e).__name__
    except Exception as e:
        return "failed", _full_error_text(e), type(e).__name__


def _full_error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"


def _likely_issue(
    password_status: str,
    smtp_connection: str,
    smtp_error: str,
    exc_type: Optional[str],
    main_ok: bool,
    branches_without_email: List[str],
) -> str:
    parts: List[str] = []

    if password_status == "empty":
        parts.append(
            "SENDER_PASSWORD غير معرّف أو فارغ — عيّن App Password من حساب Google."
        )
    elif password_status == "invalid_format":
        parts.append(
            "تنسيق كلمة المرور غير مقبول: لا مسافات في البداية/النهاية، والطول يفضّل ≥ 16 (App Password لـ Gmail)."
        )

    if smtp_connection == "failed" and smtp_error:
        low = smtp_error.lower()
        if exc_type == "SMTPAuthenticationError" or "535" in smtp_error or "534" in smtp_error:
            parts.append(
                "فشل المصادقة مع Gmail: غالباً تحتاج «كلمة مرور التطبيقات» مع تفعيل التحقق بخطوتين، أو البريد/الكلمة خاطئة."
            )
        elif "connection" in low or "timed out" in low or "errno" in low:
            parts.append(
                "مشكلة شبكة أو جدار ناري أو منفذ 587 غير متاح من بيئة التشغيل."
            )
        else:
            parts.append("فشل جلسة SMTP — راجع نص الخطأ أعلاه.")

    if not main_ok:
        parts.append(
            "MAIN_RECEIVER_EMAIL غير مضبوط أو فارغ — لن يصل صندوق الشكاوى الرئيسي."
        )
    if branches_without_email:
        parts.append(
            f"عدد {len(branches_without_email)} فرع(فروع) بلا complaint_email في قاعدة البيانات."
        )

    if not parts:
        return "لا توجد مؤشرات فادحة؛ إن كان الإرسال يفشل في التطبيق راجع قيود Gmail أو المستلمين."
    return " ".join(parts)


def build_complaint_recipients_example(db: "DatabaseManager") -> List[str]:
    """نفس منطق _send_complaint_email لفرع افتراضي (فرع جدة) — بدون إرسال."""
    branch_label = "فرع جدة"
    recipients: List[str] = []
    if MAIN_RECEIVER_EMAIL:
        recipients.append(MAIN_RECEIVER_EMAIL.strip())

    branch_id = db.get_branch_id_by_city_name(branch_label)
    branch_email: Optional[str] = None
    if branch_id is not None:
        branch_email = db.get_branch_complaint_email(branch_id)
    if not branch_email and branch_label:
        contact = get_branch(branch_label) or {}
        branch_email = (contact.get("manager_email") or "").strip() or None

    if branch_email:
        recipients.append(branch_email)

    recipients.extend(get_management_emails())
    return list(dict.fromkeys([r for r in recipients if r]))


def run_email_diagnostics(db: "DatabaseManager") -> Dict[str, Any]:
    sender = (SENDER_EMAIL or "").strip() or None
    password_status, pwd_secret = _password_status_and_secret()

    smtp_error = ""
    smtp_connection = "failed"
    exc_type: Optional[str] = None

    if password_status != "ok" or not sender:
        smtp_connection = "failed"
        if not sender:
            smtp_error = "SENDER_EMAIL غير مضبوط أو فارغ — لا يمكن اختبار SMTP."
        elif password_status == "empty":
            smtp_error = "SENDER_PASSWORD فارغ — لم يُجرَ اتصال SMTP."
        else:
            smtp_error = (
                "تنسيق كلمة المرور غير صالح (مسافات أو طول قصير) — لم يُجرَ اتصال SMTP."
            )
    else:
        smtp_connection, smtp_error, exc_type = _smtp_login_starttls(sender, pwd_secret or "")

    main_raw = (MAIN_RECEIVER_EMAIL or "").strip()
    main_ok = bool(main_raw)
    main_email = main_raw or None

    branches = db.list_branches_complaint_emails()
    branches_without_email = [
        b["branch"]
        for b in branches
        if b.get("branch") and not (b.get("email") or "").strip()
    ]

    likely = _likely_issue(
        password_status,
        smtp_connection,
        smtp_error,
        exc_type,
        main_ok,
        branches_without_email,
    )

    return {
        "env_file_exists": _env_file_exists(),
        "sender_email": sender,
        "password_status": password_status,
        "password_length": len((os.getenv("SENDER_PASSWORD") or "").strip())
        if os.getenv("SENDER_PASSWORD")
        else 0,
        "smtp_connection": smtp_connection,
        "smtp_error": smtp_error if smtp_error else None,
        "smtp_exception_type": exc_type,
        "likely_issue": likely,
        "main_email": main_email,
        "main_receiver_configured": main_ok,
        "admin_email": (ADMIN_EMAIL or "").strip() or None,
        "branches": branches,
        "branches_without_email": branches_without_email,
        "branches_with_email_count": sum(
            1 for b in branches if (b.get("email") or "").strip()
        ),
        "recipients_example": build_complaint_recipients_example(db),
    }
