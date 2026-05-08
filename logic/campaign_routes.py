# -*- coding: utf-8 -*-
"""
مسارات الحملات الإعلانية — يُسجَّل الـ Blueprint من app.py بسطر واحد.
"""
from __future__ import annotations

from typing import Optional

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from logic import campaign_service as camp_svc
from logic.media_uploads import file_storage_to_upload


def _save_campaign_image(file_storage) -> Optional[str]:
    if not file_storage or not getattr(file_storage, "filename", None):
        return None
    ref, _err = file_storage_to_upload(
        file_storage, folder="campaigns", require_validation=True
    )
    return ref


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
        wa_sent = int(result.get("wa_sent") or 0)
        wa_targeted = int(result.get("wa_targeted") or 0)
        wa_failed = int(result.get("wa_failed") or 0)
        if targeted:
            flash(
                f"البريد: إرسال ناجح {sent} من {targeted}"
                + (f" — فشل: {failed}" if failed else ""),
                "success" if sent or not failed else "warning",
            )
        elif failed:
            flash(f"البريد: فشل إرسال {failed} رسالة.", "warning")
        if wa_targeted:
            flash(
                f"واتساب: إرسال ناجح {wa_sent} من {wa_targeted}"
                + (f" — فشل: {wa_failed}" if wa_failed else ""),
                "success" if wa_sent or not wa_failed else "warning",
            )
        if not targeted and not wa_targeted and not failed and not wa_failed:
            flash(
                "لا يوجد مستهدفون مؤهلون حالياً (بريد/هاتف موافق + مرور 24 ساعة على آخر حملة).",
                "info",
            )

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

        if not email_message and not image_url and not whatsapp_message:
            flash(
                "أضف نص البريد أو نص واتساب أو صورة لإنشاء الحملة.",
                "warning",
            )
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
                f"تم جدولة الحملة #{result.get('campaign_id')} — سيُرسل البريد وواتساب (إن وُجد نص) تلقائياً في الوقت المحدد (مع احترام فترة 24 ساعة وترتيب الأولوية).",
                "success",
            )
            return redirect(url_for(redirect_endpoint))

        et = int(result.get("email_targets", 0) or 0)
        es = int(result.get("emails_sent", 0) or 0)
        ef = int(result.get("emails_failed", 0) or 0)
        wt = int(result.get("whatsapp_targets", 0) or 0)
        ws = int(result.get("wa_sent", 0) or 0)
        wf = int(result.get("wa_failed", 0) or 0)
        if et:
            flash(
                f"البريد: تم الإرسال إلى {es} من {et}"
                + (f" — فشل: {ef}" if ef else ""),
                "success" if es or not ef else "warning",
            )
        if wt:
            flash(
                f"واتساب: تم الإرسال إلى {ws} من {wt}"
                + (f" — فشل: {wf}" if wf else ""),
                "success" if ws or not wf else "warning",
            )
        if not et and not wt:
            flash(
                "لا مستهدفون مؤهلون لهذه الحملة في الوقت الحالي.",
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
