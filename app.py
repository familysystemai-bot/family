import hmac
import logging
import json
import os
import re
import threading
import time
import uuid
import atexit
from datetime import timedelta
from functools import wraps

from urllib.parse import urljoin

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    flash,
    current_app,
    Response,
)
from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALLOWED_EXTENSIONS,
    FLASK_DEBUG,
    FLASK_HOST,
    FLASK_PORT,
    FOUNDER_PASSWORD,
    FOUNDER_USERNAME,
    PUBLIC_BASE_URL,
    SECRET_KEY,
    SEO_META_DESCRIPTION,
    SEO_SITE_NAME,
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
from logic.wa_inbox_routes import create_wa_inbox_blueprint
from logic.finance_routes import create_finance_blueprint
from logic.company_info_repository import ALLOWED_COMPANY_INFO_KEYS, parse_delivery_image_urls
from logic.ai_usage_tracker import get_founder_accounting
from logic.database import DatabaseManager
from logic.site_logo import (
    FOUNDER_LOGO_CLOUD_ID_KEY,
    FOUNDER_LOGO_RELATIVE,
    FOUNDER_LOGO_SETTING_KEY,
    SITE_LOGO_CLOUD_ID_KEY,
    SITE_LOGO_RELATIVE,
    SITE_LOGO_SETTING_KEY,
    clear_branding_from_disk_and_settings,
    delete_remote_storage_public_id,
    get_public_logo_url,
    remove_logo_file,
    resolve_site_logo_url,
    save_png_bytes_to_upload_folder,
)
from logic.media_uploads import (
    collect_product_images_from_request,
    file_storage_to_upload,
    normalize_stored_media_ref,
    png_bytes_from_image_bytes,
    upload_branding_image_via_cloud_then_png,
)

ensure_upload_dir()

# استيراد المكتبات الجديدة
try:
    from logic.logger_config import app_logger, error_logger, security_logger
    logger = app_logger
except ImportError:
    logger = logging.getLogger(__name__)

try:
    from logic.api_response import APIResponse
except ImportError:
    APIResponse = None

# الملفات العامة (CSS/JS) من /static/؛ الشعار يُخزَّن تحت static/uploads/ ويُعرَض عبر url_for('static', filename='uploads/...')
# لا تغيّر static_folder إلى uploads فقط — سيُعطّل كل الموارد الثابتة.
app = Flask(__name__, static_folder='static')
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# ── الدفعة 4-ج: تهيئة الأمان (CSRF + Cookie flags + MAX_CONTENT_LENGTH) ──
try:
    from logic.security import init_security as _init_security, csrf_exempt
    _init_security(app)
    logger.info("security module initialized (CSRF + cookie flags + max content)")
except ImportError as _se:
    logger.warning("logic.security not available: %s", _se)
    # fallback آمن: csrf_exempt يصبح no-op لو الموديول غير متاح
    def csrf_exempt(fn):
        return fn


@app.before_request
def _make_session_permanent():
    session.permanent = True


# ── حماية جلسات الموظفين: انتهاء صلاحية بعد 8 ساعات من الخمول ──
_STAFF_IDLE_TIMEOUT_SECONDS = 8 * 3600


@app.before_request
def _enforce_staff_session_timeout():
    role = session.get("role")
    if role not in ("founder", "admin", "branch"):
        return
    now = time.time()
    last_active = session.get("_staff_last_active")
    if last_active is not None:
        if (now - float(last_active)) > _STAFF_IDLE_TIMEOUT_SECONDS:
            session.clear()
            flash("انتهت جلستك بسبب عدم النشاط. يرجى تسجيل الدخول مجدداً.", "warning")
            return redirect(url_for("login"))
    session["_staff_last_active"] = now


db = DatabaseManager()
migrated_branch_passwords = db.migrate_branch_passwords_to_hashes()
if migrated_branch_passwords:
    logger.info(
        "migrated %s legacy branch password(s) to hashes",
        migrated_branch_passwords,
    )
init_chat_service(db)
app.register_blueprint(create_campaign_blueprint(db))
app.register_blueprint(create_wa_inbox_blueprint(db))
app.register_blueprint(create_finance_blueprint(db))


def _persist_company_delivery_images_from_request(database) -> None:
    """يحدّث company_info.delivery_images من نموذج لوحة الإدارة (روابط محفوظة + رفع ملفات)."""
    if request.form.get("company_globals_form") != "1":
        return
    from logic import cloud_storage as cst
    from logic.media_uploads import normalize_stored_media_ref

    raw = request.form.get("delivery_images_json") or "[]"
    try:
        keep = json.loads(raw)
        if not isinstance(keep, list):
            keep = []
    except Exception:
        keep = []
    seen: set[str] = set()
    merged: list[str] = []
    for u in keep:
        s = str(u).strip()
        if s and s not in seen and len(s) < 2000:
            seen.add(s)
            merged.append(s)
    allowed_image_exts = {"png", "jpg", "jpeg", "gif", "webp"}
    upload_folder = current_app.config.get("UPLOAD_FOLDER") or os.path.join(
        current_app.root_path, "static", "uploads"
    )
    for f in request.files.getlist("delivery_image_uploads"):
        if not f or not getattr(f, "filename", None):
            continue
        fn = f.filename
        ext = fn.rsplit(".", 1)[1].lower() if "." in fn else ""
        if ext not in allowed_image_exts:
            continue
        data = f.read()
        if not data:
            continue
        mime = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }.get(ext, "image/jpeg")
        res = cst.upload(data, fn, mime, folder="company-delivery")
        url = (
            normalize_stored_media_ref((res.url or "").strip())
            if getattr(res, "success", False)
            else ""
        )
        if url and url not in seen:
            seen.add(url)
            merged.append(url)
            continue
        unique = f"{uuid.uuid4().hex}.{ext}"
        try:
            os.makedirs(upload_folder, exist_ok=True)
            dest = os.path.join(upload_folder, unique)
            with open(dest, "wb") as out:
                out.write(data)
            rel = f"uploads/{unique}"
            if rel not in seen:
                seen.add(rel)
                merged.append(rel)
        except OSError:
            continue
    database.set_company_info_key(
        "delivery_images", json.dumps(merged[:16], ensure_ascii=False)
    )

# ── Blueprint التكاملات (Cloud Storage / LLM / Payment / Shipping / Invoicing)
try:
    from app_integrations import bp as integrations_bp
    app.register_blueprint(integrations_bp)
    logger.info("integrations blueprint registered")
except ImportError as _e:
    logger.warning("integrations blueprint not available: %s", _e)


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


@app.route("/manifest.json")
def pwa_manifest():
    return current_app.send_static_file("manifest.json")


