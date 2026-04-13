import logging
import os
import re
import time
import uuid
import atexit
from datetime import timedelta
from functools import wraps

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, current_app
from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALLOWED_EXTENSIONS,
    FLASK_DEBUG,
    FLASK_HOST,
    FLASK_PORT,
    FOUNDER_PASSWORD,
    FOUNDER_USERNAME,
    SECRET_KEY,
    UPLOAD_FOLDER,
    ensure_upload_dir,
    password_matches_stored,
    persist_admin_password_hashed,
    persist_founder_password_hashed,
    set_admin_username_runtime,
    set_founder_username_runtime,
    update_env_file,
)
from logic.chat_service import chat_query as chat_query_handler, init_chat_service
from logic.campaign_routes import create_campaign_blueprint
from logic.campaign_scheduler import (
    start_campaign_scheduler_thread,
    stop_campaign_scheduler_thread,
)
from logic.company_info_repository import ALLOWED_COMPANY_INFO_KEYS
from logic.database import DatabaseManager
from logic.site_logo import (
    FOUNDER_LOGO_RELATIVE,
    FOUNDER_LOGO_SETTING_KEY,
    SITE_LOGO_RELATIVE,
    SITE_LOGO_SETTING_KEY,
    get_public_logo_url,
    remove_logo_file,
    resolve_site_logo_url,
    save_uploaded_logo_as_png,
)

ensure_upload_dir()

logger = logging.getLogger(__name__)

# الملفات العامة (CSS/JS) من /static/؛ الشعار يُخزَّن تحت static/uploads/ ويُعرَض عبر url_for('static', filename='uploads/...')
# لا تغيّر static_folder إلى uploads فقط — سيُعطّل كل الموارد الثابتة.
app = Flask(__name__, static_folder='static')
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)


@app.before_request
def _make_session_permanent():
    session.permanent = True

db = DatabaseManager()
migrated_branch_passwords = db.migrate_branch_passwords_to_hashes()
if migrated_branch_passwords:
    logger.info(
        "migrated %s legacy branch password(s) to hashes",
        migrated_branch_passwords,
    )
init_chat_service(db)
app.register_blueprint(create_campaign_blueprint(db))


def _should_start_campaign_scheduler() -> bool:
    if not FLASK_DEBUG:
        return True
    return os.environ.get("WERKZEUG_RUN_MAIN") == "true"


if _should_start_campaign_scheduler():
    start_campaign_scheduler_thread(db)
atexit.register(stop_campaign_scheduler_thread)


def get_logo_url():
    """رابط الشعار العام — نفس القيمة المحقونة في القوالب كـ logo_url (اختياري لـ render_template الصريح)."""
    return get_public_logo_url(app, db)


app.jinja_env.globals["get_logo_url"] = get_logo_url


@app.context_processor
def inject_site_logo():
    stored_founder = db.get_system_setting(FOUNDER_LOGO_SETTING_KEY, "") or ""
    site_logo_resolved = get_public_logo_url(app, db)
    return {
        "logo_url": site_logo_resolved,
        "site_logo_url": site_logo_resolved,
        "site_logo_path": site_logo_resolved,
        "site_logo_cache_bust": int(time.time()),
        "founder_logo_url": resolve_site_logo_url(app, stored_founder),
    }


def _session_admin_or_founder():
    """صلاحيات لوحة الإدارة (مدير عام أو مؤسس)."""
    return session.get("role") in ("admin", "founder")


def _staff_session_ok() -> bool:
    """دخول لوحة الفروع/الإدارة فقط — زوار الشات قد يكون لديهم logged_in بدون role."""
    return session.get("role") in ("founder", "admin", "branch")


def _session_branch_id_int():
    """معرّف الفرع الحالي كعدد صحيح أو None."""
    bid = session.get("branch_id")
    try:
        return int(bid) if bid is not None else None
    except (TypeError, ValueError):
        return None


def staff_member_required(view_func):
    @wraps(view_func)
    def _wrapped(*args, **kwargs):
        if not _staff_session_ok():
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return _wrapped


def _session_founder_only():
    return session.get("role") == "founder"


def _session_founder_or_admin():
    return session.get("role") in ("founder", "admin")


