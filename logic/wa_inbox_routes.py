# -*- coding: utf-8 -*-
"""
لوحة وارد واتساب — عرض المحادثات وإرسال ردود (فرع / إدارة).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from logic.wa_credentials import (
    meta_graph_token_error_hint,
    wa_access_token,
    wa_phone_number_id,
)
from logic.integrations.whatsapp_meta import (
    send_test_text_message,
    send_whatsapp_image_link,
)
from logic.ai_usage_tracker import get_wa_contact_ai_usage_map
from logic.wa_inbox_repository import normalize_wa_contact_number

logger = logging.getLogger(__name__)


def _public_static_upload_url(uploads_subpath: str) -> str:
    """رابط https عام لملف تحت static/uploads (مطلوب لميتا)."""
    from config import PUBLIC_BASE_URL

    p = (uploads_subpath or "").lstrip("/").replace("\\", "/")
    if not p.startswith("uploads/"):
        p = "uploads/" + p
    base = (PUBLIC_BASE_URL or "").strip().rstrip("/")
    if base:
        return f"{base}/static/{p}"
    return url_for("static", filename=p, _external=True)


def _branch_may_use_contact(db, contact_digits: str, branch_id: int) -> bool:
    """يمنع فرعاً من إرسال أو قراءة محادثة خارج نطاقه."""
    cust = db.get_customer_by_phone(contact_digits)
    if cust and cust.get("branch_id") is not None:
        try:
            if int(cust["branch_id"]) == int(branch_id):
                return True
        except (TypeError, ValueError):
            pass
    # محادثات بدون عميل مربوط: السماح إن وردت لنفس الفرع في الجدول
    th = db.wa_inbox_list_threads(int(branch_id))
    for row in th:
        if normalize_wa_contact_number(str(row.get("contact_number") or "")) == contact_digits:
            return True
    return False


def _resolve_outbound_branch_id(
    db,
    *,
    role: str,
    session_branch_id: Optional[int],
    contact_digits: str,
) -> Optional[int]:
    if role == "branch" and session_branch_id is not None:
        return int(session_branch_id)
    inferred = db.wa_inbox_infer_branch_for_contact(contact_digits)
    if inferred is not None:
        return inferred
    cust = db.get_customer_by_phone(contact_digits)
    if cust and cust.get("branch_id") is not None:
        try:
            return int(cust["branch_id"])
        except (TypeError, ValueError):
            pass
    return None


def create_wa_inbox_blueprint(db) -> Blueprint:
    bp = Blueprint("wa_inbox", __name__)

    def _threads_with_ai_usage(rows):
        usage = get_wa_contact_ai_usage_map(days=90)
        out = []
        for row in rows or []:
            r = dict(row)
            cn = normalize_wa_contact_number(str(r.get("contact_number") or ""))
            u = usage.get(cn) if cn else {}
            if not u:
                u = {}
            r["ai_calls"] = int(u.get("ai_calls") or 0)
            r["ai_prompt_tokens"] = int(u.get("ai_prompt_tokens") or 0)
            r["ai_completion_tokens"] = int(u.get("ai_completion_tokens") or 0)
            r["ai_total_tokens"] = int(u.get("ai_total_tokens") or 0)
            out.append(r)
        return out

    # ─── صفحات HTML ─────────────────────────────────────────────

    @bp.route("/admin/messages", methods=["GET"])
    def admin_messages_page():
        if "logged_in" not in session or session.get("role") not in ("admin", "founder"):
            flash("غير مصرح.", "danger")
            return redirect(url_for("login"))
        return render_template(
            "wa_inbox/inbox.html",
            title="رسائل واتساب — الإدارة",
            role_scope="admin",
            api_threads=url_for("wa_inbox.api_threads_admin"),
            api_messages=url_for("wa_inbox.api_messages_admin"),
            api_send=url_for("wa_inbox.api_send_admin"),
            home_url=url_for("admin_dashboard"),
        )

    @bp.route("/branch/messages", methods=["GET"])
    def branch_messages_page():
        if "logged_in" not in session or session.get("role") != "branch":
            flash("غير مصرح.", "danger")
            return redirect(url_for("login"))
        bid = session.get("branch_id")
        try:
            bid_int = int(bid) if bid is not None else None
        except (TypeError, ValueError):
            bid_int = None
        if bid_int is None:
            flash("تعذر تحديد الفرع.", "danger")
            return redirect(url_for("dashboard"))
        return render_template(
            "wa_inbox/inbox.html",
            title="رسائل واتساب — الفرع",
            role_scope="branch",
            api_threads=url_for("wa_inbox.api_threads_branch"),
            api_messages=url_for("wa_inbox.api_messages_branch"),
            api_send=url_for("wa_inbox.api_send_branch"),
            home_url=url_for("dashboard"),
        )

    # ─── API: قائمة المحادثات ───────────────────────────────────

    @bp.route("/admin/messages/api/threads", methods=["GET"])
    def api_threads_admin():
        if "logged_in" not in session or session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        rows = _threads_with_ai_usage(db.wa_inbox_list_threads(None))
        return jsonify({"ok": True, "threads": rows})

    @bp.route("/branch/messages/api/threads", methods=["GET"])
    def api_threads_branch():
        if "logged_in" not in session or session.get("role") != "branch":
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        bid = session.get("branch_id")
        try:
            bid_int = int(bid)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "فرع غير صالح"}), 400
        rows = _threads_with_ai_usage(db.wa_inbox_list_threads(bid_int))
        return jsonify({"ok": True, "threads": rows})

    # ─── API: رسائل محادثة ──────────────────────────────────────

    @bp.route("/admin/messages/api/messages", methods=["GET"])
    def api_messages_admin():
        if "logged_in" not in session or session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        contact = (request.args.get("contact") or "").strip()
        cn = normalize_wa_contact_number(contact)
        if not cn:
            return jsonify({"ok": False, "error": "رقم غير صالح"}), 400
        rows = db.wa_inbox_list_messages(cn, branch_id=None)
        return jsonify({"ok": True, "messages": rows})

    @bp.route("/branch/messages/api/messages", methods=["GET"])
    def api_messages_branch():
        if "logged_in" not in session or session.get("role") != "branch":
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        bid = session.get("branch_id")
        try:
            bid_int = int(bid)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "فرع غير صالح"}), 400
        contact = (request.args.get("contact") or "").strip()
        cn = normalize_wa_contact_number(contact)
        if not cn:
            return jsonify({"ok": False, "error": "رقم غير صالح"}), 400
        if not _branch_may_use_contact(db, cn, bid_int):
            return jsonify({"ok": False, "error": "غير مصرح بهذا الرقم"}), 403
        rows = db.wa_inbox_list_messages(cn, branch_id=bid_int)
        return jsonify({"ok": True, "messages": rows})

    def _do_send(
        *,
        role: str,
        session_branch_id: Optional[int],
        contact_raw: str,
        text: str,
        image_file: Optional[Any],
    ) -> Tuple[bool, str]:
        cn = normalize_wa_contact_number(contact_raw)
        if not cn:
            return False, "رقم العميل غير صالح."
        if role == "branch":
            if session_branch_id is None:
                return False, "فرع غير محدد."
            if not _branch_may_use_contact(db, cn, int(session_branch_id)):
                return False, "لا يمكنك مراسلة هذا الرقم من هذا الفرع."

        wa_pid = wa_phone_number_id().strip()
        wa_token = wa_access_token().strip()
        if not wa_pid or not wa_token:
            return False, "إعدادات واتساب غير مكتملة (WA_PHONE_NUMBER_ID / WA_ACCESS_TOKEN)."

        image_url_https: Optional[str] = None
        if image_file and getattr(image_file, "filename", None):
            try:
                from logic.security import validate_image_upload
            except ImportError:
                validate_image_upload = None  # type: ignore
            if validate_image_upload is None:
                return False, "التحقق من الصور غير متاح."
            ok_v, err_msg, data_bytes, mime_v = validate_image_upload(image_file)
            if not ok_v or not data_bytes:
                return False, err_msg or "ملف الصورة مرفوض."
            from logic.media_uploads import public_https_url, upload_image_bytes

            fn = secure_filename(image_file.filename or "img.jpg")
            stored = upload_image_bytes(
                data_bytes,
                fn,
                mime_v or "image/jpeg",
                folder="wa-outbound",
            )
            image_url_https = public_https_url(stored)
            if not image_url_https.startswith("https://"):
                image_url_https = _public_static_upload_url(
                    stored if stored.startswith("uploads/") else f"uploads/{stored.split('/')[-1]}"
                )

        display_name = db.wa_inbox_latest_display_name(cn) or ""
        bid = _resolve_outbound_branch_id(
            db,
            role=role,
            session_branch_id=session_branch_id,
            contact_digits=cn,
        )

        if image_url_https:
            ok, detail = send_whatsapp_image_link(
                wa_pid,
                wa_token,
                contact_raw,
                image_https_url=image_url_https,
                caption=text if text else None,
            )
            body_db = text.strip() if text else ""
            if body_db:
                body_db = f"{body_db}\n[صورة] {image_url_https}"
            else:
                body_db = f"[صورة] {image_url_https}"
        else:
            if not (text or "").strip():
                return False, "أدخل نصاً أو ارفق صورة."
            ok, detail = send_test_text_message(
                wa_pid, wa_token, contact_raw, body=text.strip()
            )
            body_db = text.strip()

        if not ok:
            err = detail if isinstance(detail, str) else "فشل الإرسال."
            return False, err + meta_graph_token_error_hint(err)

        db.wa_inbox_save_message(
            contact_number=cn,
            whatsapp_name=display_name or "—",
            message_body=body_db,
            direction="outbound",
            branch_id=bid,
        )
        return True, "تم الإرسال."

    @bp.route("/admin/messages/api/send", methods=["POST"])
    def api_send_admin():
        if "logged_in" not in session or session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        contact = (request.form.get("contact") or "").strip()
        text = (request.form.get("text") or "").strip()
        image_f = request.files.get("image")
        ok, msg = _do_send(
            role="admin",
            session_branch_id=None,
            contact_raw=contact,
            text=text,
            image_file=image_f,
        )
        if ok:
            return jsonify({"ok": True, "message": msg})
        return jsonify({"ok": False, "error": msg}), 400

    @bp.route("/branch/messages/api/send", methods=["POST"])
    def api_send_branch():
        if "logged_in" not in session or session.get("role") != "branch":
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        bid = session.get("branch_id")
        try:
            bid_int = int(bid)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "فرع غير صالح"}), 400
        contact = (request.form.get("contact") or "").strip()
        text = (request.form.get("text") or "").strip()
        image_f = request.files.get("image")
        ok, msg = _do_send(
            role="branch",
            session_branch_id=bid_int,
            contact_raw=contact,
            text=text,
            image_file=image_f,
        )
        if ok:
            return jsonify({"ok": True, "message": msg})
        return jsonify({"ok": False, "error": msg}), 400

    # ─── TASK 2: تحكم العميل (إيقاف AI / حظر) ────────────────────

    @bp.route("/admin/messages/api/contact-status", methods=["GET"])
    def api_contact_status_admin():
        if "logged_in" not in session or session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        contact = (request.args.get("contact") or "").strip()
        cn = normalize_wa_contact_number(contact)
        if not cn:
            return jsonify({"ok": False, "error": "رقم غير صالح"}), 400
        controls = db.wa_contact_get_controls(cn)
        return jsonify({"ok": True, "contact": cn, **controls})

    @bp.route("/admin/messages/api/contact-control", methods=["POST"])
    def api_contact_control_admin():
        if "logged_in" not in session or session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        data = request.get_json(silent=True) or {}
        contact = (data.get("contact") or "").strip()
        cn = normalize_wa_contact_number(contact)
        if not cn:
            return jsonify({"ok": False, "error": "رقم غير صالح"}), 400
        if len(cn) < 8:
            return jsonify(
                {
                    "ok": False,
                    "error": "رقم قصير جداً — تأكد من صيغة الرقم بدون مسافات أو رموز",
                }
            ), 400
        field = (data.get("field") or "").strip()
        if field not in ("ai_stopped", "banned"):
            return jsonify({"ok": False, "error": "حقل غير صالح"}), 400
        value = 1 if data.get("value") else 0
        ok = db.wa_contact_set_control(cn, field, value)
        if ok:
            controls = db.wa_contact_get_controls(cn)
            return jsonify({"ok": True, "contact": cn, **controls})
        return jsonify({"ok": False, "error": "فشل الحفظ"}), 500

    @bp.route("/branch/messages/api/contact-status", methods=["GET"])
    def api_contact_status_branch():
        if "logged_in" not in session or session.get("role") != "branch":
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        contact = (request.args.get("contact") or "").strip()
        cn = normalize_wa_contact_number(contact)
        if not cn:
            return jsonify({"ok": False, "error": "رقم غير صالح"}), 400
        controls = db.wa_contact_get_controls(cn)
        return jsonify({"ok": True, "contact": cn, **controls})

    @bp.route("/branch/messages/api/contact-control", methods=["POST"])
    def api_contact_control_branch():
        if "logged_in" not in session or session.get("role") != "branch":
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        data = request.get_json(silent=True) or {}
        contact = (data.get("contact") or "").strip()
        cn = normalize_wa_contact_number(contact)
        if not cn:
            return jsonify({"ok": False, "error": "رقم غير صالح"}), 400
        if len(cn) < 8:
            return jsonify(
                {
                    "ok": False,
                    "error": "رقم قصير جداً — تأكد من صيغة الرقم بدون مسافات أو رموز",
                }
            ), 400
        field = (data.get("field") or "").strip()
        if field not in ("ai_stopped", "banned"):
            return jsonify({"ok": False, "error": "حقل غير صالح"}), 400
        value = 1 if data.get("value") else 0
        ok = db.wa_contact_set_control(cn, field, value)
        if ok:
            controls = db.wa_contact_get_controls(cn)
            return jsonify({"ok": True, "contact": cn, **controls})
        return jsonify({"ok": False, "error": "فشل الحفظ"}), 500

    return bp