@app.route("/sw.js")
def pwa_service_worker():
    resp = current_app.send_static_file("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.template_global("media_url")
def template_media_url(path):
    """رابط عرض مورد: URL سحابي كما هو، أو static/uploads عبر url_for."""
    from flask import url_for

    p = (path or "").strip()
    if not p:
        return ""
    if p.startswith(("http://", "https://")):
        return p
    if p.startswith("/static/"):
        return p
    fn = p.lstrip("/")
    if fn.startswith("static/"):
        fn = fn[8:].lstrip("/")
    return url_for("static", filename=fn)


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


@app.route("/finance/gate")
def finance_reports_gate():
    """مسار مختصر للتقارير المالية (خزنة PIN) للمؤسس فقط."""
    if not session.get("logged_in") or not _session_founder_only():
        flash("التقارير المالية متاحة للمؤسس بعد تسجيل الدخول.", "warning")
        return redirect(url_for("login"))
    return redirect(url_for("almanakh_finance.finance_gate"))


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
            session["city_name"] = "النظام"
            session["_staff_last_active"] = time.time()
            flash("مرحباً بك — لوحة تحكم النظام", "success")
            return redirect(url_for("founder_dashboard"))

        if username == ADMIN_USERNAME and password_matches_stored(password, ADMIN_PASSWORD):
            session.clear()
            session.permanent = True
            session['logged_in'] = True
            session['username'] = username
            session['role'] = 'admin'
            session['city_name'] = 'الإدارة العامة'
            session["_staff_last_active"] = time.time()
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
            session["_staff_last_active"] = time.time()
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


def _absolute_public_base() -> str:
    """أساس الرابط العام (يفضّل PUBLIC_BASE_URL على Render/الإنتاج)."""
    b = (PUBLIC_BASE_URL or "").strip().rstrip("/")
    if b:
        return b
    try:
        return (request.url_root or "").strip().rstrip("/")
    except Exception:
        return ""


@app.route("/robots.txt")
def robots_txt():
    base = _absolute_public_base()
    body_lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /login",
        "Disallow: /admin/",
        "Disallow: /dashboard",
        "Disallow: /branch/",
        "Disallow: /founder/",
        "Disallow: /api/",
        "Disallow: /webhook",
        "Disallow: /chat_query",
        "Disallow: /categories/",
        "Disallow: /products",
        "Disallow: /add_product",
        "Disallow: /edit_product",
        "Disallow: /get_sections/",
        "Disallow: /chat-logout",
    ]
    if base:
        body_lines.append(f"Sitemap: {base}/sitemap.xml")
    body_lines.append("")
    return Response("\n".join(body_lines), mimetype="text/plain; charset=utf-8")


@app.route("/sitemap.xml")
def sitemap_xml():
    from datetime import date

    base = _absolute_public_base()
    if not base:
        base = (request.url_root or "").strip().rstrip("/")
    if not base:
        return Response("", status=503, mimetype="application/xml")
    today = date.today().isoformat()
    loc = f"{base}/"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{loc}</loc>"
        f"<lastmod>{today}</lastmod>"
        "<changefreq>daily</changefreq><priority>1.0</priority></url>\n"
        "</urlset>\n"
    )
    return Response(xml, mimetype="application/xml; charset=utf-8")


@app.route('/')
def index():
    uid = (session.get("user") or "").strip()
    chat_ok = session.get("login_scope") == "chat_customer" and bool(uid)
    base = _absolute_public_base()
    canon = f"{base}/" if base else ""
    seo_title = f"{SEO_SITE_NAME} | خدمة العملاء الذكية"
    lu = get_logo_url()
    og_image = ""
    if lu:
        u = str(lu).strip()
        if u.startswith(("http://", "https://")):
            og_image = u.split("?")[0]
        elif base and u.startswith("/"):
            og_image = urljoin(base + "/", u.lstrip("/")).split("?")[0]
    return render_template(
        'index.html',
        chat_logged_in=chat_ok,
        chat_user_name=(session.get("user_name") or session.get("name") or ""),
        chat_user_email=(session.get("user_email") or ""),
        chat_user_contact=uid,
        seo_title=seo_title,
        seo_description=SEO_META_DESCRIPTION,
        seo_canonical_url=canon,
        seo_og_image=og_image or None,
    )


# ==========================================
# واجهة الشات الموحّدة (نص + مرفقات)
# ==========================================
@app.route("/api/chat-login", methods=["POST"])
@csrf_exempt
def chat_login():
    """تسجيل دخول زائر الشات مباشرة بالبريد أو جوال (9 أرقام) — بدون OTP."""
    try:
        data = request.get_json(silent=True) or {}
        ok, kind, value, err = _parse_customer_login_identifier(data.get("identifier"))
        if not ok or not kind or value is None:
            return jsonify({"ok": False, "error": err or "بيانات غير صالحة"}), 400
        nm = (data.get("name") or "").strip()
        if len(nm) < 2:
            return jsonify(
                {"ok": False, "error": "الاسم مطلوب (حرفان على الأقل) كما في واجهة الشات."}
            ), 400
        try:
            prior_customer = (
                db.get_customer_by_email(value)
                if kind == "email"
                else db.get_customer_by_phone(value)
            )
        except Exception:
            logger.exception("chat_login: db lookup failed for %s", kind)
            prior_customer = None
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
        try:
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
        except Exception:
            logger.exception("chat_login: get_or_create_customer failed for %s", kind)
        return jsonify(
            {
                "ok": True,
                "kind": kind,
                "identifier": value,
                "name": (session.get("name") or session.get("user_name") or ""),
            }
        )
    except Exception:
        logger.exception("chat_login: unexpected error")
        return jsonify({"ok": False, "error": "حدث خطأ في الخادم، يرجى المحاولة مرة أخرى."}), 500


@app.route("/chat-logout", methods=["GET", "POST"])
def chat_visitor_logout():
    """خروج زوار الشات من الصفحة الرئيسية دون توجيه لصفحة دخول الموظفين."""
    if session.get("login_scope") == "chat_customer":
        session.clear()
        return redirect(url_for("index"))
    return redirect(url_for("login"))


@app.route('/chat_query', methods=['POST'])
@csrf_exempt
def chat_query():
    # ملاحظة 4-ج: AJAX endpoint من شات الموقع — معفى من CSRF.
    # العميل (chat widget) لا يستطيع تمرير CSRF token من JavaScript بسهولة.
    # الحماية تأتي من: same-origin policy + session-based auth.
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


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp Cloud API Webhook
# ─────────────────────────────────────────────────────────────────────────────
_WA_VERIFY_TOKEN    = os.environ.get("WA_VERIFY_TOKEN", "kazem_token_123")


