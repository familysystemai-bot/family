# logic/routes/admin_routes.py
# ─────────────────────────────────────────────────────────────────────
# Admin routes extracted from the monolithic app.py.
# Uses a register(app, db) pattern so ALL endpoint names are preserved
# and NO template changes are needed.
# ─────────────────────────────────────────────────────────────────────
import json
import logging
import os
import uuid

from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# ── CSRF ────────────────────────────────────────────────────────────
try:
    from logic.security import csrf_exempt
except ImportError:
    def csrf_exempt(fn):
        return fn

# ── helpers (session checks, decorators, delivery-images util) ─────
from logic.routes.helpers import (
    _session_admin_or_founder,
    _staff_session_ok,
    _session_branch_id_int,
    staff_member_required,
    _session_founder_only,
    _session_founder_or_admin,
    _persist_company_delivery_images_from_request,
)

# ── config ──────────────────────────────────────────────────────────
from config import (
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    FOUNDER_USERNAME,
    FOUNDER_PASSWORD,
    persist_admin_password_hashed,
    persist_founder_password_hashed,
    update_env_file,
    set_admin_username_runtime,
    set_founder_username_runtime,
    password_matches_stored,
)

# ── company-info repository ────────────────────────────────────────
from logic.company_info_repository import (
    ALLOWED_COMPANY_INFO_KEYS,
    parse_delivery_image_urls,
)

# ── AI usage tracker ───────────────────────────────────────────────
from logic.ai_usage_tracker import get_founder_accounting

# ── logger ──────────────────────────────────────────────────────────
try:
    from logic.logger_config import app_logger
    logger = app_logger
except ImportError:
    logger = logging.getLogger(__name__)


