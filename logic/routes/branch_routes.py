"""
logic/routes/branch_routes.py
─────────────────────────────
Branch-facing routes extracted from app.py (dashboard, categories, sections,
products, complaints, inquiries, contact settings).
"""

import json
import logging

from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from helpers import (
    _staff_session_ok,
    _session_admin_or_founder,
    _session_branch_id_int,
    staff_member_required,
)
from logic.media_uploads import collect_product_images_from_request, file_storage_to_upload

logger = logging.getLogger(__name__)


def register(app, db):
    """Register all branch-facing routes on *app*."""

    # ──────────────────────────────────────────────
    # لوحة تحكم الفرع (dashboard)
    # ──────────────────────────────────────────────
    @app.route('/dashboard')
    def dashboard():
        if not _staff_session_ok():
            return redirect(url_for('login'))
        role = session.get('role')
        if role == 'founder':
            return redirect(url_for('founder_dashboard'))
        if role == 'admin':
            return redirect(url_for('admin_dashboard'))

        main_cats = db.get_main_categories()
        bid = session.get('branch_id')
        try:
            bid_int = int(bid) if bid is not None else None
        except (TypeError, ValueError):
            bid_int = None
        branch_cats = [
            c for c in main_cats
            if bid_int is not None and c.get('branch_id') is not None
            and int(c['branch_id']) == bid_int
        ]
        products = db.get_branch_products(bid_int) if bid_int is not None else []
        branch_services_by_id = {}
        if bid_int is not None:
            for r in db.list_branch_services_with_branches():
                if int(r["branch_id"]) == bid_int:
                    branch_services_by_id.setdefault(bid_int, []).append(r)
        branches = []
        if bid_int is not None:
            br = db.get_branch_by_id(bid_int)
            if br:
                branches = [br]
        # شكاوى الفرع للعرض في الداشبورد (مربوطة بـ branch_id فقط)
        branch_complaints = []
        branch_product_requests: list = []
        if bid_int is not None:
            try:
                branch_complaints = db.get_complaints(branch_id=bid_int, limit=50)
            except Exception:
                branch_complaints = []
            try:
                branch_product_requests = db.list_recent_product_requests(40)
            except Exception:
                branch_product_requests = []

        # استفسارات العملاء عن منتجات غير مسجلة
        branch_inquiries = []
        pending_inquiries_count = 0
        if bid_int is not None:
            try:
                br_info = db.get_branch_by_id(bid_int)
                br_city = (br_info.get("city_name") or "") if br_info else ""
                branch_inquiries = db.get_branch_inquiries(br_city, limit=50)
                pending_inquiries_count = sum(
                    1 for i in branch_inquiries if i.get("status") == "pending"
                )
            except Exception:
                branch_inquiries = []

        # معلومات الفرع (بريد + جوال) لعرضها في الإعدادات
        branch_info = db.get_branch_by_id(bid_int) if bid_int is not None else {}

        return render_template(
            "dashboard.html",
            main_categories=branch_cats,
            products=products,
            branch_services_by_id=branch_services_by_id,
            branches=branches,
            branch_complaints=branch_complaints,
            branch_inquiries=branch_inquiries,
            pending_inquiries_count=pending_inquiries_count,
            branch_product_requests=branch_product_requests,
            branch_info=branch_info or {},
        )

    # ──────────────────────────────────────────────
    # إضافة فئة للفرع
    # ──────────────────────────────────────────────
    @app.route('/branch/add_category', methods=['POST'])
    def branch_add_category():
        if session.get('role') != 'branch':
            flash("غير مصرح.", "danger")
            return redirect(url_for('login'))
        cat_name = (request.form.get('cat_name') or '').strip()
        bid = session.get('branch_id')
        if cat_name and bid:
            if db.add_main_category(cat_name, branch_id=bid):
                flash("تم حفظ الفئة بنجاح", "success")
            else:
                flash("تعذر حفظ الفئة (قد تكون موجودة مسبقاً)", "warning")
        else:
            flash("أدخل اسماً صحيحاً للفئة", "warning")
        return redirect(url_for('dashboard'))

    # ==========================================
    # صفحات الأقسام المرتبطة بالفئة
    # Category -> Sections -> Add Section
    # ==========================================
    @app.route('/categories/<int:category_id>/sections')
    def show_sections(category_id: int):
        if not _staff_session_ok():
            return redirect(url_for('login'))
        role = session.get('role')
        if role not in ('branch', 'admin', 'founder'):
            return redirect(url_for('dashboard'))

        mc = db.get_main_category_by_id(category_id)
        if not mc:
            flash("الفئة غير موجودة.", "danger")
            if role == 'founder':
                return redirect(url_for('founder_dashboard'))
            if role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))

        if role == 'branch':
            bid = session.get('branch_id')
            try:
                if int(mc.get('branch_id') or -1) != int(bid):
                    flash("غير مصرح.", "danger")
                    return redirect(url_for('dashboard'))
            except (TypeError, ValueError):
                flash("غير مصرح.", "danger")
                return redirect(url_for('dashboard'))

        sections = db.get_sections_by_category(category_id)
        return render_template(
            'sections.html',
            category_id=category_id,
            category=mc,
            sections=sections,
        )

    @app.route('/categories/<int:category_id>/sections/<int:section_id>/delete', methods=['POST'])
    def delete_subcategory_section(category_id: int, section_id: int):
        """حذف قسم (sub_category) وجميع المنتجات المرتبطة — فرع / إدارة / مؤسس."""
        if not _staff_session_ok():
            return jsonify(ok=False, error='unauthorized'), 401
        role = session.get('role')
        if role not in ('branch', 'admin', 'founder'):
            return jsonify(ok=False, error='forbidden'), 403

        mc = db.get_main_category_by_id(category_id)
        if not mc:
            return jsonify(ok=False, error='الفئة غير موجودة.'), 404

        if role == 'branch':
            bid = session.get('branch_id')
            try:
                if int(mc.get('branch_id') or -1) != int(bid):
                    return jsonify(ok=False, error='غير مصرح.'), 403
            except (TypeError, ValueError):
                return jsonify(ok=False, error='غير مصرح.'), 403

        row = db.get_sections_by_category(category_id)
        allowed_ids = {int(s['id']) for s in row}
        if int(section_id) not in allowed_ids:
            return jsonify(ok=False, error='القسم غير ضمن هذه الفئة.'), 400

        ok = db.delete_branch_subcategory_and_products(section_id)
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if ok:
            msg = 'تم حذف القسم وجميع المنتجات المرتبطة به بنجاح'
            if wants_json:
                return jsonify(ok=True, message=msg)
            flash(msg, 'success')
            return redirect(url_for('show_sections', category_id=category_id))

        if wants_json:
            return jsonify(ok=False, error='تعذر حذف القسم.'), 500
        flash('تعذر حذف القسم.', 'danger')
        return redirect(url_for('show_sections', category_id=category_id))

    @app.route('/categories/<int:category_id>/sections/add', methods=['POST'])
    def add_section(category_id: int):
        if not _staff_session_ok():
            return redirect(url_for('login'))
        if session.get('role') != 'branch':
            flash("غير مصرح.", "danger")
            return redirect(url_for('login'))

        section_name = (request.form.get('section_name') or '').strip()
        bid = session.get('branch_id')
        if not section_name:
            flash("يرجى إدخال اسم القسم.", "warning")
            return redirect(url_for('show_sections', category_id=category_id))

        try:
            # branch_id في الجدول عدد، والجلسة قد تعطي string
            bid_int = int(bid) if bid is not None else None
        except (TypeError, ValueError):
            bid_int = None

        if bid_int is None:
            flash("تعذر تحديد الفرع الحالي.", "danger")
            return redirect(url_for('show_sections', category_id=category_id))

        sub_id = db.add_sub_category(main_id=category_id, branch_id=bid_int, name=section_name)
        if sub_id is None:
            flash("تعذر حفظ القسم (قد يكون موجوداً).", "warning")
        else:
            flash("تم حفظ القسم بنجاح.", "success")

        return redirect(url_for('show_sections', category_id=category_id))

    # ==========================================
    # صفحة إضافة المنتج + API للأقسام
    # Category -> Sections (Dynamic)
    # ==========================================
    @app.route('/get_sections/<int:category_id>')
    def api_get_sections(category_id: int):
        if not _staff_session_ok():
            return jsonify({"error": "unauthorized"}), 401
        if session.get('role') != 'branch':
            return jsonify({"error": "forbidden"}), 403

        bid = session.get('branch_id')
        try:
            bid_int = int(bid)
        except (TypeError, ValueError):
            bid_int = None

        sections = db.get_sections_by_category(category_id)
        if bid_int is not None:
            sections = [s for s in sections if int(s.get('branch_id') or -1) == bid_int]

        return jsonify(
            {
                "sections": [
                    {"id": s["id"], "name": s["name"]}
                    for s in sections
                ]
            }
        )

    @app.route('/add_product', methods=['GET', 'POST'])
    def add_product():
        if not _staff_session_ok():
            return redirect(url_for('login'))
        if session.get('role') != 'branch':
            flash("غير مصرح.", "danger")
            return redirect(url_for('dashboard'))

        bid = session.get('branch_id')
        try:
            bid_int = int(bid)
        except (TypeError, ValueError):
            flash("تعذر تحديد الفرع.", "danger")
            return redirect(url_for('dashboard'))

        if request.method == 'GET':
            categories = db.get_main_categories_by_branch(bid_int)
            return render_template('add_product.html', categories=categories)

        # POST: حفظ المنتج
        category_id = request.form.get('category_id')
        section_id = request.form.get('section_id')
        product_name = (request.form.get('product_name') or '').strip()
        product_description = (request.form.get('product_description') or '').strip()
        product_sku = (request.form.get('sku') or '').strip() or None

        try:
            category_id_int = int(category_id) if category_id is not None else None
        except (TypeError, ValueError):
            category_id_int = None
        try:
            section_id_int = int(section_id) if section_id is not None else None
        except (TypeError, ValueError):
            section_id_int = None

        if category_id_int is None or section_id_int is None or not product_name:
            flash("يرجى تعبئة الحقول بشكل صحيح.", "warning")
            return redirect(url_for('add_product'))

        # تحقق أن section تابع للفئة وللفرع الحالي
        possible_sections = db.get_sections_by_category(category_id_int)
        sec_row = next((s for s in possible_sections if int(s["id"]) == section_id_int), None)
        if not sec_row or int(sec_row.get("branch_id") or -1) != bid_int:
            flash("القسم غير صالح لهذا الفرع.", "danger")
            return redirect(url_for('add_product'))

        try:
            product_price = float(request.form.get("product_price") or 0)
        except (TypeError, ValueError):
            product_price = 0.0

        # variants من النموذج: مقاس + لون + كمية (بدون سعر — السعر من product_price)
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

        # صور: حتى 3
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
            flash("تعذر حفظ المنتج. تأكد من القسم والصور (حد أقصى 3).", "warning")
            return redirect(url_for('add_product'))

        flash("تم حفظ المنتج بنجاح.", "success")
        return redirect(url_for('add_product'))

    # ==========================================
    # لوحة تحكم الفرع: عرض المنتجات + حذف/تعديل
    # ==========================================
    @app.route('/products')
    def products():
        if not _staff_session_ok():
            return redirect(url_for('login'))
        if session.get('role') != 'branch':
            return redirect(url_for('dashboard'))
        bid = session.get('branch_id')
        try:
            bid_int = int(bid)
        except (TypeError, ValueError):
            return redirect(url_for('dashboard'))

        items = db.list_products_for_branch(bid_int)
        return render_template('products.html', products=items)

    @app.route('/products/delete/<int:product_id>', methods=['POST'])
    def delete_product(product_id: int):
        if not _staff_session_ok():
            return redirect(url_for('login'))
        role = session.get('role')
        if role not in ('branch', 'founder'):
            return redirect(url_for('dashboard'))

        prod = db.get_product_detail(product_id)
        if not prod:
            flash("منتج غير صالح.", "danger")
            return redirect(url_for('founder_products' if role == 'founder' else 'products'))

        if role == 'branch':
            bid = session.get('branch_id')
            try:
                bid_int = int(bid)
            except (TypeError, ValueError):
                bid_int = None
            if bid_int is None or int(prod.get('branch_id') or -1) != bid_int:
                flash("منتج غير صالح.", "danger")
                return redirect(url_for('products'))

        if db.delete_product_cascade(product_id):
            flash("تم حذف المنتج.", "success")
        else:
            flash("تعذر حذف المنتج.", "warning")
        return redirect(url_for('founder_products' if role == 'founder' else 'products'))

    @app.route('/edit_product/<int:product_id>', methods=['GET', 'POST'])
    def edit_product(product_id: int):
        if not _staff_session_ok():
            return redirect(url_for('login'))
        role = session.get('role')
        if role not in ('branch', 'founder'):
            return redirect(url_for('dashboard'))

        bid_int = None
        if role == 'branch':
            bid = session.get('branch_id')
            try:
                bid_int = int(bid)
            except (TypeError, ValueError):
                bid_int = None

        prod = db.get_product_detail(product_id)
        if not prod:
            flash("منتج غير صالح.", "danger")
            return redirect(url_for('founder_products' if role == 'founder' else 'products'))
        if role == 'branch' and (bid_int is None or int(prod.get('branch_id') or -1) != bid_int):
            flash("منتج غير صالح.", "danger")
            return redirect(url_for('products'))

        if request.method == 'GET':
            return render_template('edit_product.html', product=prod)

        # POST: تحديث
        name = (request.form.get('product_name') or '').strip()
        desc = (request.form.get('product_description') or '').strip()
        try:
            price_val = float(request.form.get("product_price") or prod.get("price") or 0)
        except (TypeError, ValueError):
            price_val = 0.0

        ok_basic = db.update_product_basic(product_id, name=name, description=desc, price=price_val)

        # variants replace (مقاس + لون + كمية — السعر من سعر المنتج)
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
        ok_variants = db.replace_product_variants(product_id, variants, product_price=price_val)

        # images: إن تم رفع صور جديدة نستبدل
        new_paths = collect_product_images_from_request(
            request.files.getlist("product_images"), max_images=3
        )
        ok_images = True
        if new_paths:
            ok_images = db.replace_product_images(product_id, new_paths)

        if ok_basic and ok_variants and ok_images:
            flash("تم تحديث المنتج.", "success")
        else:
            flash("تم الحفظ مع ملاحظة: تأكد من البيانات (variants/صور).", "warning")
        if role == 'founder':
            return redirect(url_for('founder_products'))
        return redirect(url_for('edit_product', product_id=product_id))

    # ──────────────────────────────────────────────
    # رد الفرع على شكوى العميل
    # ──────────────────────────────────────────────
    @app.route("/branch/complaints/<int:complaint_id>/reply", methods=["POST"])
    @staff_member_required
    def branch_complaint_reply(complaint_id: int):
        """
        مدير الفرع يكتب رد على الشكوى → النظام يرسله للعميل (إيميل أو واتساب).
        """
        from logic.mail_service import send_email as _send_email
        from logic.routes.wa_webhook_routes import _wa_send_message, _wa_runtime_phone_number_id

        role = session.get("role")
        bid_sess = _session_branch_id_int()

        reply_text = (request.form.get("reply_text") or "").strip()
        if not reply_text:
            flash("يرجى كتابة نص الرد.", "warning")
            return redirect(request.referrer or url_for("dashboard"))

        row = db.get_complaint_with_customer_contact(complaint_id)
        if not row:
            flash("الشكوى غير موجودة.", "danger")
            return redirect(request.referrer or url_for("dashboard"))

        # التحقق من صلاحية الفرع
        if role == "branch":
            comp_bid = row.get("branch_id")
            if comp_bid is not None and int(comp_bid) != bid_sess:
                flash("غير مصرح لك بالرد على هذه الشكوى.", "danger")
                return redirect(url_for("dashboard"))

        cust_name  = (row.get("customer_name")  or "").strip() or "العميل"
        cust_email = (row.get("customer_email") or "").strip() or None
        cust_phone = (row.get("customer_phone") or "").strip() or None
        ticket     = (row.get("ticket_code")    or "").strip()
        branch_lbl = (row.get("branch_name")    or session.get("city_name") or "").strip()

        sent_channels = []
        send_failed   = []

        # ── إرسال عبر البريد ──
        if cust_email:
            subj = f"رد على شكواك" + (f" #{ticket}" if ticket else "")
            ticket_line = f" برقم {ticket}" if ticket else ""
            branch_name_line = branch_lbl or "خدمة العملاء"
            body = (
                "أخي/أختي " + cust_name + "،\n\n"
                "بخصوص شكواك المسجّلة" + ticket_line + ":\n\n"
                + reply_text + "\n\n"
                "نشكر تواصلك معنا ونتمنى أن يكون الأمر قد حُلّ بما يرضيك.\n"
                "فريق " + branch_name_line
            )
            if _send_email(cust_email, subj, body):
                sent_channels.append("البريد الإلكتروني")
            else:
                send_failed.append("البريد الإلكتروني")

        # ── إرسال عبر واتساب إذا كان للعميل رقم جوال ──
        if cust_phone and _wa_runtime_phone_number_id():
            wa_phone = cust_phone.lstrip("+").replace(" ", "").replace("-", "")
            ticket_line = f" #{ticket}" if ticket else ""
            wa_body = f"رد على شكواك{ticket_line}:\n\n{reply_text}"
            if _wa_send_message(_wa_runtime_phone_number_id(), wa_phone, wa_body):
                sent_channels.append("واتساب")
            else:
                send_failed.append("واتساب")

        # ── تسجيل الرد في قاعدة البيانات ──
        db.save_complaint_customer_reply(complaint_id, reply_text)

        if sent_channels:
            flash(f"تم إرسال الرد للعميل عبر: {', '.join(sent_channels)}.", "success")
        elif cust_email or cust_phone:
            flash("تم حفظ الرد — تعذر الإرسال التلقائي، تحقق من إعدادات البريد.", "warning")
        else:
            flash("تم حفظ الرد — لا يوجد بريد أو جوال للعميل لإرساله تلقائياً.", "info")

        return redirect(request.referrer or url_for("dashboard"))

    # ──────────────────────────────────────────────
    # رد الفرع على استفسار العميل
    # ──────────────────────────────────────────────
    @app.route("/branch/inquiry/<int:inquiry_id>/reply", methods=["POST"])
    def branch_reply_inquiry(inquiry_id: int):
        """
        الفرع يرد على استفسار العميل بنص + سعر + صورة اختيارية.
        """
        if not _staff_session_ok():
            return redirect(url_for("login"))

        role = session.get("role")
        if role == "branch":
            inq_row = db.get_inquiry_by_id(inquiry_id)
            bid_sess = _session_branch_id_int()
            br = db.get_branch_by_id(bid_sess) if bid_sess is not None else None
            city = (br.get("city_name") or "").strip() if br else ""
            inq_branch = (inq_row.get("branch_name") or "").strip() if inq_row else ""
            if not inq_row or not city or not inq_branch or inq_branch != city:
                flash("هذا الاستفسار لا يخص فرعك.", "danger")
                return redirect(url_for("dashboard"))

        reply_text = (request.form.get("reply_text") or "").strip()
        branch_price = (request.form.get("branch_price") or "").strip()

        if not reply_text:
            flash("يرجى كتابة نص الرد.", "warning")
            return redirect(request.referrer or url_for("dashboard"))

        # رفع صورة اختيارية من الفرع
        branch_image_path = ""
        img_file = request.files.get("branch_image")
        if img_file and img_file.filename:
            ref, err_img = file_storage_to_upload(
                img_file, folder="branch-inquiries", require_validation=True
            )
            if err_img:
                flash(f"رفض رفع الصورة: {err_img}", "danger")
                return redirect(request.referrer or url_for("dashboard"))
            if ref:
                branch_image_path = ref

        ok = db.reply_to_inquiry(
            inquiry_id=inquiry_id,
            branch_reply=reply_text,
            branch_price=branch_price,
            branch_image_path=branch_image_path,
        )

        if ok:
            # ── إشعار العميل بالبريد إذا كان لديه بريد مسجّل ──
            inquiry = None
            try:
                inquiry = db.get_inquiry_by_id(inquiry_id)
                if inquiry:
                    inquiry["branch_reply"] = reply_text
                    inquiry["branch_price"] = branch_price
                    inquiry["branch_image_path"] = branch_image_path
                    from logic.branch_inquiry_service import notify_customer_of_reply
                    notify_customer_of_reply(inquiry)
            except Exception:
                pass  # الإشعار غير إلزامي

            # ── إرسال عبر واتساب إذا كان العميل قادماً من واتساب ──
            try:
                from logic.routes.wa_webhook_routes import _wa_send_message, _wa_runtime_phone_number_id
                if inquiry and _wa_runtime_phone_number_id():
                    inq_phone = (inquiry.get("customer_phone") or "").strip()
                    if inq_phone:
                        wa_phone = inq_phone.lstrip("+").replace(" ", "").replace("-", "")
                        parts = [f"رد على استفسارك:\n\n{reply_text}"]
                        if branch_price:
                            parts.append(f"💰 السعر: {branch_price}")
                        _wa_send_message(_wa_runtime_phone_number_id(), wa_phone, "\n".join(parts))
            except Exception:
                pass  # واتساب اختياري — لا يوقف العملية

            flash("✅ تم إرسال الرد للعميل بنجاح.", "success")
        else:
            flash("حدث خطأ أثناء حفظ الرد.", "danger")

        return redirect(request.referrer or url_for("dashboard"))

    # ──────────────────────────────────────────────
    # API: حالة استفسار (polling من الشات)
    # ──────────────────────────────────────────────
    @app.route("/api/inquiry-status")
    def api_inquiry_status():
        """
        يتحقق إذا وصل رد من الفرع على آخر استفسار في الجلسة.
        يُستخدم من الشات بالـ polling كل 30 ثانية.
        """
        inq_id = session.get("last_inquiry_id")
        if not inq_id:
            return jsonify({"replied": False})
        try:
            inquiry = db.get_inquiry_by_id(int(inq_id))
            if not inquiry or inquiry.get("status") != "answered":
                return jsonify({"replied": False})

            # بناء رسالة الرد
            from logic.branch_inquiry_service import get_inquiry_reply_message
            dialect = session.get("chat_dialect") or "default"
            payload = get_inquiry_reply_message(inquiry, dialect)
            # امسح من الجلسة حتى لا يُظهر مرة ثانية
            session.pop("last_inquiry_id", None)
            return jsonify({"replied": True, **payload})
        except Exception:
            return jsonify({"replied": False})

    # ──────────────────────────────────────────────
    # تحديث بيانات اتصال الفرع
    # ──────────────────────────────────────────────
    @app.route("/branch/settings/contact", methods=["POST"])
    def branch_update_contact():
        """يحدّث بريد الفرع ورقم جواله من الداشبورد."""
        if not _staff_session_ok():
            return redirect(url_for("login"))
        bid = _session_branch_id_int()
        if bid is None:
            flash("غير مصرح.", "danger")
            return redirect(url_for("dashboard"))

        branch_email = (request.form.get("branch_email") or "").strip()
        branch_phone = (request.form.get("branch_phone") or "").strip()

        ok = db.update_branch_fields(
            branch_id=bid,
            complaint_email=branch_email or None,
            phone=branch_phone or None,
        )
        if ok:
            flash("✅ تم حفظ بيانات الفرع.", "success")
        else:
            flash("حدث خطأ أثناء الحفظ.", "danger")

        return redirect(url_for("dashboard"))