def _wa_runtime_access_token() -> str:
    """يوكن الإرسال: من system_settings ثم البيئة (منطق موحّد مع لوحة الرسائل)."""
    from logic.wa_credentials import wa_access_token

    return wa_access_token()


def _wa_runtime_phone_number_id() -> str:
    """Phone Number ID من system_settings ثم البيئة."""
    from logic.wa_credentials import wa_phone_number_id

    return wa_phone_number_id()


def _wa_collect_inbound_user_messages(body: dict) -> list:
    """
    يجمع رسائل المستخدم من payload الـ webhook (كل entry / changes).
    يتجاهل changes التي ليست من حقل messages (مثل statuses فقط).
    """
    rows = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            fld = (change.get("field") or "").strip()
            if fld and fld != "messages":
                continue
            val = change.get("value") or {}
            msgs = val.get("messages")
            if not msgs:
                continue
            metadata = val.get("metadata") or {}
            pid = (metadata.get("phone_number_id") or "").strip()
            for msg in msgs:
                if isinstance(msg, dict):
                    rows.append({"msg": msg, "phone_number_id": pid, "value": val})
    return rows


def _wa_sync_contacts_to_customers(db_mgr, value: dict) -> None:
    """يحدّث العملاء من value.contacts (profile.name + wa_id) عند وصول رسالة واتساب."""
    contacts = value.get("contacts")
    if not contacts or not isinstance(contacts, list):
        return
    seen: set[str] = set()
    for c in contacts:
        if not isinstance(c, dict):
            continue
        wa_id = (c.get("wa_id") or "").strip()
        if not wa_id or wa_id in seen:
            continue
        seen.add(wa_id)
        prof = c.get("profile") if isinstance(c.get("profile"), dict) else {}
        disp = (prof.get("name") or "").strip()
        nm = disp[:200] if disp else "ضيف"
        try:
            db_mgr.get_or_create_customer(name=nm, phone=wa_id, branch_id=None)
        except Exception:
            logger.debug(
                "[WA-Webhook] تعذر مزامنة جهة اتصال wa_id=%s", wa_id, exc_info=True
            )


def _wa_inbox_profile_from_value(value: dict, wa_from: str) -> str:
    wf = (wa_from or "").strip()
    for c in value.get("contacts") or []:
        if not isinstance(c, dict):
            continue
        if (c.get("wa_id") or "").strip() == wf:
            prof = c.get("profile") if isinstance(c.get("profile"), dict) else {}
            return (prof.get("name") or "").strip()[:200]
    return ""


def _wa_inbox_branch_for_contact(db, wa_from: str):
    """
    فرع العميل إن وُجد في الجدول ولا يزال الفرع قائماً.
    بدون هذا التحقق قد يفشل INSERT بسبب FOREIGN KEY إن حُذف الفرع ولم يُحدَّث العميل.
    """
    try:
        cust = db.get_customer_by_phone(wa_from)
        if cust and cust.get("branch_id") is not None:
            bid = int(cust["branch_id"])
            if db.get_branch_by_id(bid):
                return bid
    except (TypeError, ValueError):
        pass
    return None


def _wa_inbox_store_inbound(
    db,
    *,
    value: dict,
    wa_from: str,
    message_body: str,
) -> None:
    body = (message_body or "").strip()
    if not body:
        return
    try:
        name = _wa_inbox_profile_from_value(value, wa_from)
        bid = _wa_inbox_branch_for_contact(db, wa_from)
        db.wa_inbox_save_message(
            contact_number=wa_from,
            whatsapp_name=name,
            message_body=body[:50000],
            direction="inbound",
            branch_id=bid,
        )
    except Exception:
        logger.warning(
            "[WA-Webhook] فشل حفظ رسالة في صندوق الواتساب (messages)", exc_info=True
        )


def _wa_normalize_inbound_text(msg: dict) -> tuple[str, str, bool]:
    """
    يستخرج نصاً لمعالجة الشات من رسالة واتساب.
    يعيد: (النص, نوع_meta, هل_يحتاج_مسار_وسائط_كامل)
    """
    msg_type = (msg.get("type") or "").strip()
    if msg_type == "text":
        t = (msg.get("text") or {}).get("body") or ""
        return t.strip(), msg_type, False
    if msg_type == "interactive":
        inter = msg.get("interactive") or {}
        itype = (inter.get("type") or "").strip()
        if itype == "button_reply":
            t = (inter.get("button_reply") or {}).get("title") or ""
            return t.strip(), msg_type, False
        if itype == "list_reply":
            t = (inter.get("list_reply") or {}).get("title") or ""
            return t.strip(), msg_type, False
        if itype == "nfm_reply":
            t = (inter.get("nfm_reply") or {}).get("response_json") or ""
            return str(t).strip(), msg_type, False
        return "", msg_type, False
    if msg_type == "button":
        t = (msg.get("button") or {}).get("text") or ""
        return str(t).strip(), msg_type, False
    if msg_type in ("image", "audio", "document", "video"):
        return "", msg_type, True
    return "", msg_type, False

# ── ذاكرة جلسة واتساب: L1 (ذاكرة العملية، TTL قصير) + L2 (جدول wa_sessions) ──
# L1 يقلل ضغط القراءة من القرص؛ L2 يحافظ على chat_welcome_sent وغيره بعد إعادة التشغيل/عمال Gunicorn.
# البنية: _WA_SESSION_L1[session_id] = {"cached_at", "updated_at", "state"}
_WA_SESSION_L1: dict = {}
_WA_SESSION_CACHE_MAX = 500
_WA_L1_TTL_SEC = 300
_WA_SESSION_IDLE_RESET_SEC = int(os.environ.get("WA_SESSION_IDLE_RESET_SEC", "2700"))
_WA_STATE_KEYS = (
    "pending_inquiry", "chat_last_branch", "chat_selected_branch",
    "last_bot_message", "complaint_active", "complaint_data",
    "complaint_wizard", "complaint_policy_precheck", "pending_intent",
    "chat_pending_action", "chat_current_intent", "user_name",
    "chat_name_declined", "last_inquiry_id", "awaiting_user_name",
    "chat_dialect", "chat_service_turns", "pending_complaint_lookup",
    "user_contact", "name", "chat_pending_branch_phone_offer",
    "chat_islamic_salam_named_count", "chat_intent_score_snapshot",
    "last_intent_category",  # FIXED
    "chat_welcome_sent",
    "complaint_ai_flow",
    "chat_last_product",
    "last_products",
    "pending_product_intent",
    "last_section",
)

