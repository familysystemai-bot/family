# -*- coding: utf-8 -*-
"""
مسارات الحملات الإعلانية — يُسجَّل الـ Blueprint من app.py بسطر واحد.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from config import UPLOAD_FOLDER
from logic import campaign_service as camp_svc


def _allowed_image(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in {"png", "jpg", "jpeg", "gif", "webp"}


def _save_campaign_image(file_storage) -> Optional[str]:
    if not file_storage or not getattr(file_storage, "filename", None):
        return None
    if not _allowed_image(file_storage.filename):
        return None
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    unique = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    dest = os.path.join(UPLOAD_FOLDER, unique)
    file_storage.save(dest)
    return f"uploads/{unique}"


def _can_actor_send_campaign(
    *, role: str, branch_id_session: Optional[int], campaign_row: Optional[dict]
) -> bool:
    if not campaign_row:
        return False
    if role in ("admin", "founder"):
        return True
    if role != "branch":
        return False
    cb = campaign_row.get("branch_id")
    if cb is None:
        return False
    try:
        return int(cb) == int(branch_id_session)
    except (TypeError, ValueError):
        return False


def create_campaign_blueprint(db) -> Blueprint:
    bp = Blueprint("campaigns", __name__, url_prefix="")

    def _flash_send_result(result: dict) -> None:
        if not result.get("ok"):
            flash(result.get("error") or "تعذر إرسال الحملة.", "danger")
            return
        sent = int(result.get("sent") or 0)
        targeted = int(result.get("targeted") or 0)
        failed = int(result.get("failed") or 0)
        flash(f"تم إرسال الحملة إلى {sent} عميل", "success")
        if targeted and sent < targeted:
            flash(f"مستهدفون بالبريد: {targeted} — نجح الإرسال: {sent} — فشل: {failed}", "info")
        elif failed:
            flash(f"ملاحظة: فشل إرسال {failed} رسالة.", "warning")

    def _handle_send_existing(
        *,
        role: str,
        branch_id_session: Optional[int],
        redirect_endpoint: str,
        url_root: str,
    ):
        raw = request.form.get("campaign_id")
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            flash("معرّف حملة غير صالح.", "warning")
            return redirect(url_for(redirect_endpoint))

        camp = camp_svc.get_campaign_by_id(db, cid)
        if not _can_actor_send_campaign(
            role=role, branch_id_session=branch_id_session, campaign_row=camp
        ):
            flash("غير مصرح بإرسال هذه الحملة.", "danger")
            return redirect(url_for(redirect_endpoint))

        result = camp_svc.send_campaign(db, cid, url_root)
        _flash_send_result(result)
        return redirect(url_for(redirect_endpoint))

    def _handle_create_and_send(
        *,
        role: str,
        branch_scope: Optional[int],
        redirect_endpoint: str,
        url_root: str,
    ):
        title = (request.form.get("title") or "").strip()
        email_message = (request.form.get("email_message") or "").strip()
        whatsapp_message = (request.form.get("whatsapp_message") or "").strip()
        image_url = _save_campaign_image(request.files.get("image"))
        send_timing = (request.form.get("send_timing") or "now").strip()
        if send_timing == "later":
            scheduled_norm = camp_svc.parse_schedule_input(
                request.form.get("scheduled_at")
            )
            if not scheduled_norm:
                flash("حدد تاريخ ووقت الإرسال عند اختيار «لاحقاً».", "warning")
                return redirect(url_for(redirect_endpoint))
        else:
            scheduled_norm = None

        if not title:
            flash("أدخل عنواناً للحملة.", "warning")
            return redirect(url_for(redirect_endpoint))

        if not email_message and not image_url:
            flash("أضف نص البريد أو صورة لإنشاء الحملة.", "warning")
            return redirect(url_for(redirect_endpoint))

        result = camp_svc.send_campaign_now(
            db,
            title=title,
            message=email_message,
            whatsapp_message=whatsapp_message or None,
            image_url=image_url,
            branch_scope=branch_scope,
            created_by=role,
            request_url_root=url_root,
            scheduled_at=scheduled_norm,
        )

        if not result.get("ok"):
            flash(result.get("error") or "تعذر حفظ الحملة.", "danger")
            return redirect(url_for(redirect_endpoint))

        if result.get("scheduled_only"):
            flash(
                f"تم جدولة الحملة #{result.get('campaign_id')} — سيُرسل البريد تلقائياً في الوقت المحدد (مع احترام فترة عدم إزعاج 24 ساعة وترتيب الأولوية).",
                "success",
            )
            if whatsapp_message:
                flash(
                    "نص الواتساب محفوظ مع الحملة فقط (لا إرسال واتساب حالياً).",
                    "info",
                )
            return redirect(url_for(redirect_endpoint))

        flash(
            f"تم إرسال الحملة إلى {result.get('emails_sent', 0)} عميل",
            "success",
        )
        if result.get("emails_failed"):
            flash(
                f"مستهدفون: {result.get('email_targets', 0)} — فشل جزئي: {result.get('emails_failed')}",
                "warning",
            )
        if whatsapp_message:
            flash(
                "نص الواتساب محفوظ مع الحملة فقط (لا إرسال واتساب حالياً).",
                "info",
            )
        return redirect(url_for(redirect_endpoint))

    @bp.route("/branch/campaigns", methods=["GET", "POST"])
    def branch_campaigns():
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

        if request.method == "POST":
            if request.form.get("action") == "send":
                return _handle_send_existing(
                    role="branch",
                    branch_id_session=bid_int,
                    redirect_endpoint="campaigns.branch_campaigns",
                    url_root=request.url_root,
                )
            return _handle_create_and_send(
                role="branch",
                branch_scope=bid_int,
                redirect_endpoint="campaigns.branch_campaigns",
                url_root=request.url_root,
            )

        campaigns = camp_svc.get_campaigns(db, branch_id=bid_int)
        return render_template(
            "campaigns/branch_manage.html",
            campaigns=campaigns,
            branch_scope=bid_int,
        )

    @bp.route("/admin/campaigns", methods=["GET", "POST"])
    def admin_campaigns():
        if "logged_in" not in session or session.get("role") != "admin":
            flash("غير مصرح.", "danger")
            return redirect(url_for("login"))

        if request.method == "POST":
            if request.form.get("action") == "send":
                return _handle_send_existing(
                    role="admin",
                    branch_id_session=None,
                    redirect_endpoint="campaigns.admin_campaigns",
                    url_root=request.url_root,
                )
            return _handle_create_and_send(
                role="admin",
                branch_scope=None,
                redirect_endpoint="campaigns.admin_campaigns",
                url_root=request.url_root,
            )

        campaigns = camp_svc.get_campaigns(db, branch_id=None)
        return render_template(
            "campaigns/admin_manage.html",
            campaigns=campaigns,
            branch_scope=None,
        )

    @bp.route("/founder/campaigns", methods=["GET", "POST"])
    def founder_campaigns():
        if "logged_in" not in session or session.get("role") != "founder":
            flash("غير مصرح.", "danger")
            return redirect(url_for("login"))

        if request.method == "POST":
            if request.form.get("action") == "send":
                return _handle_send_existing(
                    role="founder",
                    branch_id_session=None,
                    redirect_endpoint="campaigns.founder_campaigns",
                    url_root=request.url_root,
                )
            return _handle_create_and_send(
                role="founder",
                branch_scope=None,
                redirect_endpoint="campaigns.founder_campaigns",
                url_root=request.url_root,
            )

        campaigns = camp_svc.get_campaigns(db, branch_id=None)
        return render_template(
            "campaigns/founder_manage.html",
            campaigns=campaigns,
            branch_scope=None,
        )

    return bp
