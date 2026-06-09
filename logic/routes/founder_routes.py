"""
Founder routes — extracted from the monolithic app.py.

Uses the ``register(app, db)`` pattern (NOT Blueprints) so that every
``url_for()`` reference in the existing templates stays unchanged.
"""

import json

from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import (
    ADMIN_USERNAME,
    FOUNDER_PASSWORD,
    password_matches_stored,
    persist_admin_password_hashed,
    persist_founder_password_hashed,
)

from logic.company_info_repository import parse_delivery_image_urls
from logic.ai_usage_tracker import get_founder_accounting
from logic.site_logo import (
    FOUNDER_LOGO_CLOUD_ID_KEY,
    FOUNDER_LOGO_RELATIVE,
    FOUNDER_LOGO_SETTING_KEY,
    SITE_LOGO_CLOUD_ID_KEY,
    SITE_LOGO_RELATIVE,
    SITE_LOGO_SETTING_KEY,
    clear_branding_from_disk_and_settings,
    delete_remote_storage_public_id,
    remove_logo_file,
    save_png_bytes_to_upload_folder,
)
from logic.media_uploads import (
    collect_product_images_from_request,
    normalize_stored_media_ref,
    png_bytes_from_image_bytes,
    upload_branding_image_via_cloud_then_png,
)

from logic.routes.helpers import (
    _staff_session_ok,
    _session_founder_only,
    _session_founder_or_admin,
    _persist_company_delivery_images_from_request,
)