# ── منع إعادة معالجة WAMID: L1 + جدول wa_processed_wamids (يثبت عبر إعادة التشغيل) ──
_WA_DEDUPE_LOCK = threading.RLock()
# wamid -> {"processed_at", "cached_at"}
_WA_WAMID_L1: dict[str, dict] = {}
_WA_WAMID_TTL_SEC = int(os.environ.get("WA_WAMID_DEDUPE_TTL_SEC", str(72 * 3600)))
_WA_WAMID_MAX = 10000


def _wa_collect_wamids_from_inbound(inbound: list) -> list[str]:
    out: list[str] = []
    for row in inbound or []:
        m = (row or {}).get("msg") or {}
        mid = (m.get("id") or "").strip()
        if mid and mid not in out:
            out.append(mid)
    return out


def _wa_session_l1_prune() -> None:
    if len(_WA_SESSION_L1) <= _WA_SESSION_CACHE_MAX:
        return
    try:
        oldest_sid = min(
            _WA_SESSION_L1.keys(),
            key=lambda sid: float((_WA_SESSION_L1[sid] or {}).get("cached_at") or 0.0),
        )
        del _WA_SESSION_L1[oldest_sid]
    except (ValueError, KeyError, TypeError):
        try:
            del _WA_SESSION_L1[next(iter(_WA_SESSION_L1))]
        except StopIteration:
            pass


def _wa_wamid_prune() -> None:
    now = time.time()
    cut = now - _WA_WAMID_TTL_SEC
    try:
        db.wa_wamids_delete_before(cut)
    except Exception:
        logger.debug("wa_wamids_delete_before failed (non-fatal)", exc_info=True)
    stale_cut = now - 3600
    for k in list(_WA_WAMID_L1.keys()):
        ent = _WA_WAMID_L1.get(k) or {}
        if float(ent.get("cached_at") or 0) < stale_cut:
            _WA_WAMID_L1.pop(k, None)
    if len(_WA_WAMID_L1) <= _WA_WAMID_MAX:
        return
    sorted_items = sorted(
        _WA_WAMID_L1.items(),
        key=lambda x: float((x[1] or {}).get("cached_at") or 0.0),
    )
    overflow = len(_WA_WAMID_L1) - _WA_WAMID_MAX + 500
    for k, _ in sorted_items[: max(0, overflow)]:
        _WA_WAMID_L1.pop(k, None)


def _wa_all_wamids_already_processed(mids: list[str]) -> bool:
    if not mids:
        return False
    now = time.time()
    need_db: list[str] = []
    with _WA_DEDUPE_LOCK:
        for mid in mids:
            m = (mid or "").strip()
            if not m:
                return False
            ent = _WA_WAMID_L1.get(m)
            if ent and (now - float(ent.get("cached_at") or 0)) < _WA_L1_TTL_SEC:
                continue
            need_db.append(m)
        if need_db:
            try:
                found = db.wa_wamids_fetch_processed(need_db)
            except Exception:
                logger.exception("wa_wamids_fetch_processed")
                return False
            for m in need_db:
                if m not in found:
                    return False
                _WA_WAMID_L1[m] = {
                    "processed_at": float(found[m]),
                    "cached_at": now,
                }
            _wa_wamid_prune()
    return True


def _wa_mark_wamids_processed(mids: list[str]) -> None:
    if not mids:
        return
    now = time.time()
    with _WA_DEDUPE_LOCK:
        try:
            db.wa_wamids_mark_processed(mids, now)
        except Exception:
            logger.exception("wa_wamids_mark_processed")
        for mid in mids:
            m = (mid or "").strip()
            if m:
                _WA_WAMID_L1[m] = {"processed_at": now, "cached_at": now}
        _wa_wamid_prune()


def _wa_should_reset_wa_session_cache(msg: str, cached: dict) -> bool:  # FIXED
    MALE = ["رجالي", "رجل", "حج", "إحرام"]  # FIXED
    FEMALE = ["نسائي", "فستان", "عباية"]  # FIXED
    COMPLAINT = ["شكوى", "أشتكي", "زعلان", "مشكلة"]  # FIXED
    last = (cached.get("last_intent_category") or "") if isinstance(cached, dict) else ""  # FIXED
    if any(w in msg for w in MALE) and last == "female":  # FIXED
        return True  # FIXED
    if any(w in msg for w in FEMALE) and last == "male":  # FIXED
        return True  # FIXED
    if any(w in msg for w in COMPLAINT):  # FIXED
        return True  # FIXED
    return False  # FIXED


def _wa_cache_get_prev_state(session_id: str) -> dict:
    sid = (session_id or "").strip()
    now = time.time()
    if not sid:
        return {}
    with _WA_DEDUPE_LOCK:
        ent = _WA_SESSION_L1.get(sid)
        updated_at = 0.0
        st: dict = {}
        if ent and (now - float(ent.get("cached_at") or 0)) < _WA_L1_TTL_SEC:
            updated_at = float(ent.get("updated_at") or 0)
            st = dict(ent.get("state") or {})
        else:
            row = None
            try:
                row = db.wa_session_load(sid)
            except Exception:
                logger.exception("wa_session_load")
            if row:
                updated_at, st = float(row[0] or 0), dict(row[1] or {})
            else:
                updated_at, st = 0.0, {}
            _WA_SESSION_L1[sid] = {
                "cached_at": now,
                "updated_at": updated_at,
                "state": st,
            }
            _wa_session_l1_prune()
        if isinstance(st, dict) and "state" in st and isinstance(st.get("state"), dict):
            st = dict(st["state"])
        if updated_at and (now - updated_at) > _WA_SESSION_IDLE_RESET_SEC:
            logger.info(
                "[WA-Webhook] تصفير ذاكرة الجلسة بعد سكوت (%ss) — جلسة=%s",
                int(now - updated_at),
                sid[:24],
            )
            return {}
        return st


def _wa_cache_put_state(session_id: str, state: dict) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    now = time.time()
    st = dict(state or {})
    with _WA_DEDUPE_LOCK:
        try:
            db.wa_session_save(sid, now, st)
        except Exception:
            logger.exception("wa_session_save")
        _WA_SESSION_L1[sid] = {"cached_at": now, "updated_at": now, "state": st}
        if len(_WA_SESSION_L1) > _WA_SESSION_CACHE_MAX:
            try:
                oldest_sid = min(
                    _WA_SESSION_L1.keys(),
                    key=lambda s: float((_WA_SESSION_L1[s] or {}).get("cached_at") or 0.0),
                )
                del _WA_SESSION_L1[oldest_sid]
            except (ValueError, KeyError, TypeError):
                try:
                    del _WA_SESSION_L1[next(iter(_WA_SESSION_L1))]
                except StopIteration:
                    pass