# ==========================================
# نظام الدخول ولوحة التحكم
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if _staff_session_ok():
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if username == FOUNDER_USERNAME and password_matches_stored(password, FOUNDER_PASSWORD):
            session.clear()
            session.permanent = True
            session["logged_in"] = True
            session["username"] = username
            session["role"] = "founder"
            session["city_name"] = "المؤسس"
            flash("مرحباً بك أيها المؤسس", "success")
            return redirect(url_for("founder_dashboard"))

        if username == ADMIN_USERNAME and password_matches_stored(password, ADMIN_PASSWORD):
            session.clear()
            session.permanent = True
            session['logged_in'] = True
            session['username'] = username
            session['role'] = 'admin'
            session['city_name'] = 'الإدارة العامة'
            flash('مرحباً بك أيها المدير العام', 'success')
            return redirect(url_for('admin_dashboard'))
        
        # حسابات الفروع تدعم التوافق مع السجلات القديمة، وتُحفظ الآن كـ hash.
        branch, branch_login_status = db.check_branch_login_with_status(username, password)
        if branch:
            session.clear()
            session.permanent = True
            session['logged_in'] = True
            session['username'] = username
            session['branch_id'] = branch['id']
            session['city_name'] = branch['city_name']
            session['role'] = 'branch'
            flash(f'تم تسجيل الدخول لفرع: {branch["city_name"]}', 'success')
            return redirect(url_for('dashboard'))
        else:
            current_app.logger.warning(
                "branch login failed username=%r reason=%s",
                (username or "").strip(),
                branch_login_status,
            )
            flash("بيانات الدخول غير صحيحة!", "danger")

    return render_template('login.html')


app.add_url_rule(
    "/admin/login",
    endpoint="admin_login",
    view_func=login,
    methods=["GET", "POST"],
)
app.add_url_rule(
    "/branch/login",
    endpoint="branch_login",
    view_func=login,
    methods=["GET", "POST"],
)


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
    return render_template(
        "admin_dashboard.html",
        branches=branches,
        main_categories=main_cats,
        branch_services_by_id=branch_services_by_id,
    )


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
        for k in ALLOWED_COMPANY_INFO_KEYS:
            raw = request.form.get(k)
            if raw is not None:
                db.set_company_info_key(k, raw)
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
        flash("تم حفظ خدمات الفروع.", "success")
        return redirect(url_for("admin_dashboard"))
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
    # شكاوى الفرع للعرض في الداشبورد
    branch_complaints = []
    if bid_int is not None:
        try:
            branch_complaints = db.get_complaints(branch_name=None, status=None, limit=50)
            branch_complaints = [
                c for c in branch_complaints
                if c.get("branch_id") is not None and int(c["branch_id"]) == bid_int
            ]
        except Exception:
            branch_complaints = []

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
        branch_info=branch_info or {},
    )

@app.route('/logout')
def logout():
    role = session.get("role")
    session.clear()
    flash("تم تسجيل الخروج بنجاح", "info")
    if role == "branch":
        return redirect(url_for("branch_login"))
    if role == "admin":
        return redirect(url_for("admin_login"))
    return redirect(url_for("login"))

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

@app.route('/admin/delete_branch/<int:b_id>')
def delete_branch(b_id):
    if _session_admin_or_founder():
        db.delete_branch(b_id)
        flash("تم حذف الفرع نهائياً", "warning")
    if session.get("role") == "founder":
        return redirect(url_for("founder_branches"))
    return redirect(url_for('admin_dashboard'))

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
    uploaded_files = request.files.getlist('product_images')
    image_paths = []
    allowed_image_exts = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    for f in uploaded_files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit('.', 1)[1].lower() if '.' in f.filename else ''
        if ext not in allowed_image_exts:
            continue
        if len(image_paths) >= 3:
            break
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_name))
        image_paths.append(f"uploads/{unique_name}")

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
    uploaded_files = request.files.getlist('product_images')
    new_paths = []
    allowed_image_exts = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    for f in uploaded_files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit('.', 1)[1].lower() if '.' in f.filename else ''
        if ext not in allowed_image_exts:
            continue
        if len(new_paths) >= 3:
            break
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_name))
        new_paths.append(f"uploads/{unique_name}")
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