# ====================================================================
# register(app, db)  —  attach all admin routes to *app*
# ====================================================================
def register(app, db):
    """Register every admin-panel route on *app*.

    Each route is defined as a nested function so its endpoint name
    matches the original name in app.py (e.g. ``admin_dashboard``).
    """

    # ────────────────────────────────────────────────────────────────
    # /admin/dashboard
    # ────────────────────────────────────────────────────────────────
    @app.route('/admin/dashboard')
    def admin_dashboard():
        """لوحة المدير العام (ليست لوحة المؤسس)."""
        if not _staff_session_ok():
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            return redirect(url_for('dashboard'))
        main_cats = db.get_main_categories()
        branches = db.get_all_branches()
        branch_services_by_id = {}
        for r in db.list_branch_services_with_branches():
            bid = int(r["branch_id"])
            branch_services_by_id.setdefault(bid, []).append(r)
        ci_rows = db.get_all_company_info_rows()
        admin_pending_inquiries = db.get_all_pending_inquiries(limit=120)
        admin_all_inquiries_recent = db.list_recent_branch_inquiries_all(80)
        admin_product_requests = db.list_recent_product_requests(60)
        complaint_stats = db.get_complaints_stats()
        return render_template(
            "admin_dashboard.html",
            branches=branches,
            main_categories=main_cats,
            branch_services_by_id=branch_services_by_id,
            company_info_rows=ci_rows,
            admin_pending_inquiries=admin_pending_inquiries,
            admin_all_inquiries_recent=admin_all_inquiries_recent,
            admin_product_requests=admin_product_requests,
            complaint_stats=complaint_stats,
            delivery_images_json_text=json.dumps(
                parse_delivery_image_urls(ci_rows.get("delivery_images")),
                ensure_ascii=False,
            ),
        )

    # ────────────────────────────────────────────────────────────────
    # /admin/company-info
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/company-info", methods=["GET", "POST"])
    def admin_company_info():
        """قراءة/تحديث معلومات الشركة وخدمات الفروع (JSON أو نموذج لوحة الإدارة)."""
        if not _staff_session_ok():
            return jsonify({"error": "unauthorized"}), 401
        role = session.get("role")
        if request.method == "GET":
            if role == "admin":
                return jsonify(
                    {
                        "company_info": db.get_all_company_info_rows(),
                        "branch_services": db.list_branch_services_with_branches(),
                    }
                )
            if role == "branch":
                bid = _session_branch_id_int()
                if bid is None:
                    return jsonify({"error": "forbidden"}), 403
                filtered = [
                    r
                    for r in db.list_branch_services_with_branches()
                    if int(r["branch_id"]) == bid
                ]
                return jsonify(
                    {
                        "company_info": db.get_all_company_info_rows(),
                        "branch_services": filtered,
                    }
                )
            return jsonify({"error": "forbidden"}), 403
        if request.is_json:
            if role != "admin":
                return jsonify({"error": "forbidden"}), 403
            payload = request.get_json(silent=True) or {}
            ci = payload.get("company_info")
            if isinstance(ci, dict):
                db.bulk_set_company_info(
                    {k: str(v) if v is not None else "" for k, v in ci.items()}
                )
            bs = payload.get("branch_services")
            if isinstance(bs, dict):
                for bid_str, rows in bs.items():
                    try:
                        bid = int(bid_str)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(rows, list):
                        continue
                    pairs = []
                    for item in rows:
                        if not isinstance(item, dict):
                            continue
                        pairs.append(
                            (
                                str(item.get("title") or item.get("service_title") or ""),
                                str(item.get("details") or ""),
                            )
                        )
                    db.replace_branch_services(bid, pairs)
            return jsonify({"ok": True})
        if role == "admin":
            _persist_company_delivery_images_from_request(db)
            for k in ALLOWED_COMPANY_INFO_KEYS:
                if k == "delivery_images":
                    continue
                raw = request.form.get(k)
                if raw is not None:
                    db.set_company_info_key(k, raw)
            # لا تُفرغ خدمات الفروع إذا كان الطلب من نموذج معلومات الشركة فقط (بلا حقول b{id}_title)
            branch_svc_in_form = any(
                k.startswith("b") and "_title[]" in k for k in request.form.keys()
            )
            if branch_svc_in_form:
                for b in db.get_all_branches() or []:
                    try:
                        bid = int(b["id"])
                    except (TypeError, ValueError):
                        continue
                    titles = request.form.getlist(f"b{bid}_title[]")
                    details = request.form.getlist(f"b{bid}_details[]")
                    n = max(len(titles), len(details))
                    pairs = [
                        (titles[i] if i < len(titles) else "", details[i] if i < len(details) else "")
                        for i in range(n)
                    ]
                    db.replace_branch_services(bid, pairs)
            flash(
                "تم حفظ معلومات الشركة وخدمات الفروع."
                if branch_svc_in_form
                else "تم حفظ معلومات الشركة للشات.",
                "success",
            )
            return redirect(url_for("admin_dashboard"))
        if role == "founder":
            _persist_company_delivery_images_from_request(db)
            for k in ALLOWED_COMPANY_INFO_KEYS:
                if k == "delivery_images":
                    continue
                raw = request.form.get(k)
                if raw is not None:
                    db.set_company_info_key(k, raw)
            flash("تم حفظ معلومات الشركة للشات (روابط التواصل والمتجر وغيرها).", "success")
            return redirect(url_for("founder_dashboard"))
        if role == "branch":
            bid = _session_branch_id_int()
            if bid is None:
                flash("تعذر تحديد الفرع.", "danger")
                return redirect(url_for("dashboard"))
            titles = request.form.getlist(f"b{bid}_title[]")
            details = request.form.getlist(f"b{bid}_details[]")
            n = max(len(titles), len(details))
            pairs = [
                (titles[i] if i < len(titles) else "", details[i] if i < len(details) else "")
                for i in range(n)
            ]
            db.replace_branch_services(bid, pairs)
            flash("تم حفظ خدمات الفرع.", "success")
            return redirect(url_for("dashboard"))
        return jsonify({"error": "forbidden"}), 403

    # ────────────────────────────────────────────────────────────────
    # /admin/create_branch
    # ────────────────────────────────────────────────────────────────
    @app.route('/admin/create_branch', methods=['POST'])
    def create_branch():
        if _session_admin_or_founder():
            u_name = request.form.get('u_name')
            u_pass = request.form.get('u_pass')
            u_city = request.form.get('u_city')
            if u_name and u_pass and (u_city or "").strip():
                if db.create_new_branch(u_name, u_pass, u_city):
                    flash("تم إضافة الفرع بنجاح", "success")
                else:
                    flash("هذا الفرع موجود مسبقاً أو البيانات مكررة", "danger")
        if session.get("role") == "founder":
            return redirect(url_for("founder_branches"))
        return redirect(url_for('admin_dashboard'))

    # ────────────────────────────────────────────────────────────────
    # /admin/delete_branch/<int:b_id>
    # ────────────────────────────────────────────────────────────────
    @app.route('/admin/delete_branch/<int:b_id>')
    def delete_branch(b_id):
        if _session_admin_or_founder():
            db.delete_branch(b_id)
            flash("تم حذف الفرع نهائياً", "warning")
        if session.get("role") == "founder":
            return redirect(url_for("founder_branches"))
        return redirect(url_for('admin_dashboard'))

    # ────────────────────────────────────────────────────────────────
    # /admin/settings
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/settings", methods=["GET", "POST"])
    def admin_settings():
        if not _session_founder_only():
            return redirect(url_for("login"))
        if request.method == "POST":
            which = (request.form.get("which") or "").strip()
            if which == "admin":
                new_user = (request.form.get("admin_username") or "").strip()
                new_pw = (request.form.get("admin_new_password") or "").strip()
                confirm = (request.form.get("admin_confirm_password") or "").strip()
                if new_user and new_user != ADMIN_USERNAME:
                    try:
                        update_env_file("ADMIN_USERNAME", new_user)
                        set_admin_username_runtime(new_user)
                        flash("تم تحديث اسم مستخدم الإدارة.", "success")
                    except OSError:
                        flash("تعذر الكتابة على ملف .env.", "danger")
                    except ValueError as e:
                        flash(str(e) or "تعذر حفظ اسم المستخدم.", "danger")
                if new_pw or confirm:
                    if not new_pw or not confirm:
                        flash("لتغيير كلمة مرور الإدارة: املأ الحقلين أو اتركها فارغة.", "warning")
                    elif new_pw != confirm:
                        flash("تأكيد كلمة مرور الإدارة لا يطابق.", "danger")
                    else:
                        try:
                            persist_admin_password_hashed(new_pw)
                            flash("تم تحديث كلمة مرور الإدارة (مُشفّرة) في ملف البيئة.", "success")
                        except OSError:
                            flash("تعذر الكتابة على ملف .env.", "danger")
                        except ValueError as e:
                            flash(str(e) or "تعذر حفظ كلمة المرور.", "danger")
            elif which == "founder":
                new_user = (request.form.get("founder_username") or "").strip()
                new_pw = (request.form.get("founder_new_password") or "").strip()
                confirm = (request.form.get("founder_confirm_password") or "").strip()
                if new_user and new_user != FOUNDER_USERNAME:
                    try:
                        update_env_file("FOUNDER_USERNAME", new_user)
                        set_founder_username_runtime(new_user)
                        session["username"] = new_user
                        flash("تم تحديث اسم مستخدم المؤسس.", "success")
                    except OSError:
                        flash("تعذر الكتابة على ملف .env.", "danger")
                    except ValueError as e:
                        flash(str(e) or "تعذر حفظ اسم المستخدم.", "danger")
                if new_pw or confirm:
                    if not new_pw or not confirm:
                        flash("لتغيير كلمة مرور المؤسس: املأ الحقلين أو اتركها فارغة.", "warning")
                    elif new_pw != confirm:
                        flash("تأكيد كلمة مرور المؤسس لا يطابق.", "danger")
                    else:
                        try:
                            persist_founder_password_hashed(new_pw)
                            flash("تم تحديث كلمة مرور المؤسس (مُشفّرة) في ملف البيئة.", "success")
                        except OSError:
                            flash("تعذر الكتابة على ملف .env.", "danger")
                        except ValueError as e:
                            flash(str(e) or "تعذر حفظ كلمة المرور.", "danger")
            else:
                flash("طلب غير صالح.", "warning")
            return redirect(url_for("admin_settings"))
        return render_template(
            "admin_settings.html",
            admin_username=ADMIN_USERNAME,
            founder_username=FOUNDER_USERNAME,
        )

    # ────────────────────────────────────────────────────────────────
    # /admin/users
    # ────────────────────────────────────────────────────────────────
    @app.route('/admin/users')
    def admin_users():
        if not _staff_session_ok():
            return redirect(url_for('login'))
        if not _session_admin_or_founder():
            return redirect(url_for('dashboard'))
        users = db.get_branch_users()
        return render_template('admin_users.html', users=users)

    # ────────────────────────────────────────────────────────────────
    # /admin/update_user/<int:branch_id>
    # ────────────────────────────────────────────────────────────────
    @app.route('/admin/update_user/<int:branch_id>', methods=['POST'])
    def admin_update_user(branch_id: int):
        if not _staff_session_ok():
            return redirect(url_for('login'))
        if not _session_admin_or_founder():
            return redirect(url_for('dashboard'))

        username = request.form.get('username')
        password = request.form.get('password')
        if db.update_branch_user(branch_id, username=username, password=password):
            if (password or "").strip():
                flash("تم تحديث اسم المستخدم وإعادة تعيين كلمة المرور.", "success")
            else:
                flash("تم تحديث اسم المستخدم.", "success")
        else:
            flash("تعذر تحديث المستخدم.", "warning")
        return redirect(url_for('admin_users'))

    # ────────────────────────────────────────────────────────────────
    # /admin/complaints
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/complaints")
    def admin_complaints():
        if not _staff_session_ok():
            return redirect(url_for("login"))
        if not _session_admin_or_founder():
            return redirect(url_for("dashboard"))

        branch_filter = (request.args.get("branch") or "").strip()
        status_filter = (request.args.get("status") or "").strip().lower()
        if status_filter not in ("", "open", "resolved"):
            status_filter = ""

        stats = db.get_complaints_stats()
        branch_options = db.list_complaints_branch_filter_options()
        complaints_list = db.get_complaints(
            branch_name=branch_filter or None,
            status=status_filter or None,
            limit=800,
        )
        stats_by_category = db.get_complaints_by_category(15)
        stats_by_branch = db.get_complaints_by_branch(15)
        stats_by_employee = db.get_complaints_by_employee(15)
        return render_template(
            "admin_complaints.html",
            complaints=complaints_list,
            stats=stats,
            filter_branch=branch_filter,
            filter_status=status_filter,
            branch_options=branch_options,
            stats_by_category=stats_by_category,
            stats_by_branch=stats_by_branch,
            stats_by_employee=stats_by_employee,
        )

    # ────────────────────────────────────────────────────────────────
    # /admin/complaints/<int:complaint_id>/resolve
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/complaints/<int:complaint_id>/resolve", methods=["POST"])
    def admin_resolve_complaint(complaint_id: int):
        if not _staff_session_ok():
            return redirect(url_for("login"))
        if not _session_admin_or_founder():
            return redirect(url_for("dashboard"))

        fb = (request.form.get("filter_branch") or "").strip()
        fs = (request.form.get("filter_status") or "").strip()

        notes = (request.form.get("resolution_notes") or "").strip()
        if complaint_id and db.resolve_complaint(complaint_id, resolution_notes=notes):
            flash("تم تسجيل حل الشكوى.", "success")
        else:
            flash("تعذر التحديث أو الشكوى محلولة مسبقاً.", "warning")

        params = {}
        if fb:
            params["branch"] = fb
        if fs in ("open", "resolved"):
            params["status"] = fs
        return redirect(url_for("admin_complaints", **params))

    # ────────────────────────────────────────────────────────────────
    # /api/analytics/daily-line
    # ────────────────────────────────────────────────────────────────
    @app.route("/api/analytics/daily-line")
    def api_daily_chat_line():
        """سلسلة يومية لتفاعلات الشات — لوحة المؤسس (الرسم البياني)."""
        if not _session_founder_only():
            return jsonify({"error": "forbidden"}), 403
        raw = (request.args.get("days") or "30").strip()
        try:
            nd = int(raw)
        except ValueError:
            nd = 30
        return jsonify(db.get_daily_chat_series(days=nd))

    # ────────────────────────────────────────────────────────────────
    # /api/analytics/trends
    # ────────────────────────────────────────────────────────────────
    @app.route("/api/analytics/trends")
    @staff_member_required
    def api_trend_analytics():
        """تحليلات من trend_data — للفرع: يُفلتر حسب branch_id في الجلسة."""
        scope = None
        if session.get("role") == "branch":
            bid = session.get("branch_id")
            try:
                scope = int(bid) if bid is not None else None
            except (TypeError, ValueError):
                scope = None
        data = db.get_trend_analytics_snapshot(branch_scope=scope, limit=14)
        return jsonify(data)

    # ────────────────────────────────────────────────────────────────
    # /admin/diagnostics/email
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/diagnostics/email")
    @staff_member_required
    def admin_email_diagnostics():
        """تشخيص إعدادات البريد (JSON) للمستخدمين المخوّلين فقط."""
        from logic.email_diagnostics import run_email_diagnostics

        return jsonify(run_email_diagnostics(db))

    # ────────────────────────────────────────────────────────────────
    # /admin/diagnostics/full
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/diagnostics/full")
    @staff_member_required
    def admin_full_diagnostics():
        """تشخيص شامل للمشروع (قراءة فقط) للمستخدمين المخوّلين فقط."""
        from logic.project_diagnostics import run_full_diagnostics

        return jsonify(run_full_diagnostics(db, send_alerts=True))

    # ────────────────────────────────────────────────────────────────
    # /admin/api/image-analysis/status
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/api/image-analysis/status", methods=["GET"])
    def admin_image_analysis_status():
        if session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        try:
            from logic.integrations.base import read_setting as _rs
            enabled = (_rs("image_analysis_enabled", "0") or "0").strip()
            provider = (_rs("image_analysis_provider", "gemini") or "gemini").strip()
            has_gemini_key = bool((_rs("GEMINI_API_KEY", "") or "").strip())
            has_openai_key = bool((_rs("OPENAI_API_KEY", "") or "").strip())
            return jsonify({
                "ok": True,
                "enabled": enabled == "1",
                "provider": provider,
                "has_gemini_key": has_gemini_key,
                "has_openai_key": has_openai_key,
            })
        except Exception:
            logger.exception("admin_image_analysis_status error")
            return jsonify({"ok": False, "error": "خطأ داخلي"}), 500

    # ────────────────────────────────────────────────────────────────
    # /admin/api/image-analysis/toggle
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/api/image-analysis/toggle", methods=["POST"])
    def admin_image_analysis_toggle():
        if session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        try:
            from logic.integrations.base import write_setting as _ws, read_setting as _rs
            data = request.get_json(silent=True) or {}
            enable = data.get("enable")
            if enable is None:
                current = (_rs("image_analysis_enabled", "0") or "0").strip()
                enable = current != "1"
            new_val = "1" if enable else "0"
            _ws("image_analysis_enabled", new_val)
            return jsonify({"ok": True, "enabled": new_val == "1"})
        except Exception:
            logger.exception("admin_image_analysis_toggle error")
            return jsonify({"ok": False, "error": "خطأ داخلي"}), 500

    # ────────────────────────────────────────────────────────────────
    # /admin/api/image-analysis/save
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/api/image-analysis/save", methods=["POST"])
    def admin_image_analysis_save():
        if session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        try:
            from logic.integrations.base import write_setting as _ws
            data = request.get_json(silent=True) or {}
            provider = (data.get("provider") or "gemini").strip().lower()
            api_key = (data.get("api_key") or "").strip()
            if provider not in ("gemini", "openai"):
                return jsonify({"ok": False, "error": "مزود غير مدعوم (gemini أو openai)"}), 400
            _ws("image_analysis_provider", provider)
            if api_key:
                setting_key = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
                _ws(setting_key, api_key)
            return jsonify({"ok": True})
        except Exception:
            logger.exception("admin_image_analysis_save error")
            return jsonify({"ok": False, "error": "خطأ داخلي"}), 500

    # ────────────────────────────────────────────────────────────────
    # /admin/api/image-analysis/test
    # ────────────────────────────────────────────────────────────────
    @app.route("/admin/api/image-analysis/test", methods=["POST"])
    def admin_image_analysis_test():
        if session.get("role") not in ("admin", "founder"):
            return jsonify({"ok": False, "error": "غير مصرح"}), 403
        try:
            from logic.integrations.base import read_setting as _rs
            provider = (_rs("image_analysis_provider", "gemini") or "gemini").strip().lower()
            if provider == "gemini":
                key = (_rs("GEMINI_API_KEY", "") or "").strip()
                if not key:
                    return jsonify({"ok": False, "error": "مفتاح Gemini غير مضبوط"}), 400
                import requests as _req
                resp = _req.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}",
                    json={"contents": [{"parts": [{"text": "ping"}]}]},
                    timeout=10,
                )
                if resp.ok:
                    return jsonify({"ok": True, "message": "Gemini: اتصال ناجح ✅"})
                return jsonify({"ok": False, "error": f"Gemini: {resp.status_code} — {resp.text[:200]}"}), 400
            else:
                key = (_rs("OPENAI_API_KEY", "") or "").strip()
                if not key:
                    return jsonify({"ok": False, "error": "مفتاح OpenAI غير مضبوط"}), 400
                try:
                    from openai import OpenAI as _OAI
                    client = _OAI(api_key=key)
                    client.models.list()
                    return jsonify({"ok": True, "message": "OpenAI: اتصال ناجح ✅"})
                except Exception as _e:
                    return jsonify({"ok": False, "error": f"OpenAI: {str(_e)[:200]}"}), 400
        except Exception:
            logger.exception("admin_image_analysis_test error")
            return jsonify({"ok": False, "error": "خطأ داخلي"}), 500