def _wa_send_message(phone_number_id: str, to: str, text: str) -> bool:
    """يرسل رسالة نصية عبر WhatsApp Cloud API."""
    import requests as _req

    token = _wa_runtime_access_token()
    if not token:
        logger.warning("[WA] WA_ACCESS_TOKEN غير مضبوط — لا يمكن إرسال الرسالة")
        return False
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        resp = _req.post(url, headers=headers, json=payload, timeout=10)
        if resp.ok:
            logger.info("[WA] رسالة أُرسلت بنجاح إلى %s", to)
            return True
        snip = resp.text[:500] if resp.text else ""
        extra = ""
        if resp.status_code == 401 or "190" in snip or "OAuthException" in snip:
            extra = " — تحديث WA_ACCESS_TOKEN مطلوب (ميتا 401/190)."
        logger.warning(
            "[WA] فشل الإرسال HTTP %s إلى %s — %s%s",
            resp.status_code,
            to,
            snip,
            extra,
        )
        return False
    except Exception:
        logger.exception("[WA] خطأ أثناء إرسال الرسالة")
        return False


def send_typing_indicator(recipient_id: str, phone_number_id: str, message_id: str) -> None:
    """
    إيصال قراءة + مؤشر كتابة (WhatsApp Cloud API).

    يُستدعى مبكراً عند استلام webhook: يعلّم الرسالة كمقروءة ويعرض «يكتب…» للمستخدم.
    recipient_id = رقم المُرسِل (واتساب) — للتماشي مع طلب الواجهة؛ الطلب الفعلي يعتمد على message_id.

    الوثائق: status read + typing_indicator type text في POST /PHONE_NUMBER_ID/messages
    """
    import requests as _req

    try:
        to_log = (recipient_id or "").strip()[:32]
        pid = (phone_number_id or "").strip()
        mid = (message_id or "").strip()
        if not pid or not mid:
            return
        token = _wa_runtime_access_token()
        if not token:
            return
        url = f"https://graph.facebook.com/v19.0/{pid}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        base = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": mid,
        }
        with_typing = dict(base)
        with_typing["typing_indicator"] = {"type": "text"}
        resp = _req.post(url, headers=headers, json=with_typing, timeout=8)
        if resp.ok:
            logger.debug("[WA] read+typing ok msg=%s to=%s", mid[:24], to_log)
            return
        txt = (resp.text or "")[:400]
        if resp.status_code == 401 or "190" in txt or "OAuthException" in txt:
            logger.warning(
                "[WA] read+typing رُفض (401/توكن) — لن يظهر «يكتب…» ولن تُرسل الردود حتى تحدّث WA_ACCESS_TOKEN. | %s",
                txt,
            )
        else:
            logger.debug(
                "[WA] read+typing HTTP %s — retry read-only | %s",
                resp.status_code,
                (resp.text or "")[:200],
            )
        resp2 = _req.post(url, headers=headers, json=base, timeout=8)
        if resp2.ok:
            logger.debug("[WA] read-only ok msg=%s", mid[:24])
        else:
            txt2 = (resp2.text or "")[:400]
            if resp2.status_code == 401 or "190" in txt2 or "OAuthException" in txt2:
                logger.warning(
                    "[WA] read-only رُفض (401/توكن) | %s",
                    txt2,
                )
            else:
                logger.debug(
                    "[WA] read-only HTTP %s %s",
                    resp2.status_code,
                    (resp2.text or "")[:200],
                )
    except Exception:
        logger.debug("[WA] send_typing_indicator failed (non-fatal)", exc_info=True)


def _wa_send_image_link(phone_number_id: str, to: str, image_link: str) -> bool:
    """يرسل صورة عبر WhatsApp Cloud API باستخدام رابط HTTPS مباشر."""
    import requests as _req

    token = _wa_runtime_access_token()
    if not token:
        logger.warning("[WA] WA_ACCESS_TOKEN غير مضبوط — لا يمكن إرسال الصورة")
        return False
    if not phone_number_id:
        logger.warning("[WA] WA_PHONE_NUMBER_ID غير مضبوط — لا يمكن إرسال الصورة")
        return False
    link = (image_link or "").strip()
    if not link.startswith("https://"):
        logger.warning("[WA] رابط الصورة غير صالح (ليس https): %s", link[:200])
        return False
    if not re.search(r"\.(png|jpe?g|gif|webp)(\?.*)?$", link, re.IGNORECASE):
        logger.info(
            "[WA] رابط بدون امتداد صورة ظاهر — ميتا قد تقبله: %s",
            link[:200],
        )

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"link": link},
    }
    try:
        resp = _req.post(url, headers=headers, json=payload, timeout=10)
        if resp.ok:
            logger.info("[WA] صورة أُرسلت بنجاح إلى %s", to)
            return True
        logger.warning(
            "[WA] فشل إرسال الصورة: %s %s | link=%s",
            resp.status_code,
            resp.text[:300],
            link[:200],
        )
        return False
    except Exception:
        logger.exception("[WA] خطأ أثناء إرسال الصورة")
        return False


def _wa_collect_product_image_links(resp_data: dict) -> list:
    """
    روابط https لصور المنتجات — يفضّل image_url (للواتساب)، ثم primary_image_href والمسارات النسبية.
    يُرسل كل رابط كرسالة type=image وليس كنص في المحادثة.
    """
    products = resp_data.get("products") or []
    if not isinstance(products, list):
        return []
    seen: set = set()
    out: list = []
    for p in products:
        if not isinstance(p, dict):
            continue
        link = (p.get("image_url") or "").strip()
        if not link:
            link = (p.get("primary_image_href") or "").strip()
        if not link:
            images = p.get("images") or []
            if isinstance(images, list) and images:
                first = images[0]
                link = (str(first) if first is not None else "").strip()
        if not link:
            im1 = (p.get("img1") or "").strip()
            if im1:
                link = im1
        if not link:
            continue
        if link.startswith("http://"):
            link = "https://" + link[len("http://") :]
        if not link.startswith("https://"):
            try:
                from logic.product_service import _product_image_url_abs_https

                link = (_product_image_url_abs_https(link) or "").strip()
            except Exception:
                link = ""
        if link.startswith("https://") and link not in seen:
            seen.add(link)
            out.append(link)
    return out


def _wa_pick_first_product_image_link(resp_data: dict) -> str:
    """توافق مع الكود القديم: أول رابط صورة منتج لرسالة واتساب."""
    links = _wa_collect_product_image_links(resp_data or {})
    return links[0] if links else ""