# ==========================================
# لوحة المؤسس (Founder Dashboard)
# ==========================================
@app.route("/founder/")
@app.route("/founder/dashboard")
def founder_dashboard():
    if not _session_founder_only():
        return redirect(url_for("login"))
    n_branches = len(db.get_all_branches() or [])
    n_products = db.count_products_total()
    st = db.get_complaints_stats()
    complaints_preview = db.get_complaints(status="open", limit=40)
    return render_template(
        "founder/dashboard.html",
        n_branches=n_branches,
        n_products=n_products,
        n_complaints=st.get("total", 0),
        complaints_preview=complaints_preview,
    )


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


@app.route("/founder/site-logo", methods=["POST"])
def founder_upload_site_logo():
    if not _session_founder_only():
        return redirect(url_for("login"))
    f = request.files.get("logo")
    try:
        save_uploaded_logo_as_png(f, app.config["UPLOAD_FOLDER"], "logo.png")
        db.set_system_setting(SITE_LOGO_SETTING_KEY, SITE_LOGO_RELATIVE)
        flash("تم حفظ الشعار العام وتحديث واجهة العملاء والفروع والإدارة.", "success")
    except ValueError as e:
        flash(str(e), "warning")
    except OSError:
        flash("تعذر كتابة الملف. تحقق من صلاحيات مجلد الرفع.", "danger")
    except Exception:
        flash("تعذر معالجة الصورة. جرّب PNG أو JPEG أو WebP.", "danger")
    return redirect(url_for("founder_dashboard"))


@app.route("/founder/site-logo/delete", methods=["POST"])
def founder_delete_site_logo():
    if not _session_founder_only():
        return redirect(url_for("login"))
    try:
        remove_logo_file(app.config["UPLOAD_FOLDER"], "logo.png")
        db.set_system_setting(SITE_LOGO_SETTING_KEY, "")
        flash("تم حذف الشعار العام. ستُستخدم الهوية الافتراضية في الواجهات الأخرى.", "success")
    except ValueError as e:
        flash(str(e), "warning")
    except OSError:
        flash("تعذر حذف الملف.", "danger")
    return redirect(url_for("founder_dashboard"))


@app.route("/founder/founder-logo", methods=["POST"])
def founder_upload_founder_logo():
    if not _session_founder_only():
        return redirect(url_for("login"))
    f = request.files.get("logo")
    try:
        save_uploaded_logo_as_png(f, app.config["UPLOAD_FOLDER"], "founder_logo.png")
        db.set_system_setting(FOUNDER_LOGO_SETTING_KEY, FOUNDER_LOGO_RELATIVE)
        flash("تم حفظ شعار لوحة المؤسس.", "success")
    except ValueError as e:
        flash(str(e), "warning")
    except OSError:
        flash("تعذر كتابة الملف. تحقق من صلاحيات مجلد الرفع.", "danger")
    except Exception:
        flash("تعذر معالجة الصورة. جرّب PNG أو JPEG أو WebP.", "danger")
    return redirect(url_for("founder_dashboard"))


@app.route("/founder/founder-logo/delete", methods=["POST"])
def founder_delete_founder_logo():
    if not _session_founder_only():
        return redirect(url_for("login"))
    try:
        remove_logo_file(app.config["UPLOAD_FOLDER"], "founder_logo.png")
        db.set_system_setting(FOUNDER_LOGO_SETTING_KEY, "")
        flash("تم حذف شعار لوحة المؤسس.", "success")
    except ValueError as e:
        flash(str(e), "warning")
    except OSError:
        flash("تعذر حذف الملف.", "danger")
    return redirect(url_for("founder_dashboard"))


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


@app.route("/founder/products")
def founder_products():
    if not _session_founder_only():
        return redirect(url_for("login"))
    items = db.list_all_products_for_founder(limit=1000)
    return render_template("founder/products.html", products=items)


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

    uploaded_files = request.files.getlist("product_images")
    image_paths = []
    allowed_image_exts = {"png", "jpg", "jpeg", "gif", "webp"}
    for f in uploaded_files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit(".", 1)[1].lower() if "." in f.filename else ""
        if ext not in allowed_image_exts:
            continue
        if len(image_paths) >= 3:
            break
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
        image_paths.append(f"uploads/{unique_name}")

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