def register(app, db):
    """Register all founder routes on *app* using the shared *db* instance."""

    # ------------------------------------------------------------------
    # مسار مختصر للتقارير المالية
    # ------------------------------------------------------------------
    @app.route("/finance/gate")
    def finance_reports_gate():
        """مسار مختصر للتقارير المالية (خزنة PIN) للمؤسس فقط."""
        if not session.get("logged_in") or not _session_founder_only():
            flash("التقارير المالية متاحة للمؤسس بعد تسجيل الدخول.", "warning")
            return redirect(url_for("login"))
        return redirect(url_for("almanakh_finance.finance_gate"))

    # ------------------------------------------------------------------
    # لوحة تحكم المؤسس
    # ------------------------------------------------------------------
    @app.route("/founder/")
    @app.route("/founder/dashboard")
    def founder_dashboard():
        if not _session_founder_only():
            return redirect(url_for("login"))
        n_branches = len(db.get_all_branches() or [])
        n_products = db.count_products_total()
        st = db.get_complaints_stats()
        raw_prev = db.get_complaints(status="open", limit=80)
        complaints_preview = [
            c for c in raw_prev if c.get("branch_id") is not None
        ][:40]
        founder_inquiry_report = db.summarize_inquiries_by_branch()
        founder_product_request_total = db.count_product_requests()
        ci_rows = db.get_all_company_info_rows()
        founder_accounting = get_founder_accounting(days=30)
        return render_template(
            "founder/dashboard.html",
            n_branches=n_branches,
            n_products=n_products,
            n_complaints=st.get("total", 0),
            complaints_open=st.get("open", 0),
            complaints_preview=complaints_preview,
            founder_inquiry_report=founder_inquiry_report,
            founder_product_request_total=founder_product_request_total,
            company_info_rows=ci_rows,
            founder_accounting=founder_accounting,
            delivery_images_json_text=json.dumps(
                parse_delivery_image_urls(ci_rows.get("delivery_images")),
                ensure_ascii=False,
            ),
        )

    # ------------------------------------------------------------------
    # حل شكوى
    # ------------------------------------------------------------------
    @app.route("/founder/complaints/<int:complaint_id>/resolve", methods=["POST"])
    def founder_resolve_complaint(complaint_id: int):
        if not _session_founder_only():
            return redirect(url_for("login"))
        notes = (request.form.get("resolution_notes") or "").strip()
        if complaint_id and db.resolve_complaint(complaint_id, resolution_notes=notes):
            flash("تم تسجيل حل الشكوى.", "success")
        else:
            flash("تعذر التحديث أو الشكوى محلولة مسبقاً.", "warning")
        return redirect(url_for("founder_dashboard"))

    # ------------------------------------------------------------------
    # رفع الشعار العام
    # ------------------------------------------------------------------
    @app.route("/founder/site-logo", methods=["POST"])
    def founder_upload_site_logo():
        if not _session_founder_only():
            return redirect(url_for("login"))
        f = request.files.get("logo")
        # ── الدفعة 4-ج: تحقق magic bytes + حجم + sanitize ──
        data = None
        try:
            from logic.security import validate_image_upload as _validate_img

            ok, err, data, _mime = _validate_img(f)
            if not ok or not data:
                flash(f"رفض رفع الشعار: {err}", "danger")
                return redirect(url_for("founder_dashboard"))
        except ImportError:
            try:
                from logic.site_logo import save_uploaded_logo_as_png

                save_uploaded_logo_as_png(f, app.config["UPLOAD_FOLDER"], "logo.png")
                db.set_system_setting(SITE_LOGO_SETTING_KEY, SITE_LOGO_RELATIVE)
                db.set_system_setting(SITE_LOGO_CLOUD_ID_KEY, "")
                flash("تم حفظ الشعار العام وتحديث واجهة العملاء والفروع والإدارة.", "success")
            except Exception as _e:
                flash(str(_e) if str(_e) else "تعذر معالجة الصورة.", "danger")
            return redirect(url_for("founder_dashboard"))
        try:
            delete_remote_storage_public_id(db.get_system_setting(SITE_LOGO_CLOUD_ID_KEY))
            mime = ((_mime or "image/jpeg").strip()) or "image/jpeg"
            ref, pid = upload_branding_image_via_cloud_then_png(
                data,
                mime,
                logical_stem="site-logo",
                folder="site-logos",
            )
            ref_n = normalize_stored_media_ref(ref or "")
            if ref_n.startswith("http"):
                db.set_system_setting(SITE_LOGO_SETTING_KEY, ref_n)
                db.set_system_setting(SITE_LOGO_CLOUD_ID_KEY, (pid or "").strip())
                try:
                    remove_logo_file(app.config["UPLOAD_FOLDER"], "logo.png")
                except ValueError:
                    pass
                flash(
                    "تم حفظ الشعار العام وتحديث واجهة العملاء والفروع والإدارة.",
                    "success",
                )
            elif ref_n.startswith("uploads/"):
                db.set_system_setting(SITE_LOGO_SETTING_KEY, ref_n)
                db.set_system_setting(SITE_LOGO_CLOUD_ID_KEY, "")
                try:
                    remove_logo_file(app.config["UPLOAD_FOLDER"], "logo.png")
                except ValueError:
                    pass
                flash(
                    "تم حفظ الشعار محلياً (احتياطاً). راجع إعداد التخزين السحابي في التكاملات.",
                    "success",
                )
            else:
                png = png_bytes_from_image_bytes(data)
                save_png_bytes_to_upload_folder(
                    png, app.config["UPLOAD_FOLDER"], "logo.png"
                )
                db.set_system_setting(SITE_LOGO_SETTING_KEY, SITE_LOGO_RELATIVE)
                db.set_system_setting(SITE_LOGO_CLOUD_ID_KEY, "")
                flash("تم حفظ الشعار العام وتحديث واجهة العملاء والفروع والإدارة.", "success")
        except ValueError as e:
            flash(str(e), "warning")
        except OSError:
            flash("تعذر كتابة الملف. تحقق من صلاحيات مجلد الرفع.", "danger")
        except Exception:
            flash("تعذر معالجة الصورة. جرّب PNG أو JPEG أو WebP.", "danger")
        return redirect(url_for("founder_dashboard"))

    # ------------------------------------------------------------------
    # حذف الشعار العام
    # ------------------------------------------------------------------
    @app.route("/founder/site-logo/delete", methods=["POST"])
    def founder_delete_site_logo():
        if not _session_founder_only():
            return redirect(url_for("login"))
        try:
            clear_branding_from_disk_and_settings(
                db,
                upload_folder=app.config["UPLOAD_FOLDER"],
                path_setting_key=SITE_LOGO_SETTING_KEY,
                cloud_id_setting_key=SITE_LOGO_CLOUD_ID_KEY,
                legacy_filename="logo.png",
            )
            flash("تم حذف الشعار العام. ستُستخدم الهوية الافتراضية في الواجهات الأخرى.", "success")
        except ValueError as e:
            flash(str(e), "warning")
        except OSError:
            flash("تعذر حذف الملف.", "danger")
        return redirect(url_for("founder_dashboard"))

    # ------------------------------------------------------------------
    # رفع شعار لوحة تحكم النظام (المؤسس)
    # ------------------------------------------------------------------
    @app.route("/founder/founder-logo", methods=["POST"])
    def founder_upload_founder_logo():
        if not _session_founder_only():
            return redirect(url_for("login"))
        f = request.files.get("logo")
        # ── الدفعة 4-ج: تحقق magic bytes ──
        data = None
        try:
            from logic.security import validate_image_upload as _validate_img

            ok, err, data, _mime = _validate_img(f)
            if not ok or not data:
                flash(f"رفض رفع الشعار: {err}", "danger")
                return redirect(url_for("founder_dashboard"))
        except ImportError:
            try:
                from logic.site_logo import save_uploaded_logo_as_png

                save_uploaded_logo_as_png(
                    f, app.config["UPLOAD_FOLDER"], "founder_logo.png"
                )
                db.set_system_setting(FOUNDER_LOGO_SETTING_KEY, FOUNDER_LOGO_RELATIVE)
                db.set_system_setting(FOUNDER_LOGO_CLOUD_ID_KEY, "")
                flash("تم حفظ شعار لوحة تحكم النظام.", "success")
            except Exception as _e:
                flash(str(_e) if str(_e) else "تعذر معالجة الصورة.", "danger")
            return redirect(url_for("founder_dashboard"))
        try:
            delete_remote_storage_public_id(db.get_system_setting(FOUNDER_LOGO_CLOUD_ID_KEY))
            mime = ((_mime or "image/jpeg").strip()) or "image/jpeg"
            ref, pid = upload_branding_image_via_cloud_then_png(
                data,
                mime,
                logical_stem="founder-logo",
                folder="site-logos",
            )
            ref_n = normalize_stored_media_ref(ref or "")
            if ref_n.startswith("http"):
                db.set_system_setting(FOUNDER_LOGO_SETTING_KEY, ref_n)
                db.set_system_setting(
                    FOUNDER_LOGO_CLOUD_ID_KEY, (pid or "").strip()
                )
                try:
                    remove_logo_file(app.config["UPLOAD_FOLDER"], "founder_logo.png")
                except ValueError:
                    pass
                flash("تم حفظ شعار لوحة تحكم النظام.", "success")
            elif ref_n.startswith("uploads/"):
                db.set_system_setting(FOUNDER_LOGO_SETTING_KEY, ref_n)
                db.set_system_setting(FOUNDER_LOGO_CLOUD_ID_KEY, "")
                try:
                    remove_logo_file(app.config["UPLOAD_FOLDER"], "founder_logo.png")
                except ValueError:
                    pass
                flash(
                    "تم حفظ الشعار محلياً (احتياطاً). يُستحسن تفعيل تخزين سحابي من التكاملات.",
                    "success",
                )
            else:
                png = png_bytes_from_image_bytes(data)
                save_png_bytes_to_upload_folder(
                    png, app.config["UPLOAD_FOLDER"], "founder_logo.png"
                )
                db.set_system_setting(FOUNDER_LOGO_SETTING_KEY, FOUNDER_LOGO_RELATIVE)
                db.set_system_setting(FOUNDER_LOGO_CLOUD_ID_KEY, "")
                flash("تم حفظ شعار لوحة تحكم النظام.", "success")
        except ValueError as e:
            flash(str(e), "warning")
        except OSError:
            flash("تعذر كتابة الملف. تحقق من صلاحيات مجلد الرفع.", "danger")
        except Exception:
            flash("تعذر معالجة الصورة. جرّب PNG أو JPEG أو WebP.", "danger")
        return redirect(url_for("founder_dashboard"))

    # ------------------------------------------------------------------
    # حذف شعار لوحة تحكم النظام
    # ------------------------------------------------------------------
    @app.route("/founder/founder-logo/delete", methods=["POST"])
    def founder_delete_founder_logo():
        if not _session_founder_only():
            return redirect(url_for("login"))
        try:
            clear_branding_from_disk_and_settings(
                db,
                upload_folder=app.config["UPLOAD_FOLDER"],
                path_setting_key=FOUNDER_LOGO_SETTING_KEY,
                cloud_id_setting_key=FOUNDER_LOGO_CLOUD_ID_KEY,
                legacy_filename="founder_logo.png",
            )
            flash("تم حذف شعار لوحة تحكم النظام.", "success")
        except ValueError as e:
            flash(str(e), "warning")
        except OSError:
            flash("تعذر حذف الملف.", "danger")
        return redirect(url_for("founder_dashboard"))

    # ------------------------------------------------------------------
    # إدارة الفروع
    # ------------------------------------------------------------------
    @app.route("/founder/branches")
    def founder_branches():
        if not _session_founder_or_admin():
            return redirect(url_for("login"))
        branches = db.get_all_branches()
        enriched = []
        for b in branches:
            bid = int(b["id"])
            loc = db.get_branch_location(bid) or {}
            enriched.append({**b, "has_location": bool((loc.get("address") or "").strip())})
        return render_template("founder/branches.html", branches=enriched)

    # ------------------------------------------------------------------
    # معاينة محتوى الفرع
    # ------------------------------------------------------------------
    @app.route("/founder/branch/<int:branch_id>/view")
    def founder_branch_view(branch_id: int):
        """معاينة محتوى الفرع (فئات ومنتجات) دون تغيير جلسة الدخول."""
        if not _session_founder_or_admin():
            return redirect(url_for("login"))
        br = db.get_branch_row(branch_id)
        if not br:
            flash("الفرع غير موجود.", "danger")
            return redirect(url_for("founder_branches"))
        branch_cats = db.get_main_categories_by_branch(branch_id)
        products = db.get_branch_products(branch_id)
        return render_template(
            "founder/branch_view.html",
            branch_id=branch_id,
            city_name=br.get("city_name") or "",
            main_categories=branch_cats,
            products=products,
        )

    # ------------------------------------------------------------------
    # تعديل بيانات الفرع
    # ------------------------------------------------------------------
    @app.route("/founder/branches/<int:branch_id>/edit", methods=["GET", "POST"])
    def founder_branch_edit(branch_id: int):
        if not _session_founder_or_admin():
            return redirect(url_for("login"))
        data = db.get_branch_full_detail(branch_id)
        if not data:
            flash("الفرع غير موجود.", "danger")
            return redirect(url_for("founder_branches"))
        if request.method == "POST":
            city_name = (request.form.get("city_name") or "").strip()
            complaint_email = (request.form.get("complaint_email") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            username = (request.form.get("username") or "").strip()
            address = (request.form.get("address") or "").strip()
            maps_url = (request.form.get("google_maps_url") or "").strip()
            gps_lat = request.form.get("gps_lat")
            gps_lng = request.form.get("gps_lng")
            weekday_open = (request.form.get("weekday_open") or "09:00").strip()
            weekday_close = (request.form.get("weekday_close") or "22:00").strip()
            friday_open = (request.form.get("friday_open") or "16:00").strip()
            friday_close = (request.form.get("friday_close") or "23:00").strip()
            wd_two = (request.form.get("weekday_period_mode") or "one").strip() == "two"
            fr_two = (request.form.get("friday_period_mode") or "one").strip() == "two"
            weekday_start_2 = (request.form.get("weekday_start_2") or "").strip() or None
            weekday_end_2 = (request.form.get("weekday_end_2") or "").strip() or None
            friday_start_2 = (request.form.get("friday_start_2") or "").strip() or None
            friday_end_2 = (request.form.get("friday_end_2") or "").strip() or None
            if not wd_two:
                weekday_start_2 = weekday_end_2 = None
            elif not weekday_start_2 or not weekday_end_2:
                weekday_start_2 = weekday_end_2 = None
            if not fr_two:
                friday_start_2 = friday_end_2 = None
            elif not friday_start_2 or not friday_end_2:
                friday_start_2 = friday_end_2 = None

            if not db.update_branch_fields(
                branch_id,
                city_name=city_name,
                complaint_email=complaint_email,
                phone=phone,
                username=username,
            ):
                flash("تعذر حفظ بيانات الفرع الأساسية.", "warning")
            elif not db.upsert_branch_location(branch_id, address, maps_url, gps_lat, gps_lng):
                flash("تعذر حفظ الموقع.", "warning")
            elif not db.replace_working_hours(
                branch_id,
                weekday_open,
                weekday_close,
                friday_open,
                friday_close,
                weekday_start_2=weekday_start_2,
                weekday_end_2=weekday_end_2,
                friday_start_2=friday_start_2,
                friday_end_2=friday_end_2,
            ):
                flash("تعذر حفظ أوقات الدوام.", "warning")
            else:
                flash("تم حفظ بيانات الفرع.", "success")
            return redirect(url_for("founder_branch_edit", branch_id=branch_id))

        wh = data.get("hours") or {}
        wd = wh.get("weekday") or {}
        fr = wh.get("friday") or {}
        ws1 = (wd.get("start_time_1") or wd.get("open_time") or "09:00").strip()
        we1 = (wd.get("end_time_1") or wd.get("close_time") or "22:00").strip()
        fs1 = (fr.get("start_time_1") or fr.get("open_time") or "16:00").strip()
        fe1 = (fr.get("end_time_1") or fr.get("close_time") or "23:00").strip()
        ws2 = (wd.get("start_time_2") or "").strip()
        we2 = (wd.get("end_time_2") or "").strip()
        fs2 = (fr.get("start_time_2") or "").strip()
        fe2 = (fr.get("end_time_2") or "").strip()
        weekday_two = bool(ws2 and we2)
        friday_two = bool(fs2 and fe2)
        return render_template(
            "founder/branch_edit.html",
            branch_id=branch_id,
            b=data["branch"],
            loc=data["location"] or {},
            weekday_open=ws1,
            weekday_close=we1,
            friday_open=fs1,
            friday_close=fe1,
            weekday_start_2=ws2,
            weekday_end_2=we2,
            friday_start_2=fs2,
            friday_end_2=fe2,
            weekday_period_mode="two" if weekday_two else "one",
            friday_period_mode="two" if friday_two else "one",
            weekday_two=weekday_two,
            friday_two=friday_two,
            is_admin_editor=session.get("role") == "admin",
        )

    # ------------------------------------------------------------------
    # عرض جميع المنتجات
    # ------------------------------------------------------------------
    @app.route("/founder/products")
    def founder_products():
        if not _session_founder_only():
            return redirect(url_for("login"))
        items = db.list_all_products_for_founder(limit=1000)
        return render_template("founder/products.html", products=items)

    # ------------------------------------------------------------------
    # حسابات المستخدمين
    # ------------------------------------------------------------------
    @app.route("/founder/accounts")
    def founder_accounts():
        if not _session_founder_only():
            return redirect(url_for("login"))
        branches = db.get_all_branches()
        return render_template(
            "founder/accounts.html",
            branches=branches,
            admin_username=ADMIN_USERNAME,
        )

    # ------------------------------------------------------------------
    # تغيير كلمة مرور فرع
    # ------------------------------------------------------------------
    @app.route("/founder/branch/<int:branch_id>/password", methods=["POST"])
    def founder_branch_password(branch_id: int):
        if not _session_founder_only():
            return redirect(url_for("login"))
        new_pw = (request.form.get("new_password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()
        if len(new_pw) < 4:
            flash("كلمة مرور الفرع قصيرة جداً.", "warning")
            return redirect(url_for("founder_accounts"))
        if new_pw != confirm:
            flash("تأكيد كلمة المرور لا يطابق.", "danger")
            return redirect(url_for("founder_accounts"))
        if db.update_branch_password(branch_id, new_pw):
            flash("تم تحديث كلمة مرور الفرع.", "success")
        else:
            flash("تعذر تحديث كلمة مرور الفرع.", "danger")
        return redirect(url_for("founder_accounts"))

    # ------------------------------------------------------------------
    # تغيير كلمة مرور الإدارة
    # ------------------------------------------------------------------
    @app.route("/founder/admin/password", methods=["POST"])
    def founder_admin_password():
        if not _session_founder_only():
            return redirect(url_for("login"))
        new_pw = (request.form.get("new_password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()
        if len(new_pw) < 4:
            flash("كلمة مرور الإدارة قصيرة جداً.", "warning")
            return redirect(url_for("founder_accounts"))
        if new_pw != confirm:
            flash("تأكيد كلمة المرور لا يطابق.", "danger")
            return redirect(url_for("founder_accounts"))
        try:
            persist_admin_password_hashed(new_pw)
            flash("تم تحديث كلمة مرور حساب الإدارة في ملف البيئة.", "success")
        except OSError:
            flash("تعذر الكتابة على ملف .env.", "danger")
        except ValueError as e:
            flash(str(e) or "تعذر الحفظ.", "danger")
        return redirect(url_for("founder_accounts"))

    # ------------------------------------------------------------------
    # إضافة منتج من لوحة المؤسس
    # ------------------------------------------------------------------
    @app.route("/founder/add_product", methods=["GET", "POST"])
    def founder_add_product():
        if not _session_founder_only():
            return redirect(url_for("login"))
        branches = db.get_all_branches()
        if request.method == "GET":
            bid = request.args.get("branch_id", type=int)
            categories = db.get_main_categories_by_branch(bid) if bid else []
            return render_template(
                "founder/add_product.html",
                branches=branches,
                selected_branch_id=bid,
                categories=categories,
            )

        branch_id = request.form.get("branch_id")
        category_id = request.form.get("category_id")
        section_id = request.form.get("section_id")
        if not branch_id:
            flash("اختر الفرع.", "warning")
            return redirect(url_for("founder_add_product"))
        try:
            bid_int = int(branch_id)
        except (TypeError, ValueError):
            flash("فرع غير صالح.", "danger")
            return redirect(url_for("founder_add_product"))

        product_name = (request.form.get("product_name") or "").strip()
        product_description = (request.form.get("product_description") or "").strip()
        product_sku = (request.form.get("sku") or "").strip() or None

        try:
            category_id_int = int(category_id) if category_id else None
        except (TypeError, ValueError):
            category_id_int = None
        try:
            section_id_int = int(section_id) if section_id else None
        except (TypeError, ValueError):
            section_id_int = None

        if category_id_int is None or section_id_int is None or not product_name:
            flash("يرجى تعبئة الحقول بشكل صحيح.", "warning")
            return redirect(url_for("founder_add_product", branch_id=bid_int))

        possible_sections = db.get_sections_by_category(category_id_int)
        sec_row = next((s for s in possible_sections if int(s["id"]) == section_id_int), None)
        if not sec_row or int(sec_row.get("branch_id") or -1) != bid_int:
            flash("القسم غير صالح لهذا الفرع.", "danger")
            return redirect(url_for("founder_add_product", branch_id=bid_int))

        try:
            product_price = float(request.form.get("product_price") or 0)
        except (TypeError, ValueError):
            product_price = 0.0

        sizes = request.form.getlist("variant_size")
        colors = request.form.getlist("variant_color")
        quantities = request.form.getlist("variant_quantity")
        n = max(len(sizes), len(colors), len(quantities))
        variants = []
        for i in range(n):
            variants.append(
                {
                    "size": sizes[i] if i < len(sizes) else "",
                    "color": colors[i] if i < len(colors) else "",
                    "quantity": quantities[i] if i < len(quantities) else "",
                }
            )

        image_paths = collect_product_images_from_request(
            request.files.getlist("product_images"), max_images=3
        )

        product_id = db.add_product_from_section(
            section_id=section_id_int,
            product_name=product_name,
            description=product_description,
            variants=variants,
            image_paths=image_paths,
            sku=product_sku,
            product_price=product_price,
        )

        if product_id is None:
            flash("تعذر حفظ المنتج.", "warning")
        else:
            flash("تم حفظ المنتج بنجاح.", "success")
        return redirect(url_for("founder_products"))

    # ------------------------------------------------------------------
    # API أقسام الفئة (JSON)
    # ------------------------------------------------------------------
    @app.route("/founder/api/sections/<int:category_id>")
    def founder_api_sections(category_id: int):
        if not _session_founder_only():
            return jsonify({"error": "forbidden"}), 403
        bid = request.args.get("branch_id", type=int)
        sections = db.get_sections_by_category(category_id)
        if bid is not None:
            sections = [s for s in sections if int(s.get("branch_id") or -1) == bid]
        return jsonify({"sections": [{"id": s["id"], "name": s["name"]} for s in sections]})

    # ------------------------------------------------------------------
    # المؤسس: تغيير كلمة المرور (يُحفظ في .env فقط)
    # ------------------------------------------------------------------
    @app.route("/founder/change-password", methods=["POST"])
    def founder_change_password():
        if not _staff_session_ok():
            return redirect(url_for("login"))
        if session.get("role") != "founder":
            flash("هذه العملية للمؤسس فقط.", "warning")
            return redirect(url_for("founder_accounts"))

        current = (request.form.get("current_password") or "").strip()
        new_pw = (request.form.get("new_password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()

        if not password_matches_stored(current, FOUNDER_PASSWORD):
            flash("كلمة المرور الحالية غير صحيحة.", "danger")
            return redirect(url_for("founder_accounts"))
        if len(new_pw) < 4:
            flash("كلمة المرور الجديدة يجب أن لا تقل عن 4 أحرف.", "warning")
            return redirect(url_for("founder_accounts"))
        if new_pw != confirm:
            flash("تأكيد كلمة المرور لا يطابق الجديدة.", "danger")
            return redirect(url_for("founder_accounts"))
        try:
            persist_founder_password_hashed(new_pw)
            flash("تم تحديث كلمة مرور المؤسس وحفظها في ملف البيئة.", "success")
        except OSError:
            flash("تعذر الكتابة على ملف .env. تحقق من صلاحيات الملف.", "danger")
        except ValueError as e:
            flash(str(e) or "تعذر حفظ كلمة المرور.", "danger")
        return redirect(url_for("founder_accounts"))