def _wa_download_and_extract_text(media_id: str, mime_type: str):
    """
    تحميل الوسائط (صورة/صوت) من WhatsApp Cloud API واستخراج النص منها.
    الصور → Gemini/OpenAI Vision | الصوت → OpenAI Whisper
    """
    import requests as _req
    import tempfile

    if not media_id or not _wa_runtime_access_token():
        return None

    _mime_to_ext = {
        "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/png": "png", "image/webp": "webp", "image/gif": "gif",
        "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
        "audio/opus": "ogg", "audio/ogg; codecs=opus": "ogg",
        "audio/wav": "wav", "audio/webm": "webm", "audio/mp4": "m4a",
        "audio/aac": "m4a", "audio/x-m4a": "m4a", "audio/3gpp": "m4a",
    }
    mime_raw = (mime_type or "").strip().lower()
    mime_base = mime_raw.split(";", 1)[0].strip() if mime_raw else ""
    ext = _mime_to_ext.get(mime_raw, "") or _mime_to_ext.get(mime_base, "")

    headers = {"Authorization": f"Bearer {_wa_runtime_access_token()}"}

    # الخطوة 1: جلب رابط الميديا من Meta
    try:
        meta_resp = _req.get(
            f"https://graph.facebook.com/v19.0/{media_id}",
            headers=headers, timeout=10,
        )
        if not meta_resp.ok:
            logger.warning("[WA-Media] فشل جلب رابط الميديا: %s", meta_resp.text[:200])
            return None
        media_url = meta_resp.json().get("url", "")
        if not media_url:
            return None
    except Exception:
        logger.exception("[WA-Media] خطأ عند جلب رابط الميديا")
        return None

    # الخطوة 2: تحميل الملف
    try:
        dl_resp = _req.get(media_url, headers=headers, timeout=30)
        if not dl_resp.ok:
            logger.warning("[WA-Media] فشل تحميل الملف: %s", dl_resp.status_code)
            return None
    except Exception:
        logger.exception("[WA-Media] خطأ أثناء تحميل الملف")
        return None

    # fallback: بعض webhooks ترسل mime_type ناقص/مختلف، نعتمد Content-Type الفعلي
    if not ext:
        dl_ct = (dl_resp.headers.get("Content-Type") or "").strip().lower()
        dl_ct_base = dl_ct.split(";", 1)[0].strip() if dl_ct else ""
        ext = _mime_to_ext.get(dl_ct, "") or _mime_to_ext.get(dl_ct_base, "")
        if not ext:
            logger.info(
                "[WA-Media] mime غير مدعوم: payload=%s download=%s",
                mime_type,
                dl_resp.headers.get("Content-Type"),
            )
            return None

    # الخطوة 3: حفظ مؤقت واستخراج النص
    try:
        from logic.attachment_openai import text_from_saved_file
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(dl_resp.content)
            tmp_path = tmp.name
        result = text_from_saved_file(tmp_path, ext)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        if isinstance(result, dict):  # FIXED
            return (result.get("message") or "").strip() or None  # FIXED
        return result
    except Exception:
        logger.exception("[WA-Media] خطأ أثناء استخراج النص")
        return None