@app.route("/founder/api/sections/<int:category_id>")
def founder_api_sections(category_id: int):
    if not _session_founder_only():
        return jsonify({"error": "forbidden"}), 403
    bid = request.args.get("branch_id", type=int)
    sections = db.get_sections_by_category(category_id)
    if bid is not None:
        sections = [s for s in sections if int(s.get("branch_id") or -1) == bid]
    return jsonify({"sections": [{"id": s["id"], "name": s["name"]} for s in sections]})


# ==========================================
# المؤسس: تغيير كلمة المرور (يُحفظ في .env فقط)
# ==========================================
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


# ==========================================
# المؤسس: إعدادات أسماء المستخدمين وكلمات المرور (إدارة + مؤسس) — .env
# ==========================================
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


# ==========================================
# لوحة الإدارة: إدارة مستخدمي الفروع
# ==========================================
@app.route('/admin/users')
def admin_users():
    if not _staff_session_ok():
        return redirect(url_for('login'))
    if not _session_admin_or_founder():
        return redirect(url_for('dashboard'))
    users = db.get_branch_users()
    return render_template('admin_users.html', users=users)


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
    return render_template(
        "admin_complaints.html",
        complaints=complaints_list,
        stats=stats,
        filter_branch=branch_filter,
        filter_status=status_filter,
        branch_options=branch_options,
    )


@app.route("/branch/complaints/<int:complaint_id>/reply", methods=["POST"])
@staff_member_required
def branch_complaint_reply(complaint_id: int):
    """
    مدير الفرع يكتب رد على الشكوى → النظام يرسله للعميل (إيميل أو واتساب).
    """
    from logic.mail_service import send_email as _send_email

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

    # ── تسجيل الرد في قاعدة البيانات ──
    db.save_complaint_customer_reply(complaint_id, reply_text)

    if sent_channels:
        flash(f"تم إرسال الرد للعميل عبر: {', '.join(sent_channels)}.", "success")
    elif cust_email or cust_phone:
        flash("تم حفظ الرد — تعذر الإرسال التلقائي، تحقق من إعدادات البريد.", "warning")
    else:
        flash("تم حفظ الرد — لا يوجد بريد أو جوال للعميل لإرساله تلقائياً.", "info")

    return redirect(request.referrer or url_for("dashboard"))


@app.route("/branch/inquiry/<int:inquiry_id>/reply", methods=["POST"])
def branch_reply_inquiry(inquiry_id: int):
    """
    الفرع يرد على استفسار العميل بنص + سعر + صورة اختيارية.
    """
    if not _staff_session_ok():
        return redirect(url_for("login"))

    reply_text = (request.form.get("reply_text") or "").strip()
    branch_price = (request.form.get("branch_price") or "").strip()

    if not reply_text:
        flash("يرجى كتابة نص الرد.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    # رفع صورة اختيارية من الفرع
    branch_image_path = ""
    img_file = request.files.get("branch_image")
    if img_file and img_file.filename:
        ext_img = img_file.filename.rsplit(".", 1)[-1].lower()
        if ext_img in {"png", "jpg", "jpeg", "gif", "webp"}:
            import uuid as _uuid
            unique_img = f"inq_{_uuid.uuid4().hex}.{ext_img}"
            save_path = os.path.join(UPLOAD_FOLDER, unique_img)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            img_file.save(save_path)
            branch_image_path = f"static/uploads/{unique_img}"

    ok = db.reply_to_inquiry(
        inquiry_id=inquiry_id,
        branch_reply=reply_text,
        branch_price=branch_price,
        branch_image_path=branch_image_path,
    )

    if ok:
        # ── إشعار العميل بالبريد إذا كان لديه بريد مسجّل ──
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

        flash("✅ تم إرسال الرد للعميل بنجاح.", "success")
    else:
        flash("حدث خطأ أثناء حفظ الرد.", "danger")

    return redirect(request.referrer or url_for("dashboard"))


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


@app.route("/admin/diagnostics/email")
@staff_member_required
def admin_email_diagnostics():
    """تشخيص إعدادات البريد (JSON) للمستخدمين المخوّلين فقط."""
    from logic.email_diagnostics import run_email_diagnostics

    return jsonify(run_email_diagnostics(db))


@app.route("/admin/diagnostics/full")
@staff_member_required
def admin_full_diagnostics():
    """تشخيص شامل للمشروع (قراءة فقط) للمستخدمين المخوّلين فقط."""
    from logic.project_diagnostics import run_full_diagnostics

    return jsonify(run_full_diagnostics(db, send_alerts=True))


# ==========================================
# البوت والمحادثة الذكية
# ==========================================
def _customer_email_valid(email: str) -> bool:
    e = (email or "").strip().lower()
    if "@" not in e:
        return False
    left, right = e.rsplit("@", 1)
    return bool(left and right and "." in right)


def _parse_customer_login_identifier(raw) -> tuple:
    """
    يعيد (نجاح، النوع 'email'|'phone'، القيمة المعيارية، رسالة خطأ عربية أو None).
    """
    s = (raw or "").strip()
    if not s:
        return False, None, None, "يرجى إدخال البريد أو رقم الجوال"
    if "@" in s:
        e = s.lower().strip()
        if not _customer_email_valid(e):
            return False, None, None, "البريد غير صحيح"
        return True, "email", e, None
    digits = re.sub(r"\D", "", s)
    if len(digits) != 9 or not digits.isdigit():
        return False, None, None, "رقم الجوال يجب أن يكون 9 أرقام"
    return True, "phone", digits, None


@app.route('/')
def index():
    uid = (session.get("user") or "").strip()
    chat_ok = session.get("login_scope") == "chat_customer" and bool(uid)
    return render_template(
        'index.html',
        chat_logged_in=chat_ok,
        chat_user_name=(session.get("user_name") or session.get("name") or ""),
        chat_user_email=(session.get("user_email") or ""),
        chat_user_contact=uid,
    )


# ==========================================
# واجهة الشات الموحّدة (نص + مرفقات)
# ==========================================
@app.route("/api/chat-login", methods=["POST"])
def chat_login():
    """تسجيل دخول زائر الشات مباشرة بالبريد أو جوال (9 أرقام) — بدون OTP."""
    data = request.get_json(silent=True) or {}
    ok, kind, value, err = _parse_customer_login_identifier(data.get("identifier"))
    if not ok or not kind or value is None:
        return jsonify({"ok": False, "error": err or "بيانات غير صالحة"}), 400
    nm = (data.get("name") or "").strip()
    if len(nm) < 2:
        return jsonify(
            {"ok": False, "error": "الاسم مطلوب (حرفان على الأقل) كما في واجهة الشات."}
        ), 400
    prior_customer = (
        db.get_customer_by_email(value)
        if kind == "email"
        else db.get_customer_by_phone(value)
    )
    session.permanent = True
    session["logged_in"] = True
    session["login_scope"] = "chat_customer"
    session["user"] = value
    session["name"] = nm[:120]
    session["user_name"] = nm[:120]
    session["chat_customer_returning_visitor"] = bool(prior_customer)
    if kind == "email":
        session["user_email"] = value
        session.pop("user_phone", None)
    else:
        session["user_phone"] = value
        session["user_email"] = ""
    row = db.get_or_create_customer(
        name=nm[:120],
        email=value if kind == "email" else None,
        phone=value if kind == "phone" else None,
    )
    if row:
        session["customer_id"] = int(row["id"])
        if row.get("email"):
            session["user_email"] = row["email"]
        if row.get("phone"):
            session["user_phone"] = row["phone"]
    return jsonify(
        {
            "ok": True,
            "kind": kind,
            "identifier": value,
            "name": (session.get("name") or session.get("user_name") or ""),
        }
    )


@app.route("/chat-logout", methods=["GET", "POST"])
def chat_visitor_logout():
    """خروج زوار الشات من الصفحة الرئيسية دون توجيه لصفحة دخول الموظفين."""
    if session.get("login_scope") == "chat_customer":
        session.clear()
        return redirect(url_for("index"))
    return redirect(url_for("login"))


@app.route('/chat_query', methods=['POST'])
def chat_query():
    out = chat_query_handler()
    resp_obj = out[0] if isinstance(out, tuple) and len(out) >= 1 else out
    try:
        if hasattr(resp_obj, "get_json"):
            payload = resp_obj.get_json(silent=True)
            logger.info("chat_query response: %s", payload)
        else:
            logger.info("chat_query response: %r", resp_obj)
    except UnicodeEncodeError:
        logger.info("chat_query response: <payload omitted due to console encoding>")
    except Exception:
        logger.exception("chat_query response logging failed")
    return out


if __name__ == '__main__':
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)