def process_message(data) -> None:
    """
    معالجة ويبهوك واتساب في خيط خلفي.
    يُستدعى فقط من whatsapp_webhook بعد إرجاع 200 — لا يُشغَّل dispatch_chat_query في طلب الـ HTTP نفسه.
    """
    import json as _json

    if not isinstance(data, dict):
        logger.warning("[WA-Webhook][bg] process_message: payload غير صالح")
        return
    raw_body = data.get("raw_body") or b""
    if not isinstance(raw_body, (bytes, bytearray)):
        raw_body = bytes(raw_body) if raw_body else b""
    remote_addr = str(data.get("remote_addr") or "").strip() or "?"
    sig_header = str(data.get("sig_header") or "")

    logger.info(
        "[WA-Webhook][bg] start len=%s from=%s",
        len(raw_body or b""),
        remote_addr or "?",
    )

    # ── التحقق من توقيع Meta (HMAC-SHA256) — نفس بايتات الجسم للتحقق وللـ JSON ──
    try:
        from logic.security import verify_meta_signature
        from logic.integrations.base import read_setting

        app_secret = (read_setting("META_APP_SECRET", "") or "").strip()
        if not app_secret:
            app_secret = (os.environ.get("META_APP_SECRET", "") or "").strip()

        if app_secret:
            if not verify_meta_signature(raw_body, sig_header or "", app_secret):
                logger.warning(
                    "[WA-Webhook][bg] HMAC فشل — رفض POST. تحقق: App Secret = Settings→Basic في نفس التطبيق، "
                    "بدون فراغات زائدة. | من %s",
                    remote_addr or "unknown",
                )
                return
        else:
            logger.warning(
                "[WA-Webhook][bg] ⚠️ META_APP_SECRET غير مهيّأ — "
                "الـ webhook مفتوح لأي طلب POST! "
                "أضف المفتاح من: لوحة المؤسس → التكاملات → الواتساب"
            )
            if os.environ.get("RENDER") is not None:
                logger.warning(
                    "[WA-Webhook][bg] رفض معالجة الرسالة على Render بدون META_APP_SECRET (أمان)."
                )
                return
    except ImportError:
        logger.warning("[WA-Webhook][bg] security module unavailable; signature verify skipped")
    except Exception as _ve:
        logger.exception("[WA-Webhook][bg] signature verification error: %s", _ve)
        return

    try:
        body = _json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        logger.warning("[WA-Webhook][bg] فشل parse JSON (طول الجسم %s)", len(raw_body or b""))
        body = {}
    logger.info(
        "[WA-Webhook][bg] object=%r head=%s",
        body.get("object"),
        _json.dumps(body, ensure_ascii=False)[:800],
    )

    try:
        inbound = _wa_collect_inbound_user_messages(body)
        if not inbound:
            logger.info(
                "[WA-Webhook][bg] لا messages مستخدم (غالباً statuses فقط); object=%r",
                body.get("object"),
            )
            return

        wamids_batch = _wa_collect_wamids_from_inbound(inbound)
        with _WA_DEDUPE_LOCK:
            if wamids_batch and _wa_all_wamids_already_processed(wamids_batch):
                logger.info(
                    "[WA-Webhook][bg] تخطّي — سبقت معالجة معرفات الرسائل (إعادة توصيل ميتا): %s",
                    wamids_batch[:8],
                )
                return
            if wamids_batch:
                _wa_mark_wamids_processed(wamids_batch)

        row0 = inbound[0]
        value_full = row0.get("value") or {}
        try:
            _wa_sync_contacts_to_customers(db, value_full)
        except Exception:
            logger.debug("[WA-Webhook][bg] مزامنة contacts (غير حرجة)", exc_info=True)

        msg = row0["msg"]
        phone_id = (row0.get("phone_number_id") or "").strip() or _wa_runtime_phone_number_id()
        send_phone_id = (_wa_runtime_phone_number_id() or phone_id or "").strip()
        wa_from = (msg.get("from") or "").strip()
        msg_type = (msg.get("type") or "").strip()

        logger.info(
            "[WA-Webhook][bg] نوع=%s من=%s send_phone_id=%s",
            msg_type,
            wa_from,
            send_phone_id or "(فارغ)",
        )

        if not wa_from:
            logger.info("[WA-Webhook][bg] لا يوجد from — تجاهل")
            return

        if not send_phone_id:
            logger.warning("[WA-Webhook][bg] Phone Number ID مفقود — لن يُرسل رد API")
            w_quick, _, im_quick = _wa_normalize_inbound_text(msg)
            if im_quick:
                mt_q = (msg.get("type") or "").strip()
                media_obj = msg.get(mt_q) or {}
                if isinstance(media_obj, dict):
                    cap_q = (media_obj.get("caption") or "").strip()
                    w_quick = cap_q or f"[واتساب:{mt_q or 'media'}]"
                else:
                    w_quick = f"[واتساب:{mt_q or 'media'}]"
            if not w_quick:
                mt_fallback = (msg.get("type") or "").strip()
                w_quick = f"[واتساب:{mt_fallback}]" if mt_fallback else ""
            if w_quick:
                _wa_inbox_store_inbound(
                    db,
                    value=value_full,
                    wa_from=wa_from,
                    message_body=w_quick,
                )
            return

        wa_msg_id = (msg.get("id") or "").strip()
        if wa_msg_id:
            try:
                send_typing_indicator(wa_from, send_phone_id, wa_msg_id)
            except Exception:
                logger.debug("[WA-Webhook][bg] read/typing wrapper failed (non-fatal)", exc_info=True)

        wa_text, norm_type, is_media = _wa_normalize_inbound_text(msg)

        if is_media:
            media_obj = msg.get(msg_type) or {}
            media_id = media_obj.get("id", "")
            mime_type = media_obj.get("mime_type", "")
            caption = (media_obj.get("caption") or "").strip()

            logger.info("[WA-Webhook][bg] media type=%s id=%s mime=%s", msg_type, media_id, mime_type)

            # ── TASK 1: فحص إذا كان تحليل الصور معطلاً ──
            _is_image_type = (
                msg_type in ("image", "sticker")
                or (mime_type or "").startswith("image/")
            )
            if _is_image_type:
                try:
                    from logic.integrations.base import read_setting as _rs
                    _img_enabled = (_rs("image_analysis_enabled", "0") or "0").strip()
                except Exception:
                    _img_enabled = "1"
                if _img_enabled == "0":
                    _inbox_body = caption if caption else f"[صورة واتساب:{msg_type}]"
                    _wa_inbox_store_inbound(
                        db,
                        value=value_full,
                        wa_from=wa_from,
                        message_body=_inbox_body,
                    )
                    _wa_send_message(
                        send_phone_id,
                        wa_from,
                        "معذرة، حالياً ما أقدر أتعرف على الصورة، لكن أبشر تم إرسالها للفرع وسيتم الرد عليك قريباً.",
                    )
                    return

            extracted = _wa_download_and_extract_text(media_id, mime_type)

            if extracted:
                wa_text = (caption + "\n" + extracted).strip() if caption else extracted
            elif caption:
                wa_text = caption
            else:
                fail_body = caption or f"[واتساب:{msg_type}]"
                _wa_inbox_store_inbound(
                    db,
                    value=value_full,
                    wa_from=wa_from,
                    message_body=fail_body,
                )
                _wa_send_message(
                    send_phone_id,
                    wa_from,
                    "عذراً، لا أستطيع معالجة هذا النوع من الملفات حالياً.",
                )
                return
        elif not wa_text:
            logger.info("[WA-Webhook][bg] نوع غير مدعوم أو بلا نص قابل للمعالجة: %s", msg_type)
            _wa_inbox_store_inbound(
                db,
                value=value_full,
                wa_from=wa_from,
                message_body=f"[واتساب:{msg_type}]",
            )
            return

        # ── TASK 2: فحص تحكم العميل (حظر / إيقاف AI) — بدون مسار المحلّل ──
        from logic.wa_inbox_repository import WA_BLOCKED_AI_AUTOREPLY_AR

        try:
            _controls = db.wa_contact_get_controls(wa_from)
        except Exception:
            _controls = {"ai_stopped": 0, "banned": 0}

        if _controls.get("banned"):
            _ban_body = wa_text or f"[واتساب:{msg_type}]"
            _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=_ban_body)
            _wa_send_message(
                send_phone_id,
                wa_from,
                WA_BLOCKED_AI_AUTOREPLY_AR,
            )
            return

        if _controls.get("ai_stopped"):
            _stop_body = wa_text or f"[واتساب:{msg_type}]"
            _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=_stop_body)
            return

        if len(inbound) > 1:
            _merged_parts = []
            for _i, _inv in enumerate(inbound):
                if _i == 0:
                    if wa_text:
                        _merged_parts.append(wa_text)
                else:
                    _w_extra, _, __ = _wa_normalize_inbound_text(_inv["msg"])
                    if _w_extra:
                        _merged_parts.append(_w_extra)
            if _merged_parts:
                wa_text = "\n".join(_merged_parts).strip()

        _wa_inbox_store_inbound(
            db,
            value=value_full,
            wa_from=wa_from,
            message_body=wa_text,
        )

        logger.info(
            "[WA-Webhook][bg] → الشات: نوع=%s نص=%s",
            norm_type or msg_type,
            (wa_text[:200] + "…") if len(wa_text) > 200 else wa_text,
        )

        wa_session_id = f"wa_{wa_from}"
        fake_body = _json.dumps({"message": wa_text}).encode("utf-8")

        _prev_state = _wa_cache_get_prev_state(wa_session_id)
        welcome_needed = not bool((_prev_state or {}).get("chat_welcome_sent"))
        wa_profile_nm = _wa_inbox_profile_from_value(value_full, wa_from)
        if wa_profile_nm:
            _generic_nm = {
                "",
                "أخوي",
                "حضرتك",
                "ضيف",
                "عميلنا",
                "العميل",
                "زائر",
            }
            cur_nm = (_prev_state.get("user_name") or "").strip()
            if (not cur_nm) or (cur_nm in _generic_nm):
                _prev_state = dict(_prev_state)
                _prev_state["user_name"] = wa_profile_nm[:120]
        _captured_state: dict = {}

        with app.test_request_context(
            "/chat_query",
            method="POST",
            data=fake_body,
            content_type="application/json",
            environ_base={"REMOTE_ADDR": wa_from},
        ):
            from flask import session as _sess

            _sess["user_id"] = wa_session_id
            _sess["sid"] = wa_session_id

            if _wa_should_reset_wa_session_cache(wa_text, _prev_state):
                _prev_state = {}

            for _k, _v in _prev_state.items():
                if _k not in ("user_id", "sid"):
                    _sess[_k] = _v

            _sess.modified = True

            from logic.chat_router import dispatch_chat_query
            result = dispatch_chat_query()

            _captured_state = {
                k: _sess[k]
                for k in _WA_STATE_KEYS
                if k in _sess and _sess[k] is not None
            }

        resp_obj = result[0] if isinstance(result, tuple) else result
        resp_data = (resp_obj.get_json(silent=True) or {}) if hasattr(resp_obj, "get_json") else {}
        reply_text = (resp_data.get("message") or "").strip()
        if welcome_needed:
            display_name = (wa_profile_nm or _captured_state.get("user_name") or "").strip()
            # عرض الحالة أو جمل طويلة كاسم واتساب يفسد سطر الترحيب — نستخدم «العميل»
            welcome_name = (display_name if display_name else "العميل").strip()
            if len(welcome_name) > 36 or welcome_name.count(" ") >= 4:
                welcome_name = "العميل"
            welcome_line = f"أهلاً بك في مجمع العائلة أستاذ {welcome_name}"
            reply_text = f"{welcome_line}\n{reply_text}" if reply_text else welcome_line
            _captured_state["chat_welcome_sent"] = True

        if _captured_state:
            _wa_cache_put_state(wa_session_id, _captured_state)

        logger.info(
            "[WA-Webhook][bg] طول_رد=%s مفاتيح_json=%s",
            len(reply_text),
            list(resp_data.keys())[:15],
        )

        if not reply_text:
            logger.warning("[WA-Webhook][bg] dispatch أعاد رداً فارغاً — user=%s", wa_from)

        for image_link in _wa_collect_product_image_links(resp_data):
            if send_phone_id:
                _wa_send_image_link(send_phone_id, wa_from, image_link)

        if reply_text and send_phone_id:
            _sent_ok = _wa_send_message(send_phone_id, wa_from, reply_text)
            if not _sent_ok:
                logger.warning("[WA-Webhook][bg] فشل إرسال الرد لواتساب للمستخدم %s", wa_from)
            else:
                # ── TASK 3: حفظ رد AI في صندوق الرسائل لعرضه في لوحة التحكم ──
                try:
                    _ai_bid = _wa_inbox_branch_for_contact(db, wa_from)
                    db.wa_inbox_save_message(
                        contact_number=wa_from,
                        whatsapp_name="AI",
                        message_body=reply_text,
                        direction="outbound",
                        branch_id=_ai_bid,
                        sender_type="ai",
                    )
                except Exception:
                    logger.debug("[WA-Webhook][bg] حفظ رد AI (غير حرج)", exc_info=True)

    except Exception:
        logger.exception("[WA-Webhook][bg] error while processing message")


# ==========================================
# TASK 1: إدارة مزود تحليل الصور
# ==========================================

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


@app.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
@csrf_exempt
def whatsapp_webhook():
    """
    WhatsApp Cloud API — مسار ثابت حرفياً /webhook (بدون Blueprint ولا url_prefix).

    GET: تحقق امتلاك الرابط من Meta (hub.mode / hub.verify_token / hub.challenge).
    POST: استقبال الرسائل والأحداث — نفس منطق /chat_query؛ تحقق HMAC عبر META_APP_SECRET عند التوفر.
    """
    if request.method == "GET":
        # نفس التوكن المعرّف في لوحة المؤسس → التكاملات → واتساب (DB) ثم env
        try:
            from logic.integrations.base import read_setting

            expected = read_setting("WA_VERIFY_TOKEN", _WA_VERIFY_TOKEN)
        except Exception:
            expected = _WA_VERIFY_TOKEN

        mode = request.args.get("hub.mode", "")
        token = request.args.get("hub.verify_token", "")
        challenge = request.args.get("hub.challenge", "")

        # Meta يرسل أحياناً GET بدون params — رد نصّي بسيط (ليس JSON/HTML)
        if not mode and not token:
            return Response("ok", status=200, mimetype="text/plain")

        # مطلوب Meta: 200 + body = hub.challenge كنص خام، Content-Type مناسب
        if mode == "subscribe" and expected and token:
            if hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
                logger.info("[WA-Webhook] Verification successful ✅")
                return Response(challenge, status=200, mimetype="text/plain")

        logger.warning("[WA-Webhook] Verification failed ❌ — mode=%s", mode)
        return Response("Forbidden", status=403, mimetype="text/plain")

    # إقرار 200 فوراً لميتا: المعالجة (ومنها dispatch_chat_query) تتم فقط داخل الخيط.
    data = {
        "raw_body": request.get_data(cache=True) or b"",
        "remote_addr": request.remote_addr or "unknown",
        "sig_header": request.headers.get("X-Hub-Signature-256", ""),
    }
    threading.Thread(target=process_message, args=(data,), daemon=True).start()
    return jsonify({"status": "ok"}), 200


# ── معالجات الأخطاء العامة ──
@app.errorhandler(404)
def not_found(error):
    """معالج الصفحة غير الموجودة."""
    logger.warning(f"404 Not Found: {request.path}")
    if APIResponse:
        return APIResponse.not_found("الصفحة غير موجودة")
    return jsonify({"ok": False, "error": "الصفحة غير موجودة"}), 404

@app.errorhandler(403)
def forbidden(error):
    """معالج الوصول الممنوع."""
    logger.warning(f"403 Forbidden: {request.path} - {request.remote_addr}")
    if APIResponse:
        return APIResponse.forbidden("غير مصرح بالوصول إلى هذا المورد")
    return jsonify({"ok": False, "error": "غير مصرح"}), 403

@app.errorhandler(500)
def internal_error(error):
    """معالج الخطأ الداخلي."""
    logger.exception(f"500 Internal Server Error: {error}")
    if APIResponse:
        return APIResponse.error("خطأ داخلي في الخادم", 500)
    return jsonify({"ok": False, "error": "خطأ داخلي"}), 500

@app.errorhandler(400)
def bad_request(error):
    """معالج الطلب السيء."""
    logger.warning(f"400 Bad Request: {error}")
    if APIResponse:
        return APIResponse.error("طلب سيء", 400)
    return jsonify({"ok": False, "error": "طلب سيء"}), 400

if __name__ == '__main__':
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)