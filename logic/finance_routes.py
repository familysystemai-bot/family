# -*- coding: utf-8 -*-
"""المركز المالي — المناخ | خزنة PIN + لوحة داكنة + ربط API + محلّل مانوس."""
from __future__ import annotations

import hashlib
import logging
import secrets
import string
import time

from werkzeug.security import check_password_hash, generate_password_hash
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

from config import SECRET_KEY
from logic import finance_constants as FC
from logic.finance_amazon_client import fetch_financial_dashboard
from logic.finance_analyst_service import build_financial_context_payload, generate_insights_brief, run_financial_llm
from logic import finance_settings as fin_set
from logic.mail_service import send_email
from logic.security import csrf_exempt

logger = logging.getLogger(__name__)


def founder_brand_logo_url(db) -> str:
    try:
        from logic.site_logo import get_public_logo_url, resolve_site_logo_url

        r = resolve_site_logo_url(db)
        return get_public_logo_url(r or "")
    except Exception:
        return ""


def _otp_hash(code: str) -> str:
    return hashlib.sha256(("ALMANAKH_OTP|" + (code or "")).encode("utf-8")).hexdigest()


def _mask_url_fragment(s: str) -> str:
    s = (s or "").strip()
    if len(s) < 8:
        return "—"
    return s[:12] + "…" + s[-10:]


def _grant_unlock():
    session[FC.SESSION_OK] = True
    session[FC.SESSION_UNLOCK_UNTIL] = time.time() + FC.TTL_SECONDS_DEFAULT


def _unlock_valid() -> bool:
    until = float(session.get(FC.SESSION_UNLOCK_UNTIL) or 0)
    if until < time.time():
        session.pop(FC.SESSION_OK, None)
        session.pop(FC.SESSION_UNLOCK_UNTIL, None)
        session.pop("finance_recovery_flow", None)
        return False
    return bool(session.get(FC.SESSION_OK))


def _founder_gate():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    if session.get("role") != "founder":
        flash("التقارير المالية متاحة للمؤسس فقط.", "warning")
        return redirect(url_for("founder_dashboard"))
    return None


def _vault_pin_digest_exists(db) -> bool:
    h = db.get_system_setting(FC.KEY_PIN_HASH)
    return bool((h or "").strip())


def _pin_policy_ok(pin: str) -> tuple[bool, str]:
    p = pin or ""
    if not FC.PIN_MIN_LEN <= len(p) <= FC.PIN_MAX_LEN:
        return False, "طول رمز الدخول غير مقبول (٦–١٠ أحرف)."
    return True, ""


def _pin_hash_storage(pin: str) -> str:
    try:
        return generate_password_hash(pin, method="scrypt")
    except ValueError:
        return generate_password_hash(pin)


def create_finance_blueprint(database):  # DatabaseManager
    db = database
    bp = Blueprint("almanakh_finance", __name__, url_prefix="/founder/finance")

    # ── الواجهات ───────────────────────────

    @bp.route("/")
    def finance_gate():
        g = _founder_gate()
        if g is not None:
            return g

        sk = SECRET_KEY or ""

        # أول تهيئة: إعداد رمز واحتياطي إيميل
        if not fin_set.vault_configured(db) or not _vault_pin_digest_exists(db):
            return redirect(url_for("almanakh_finance.finance_setup"))

        if _unlock_valid():
            return redirect(url_for("almanakh_finance.finance_dashboard"))

        recovery_email = fin_set.load_recovery_email(db, sk)
        return render_template(
            "founder/finance_gate.html",
            vault_ready=True,
            has_recovery_hint=bool((recovery_email or "").strip()),
            founder_logo_url=founder_brand_logo_url(db),
        )

    @bp.route("/setup", methods=["GET", "POST"])
    def finance_setup():
        g = _founder_gate()
        if g is not None:
            return g
        sk = SECRET_KEY or ""

        if fin_set.vault_configured(db) and _vault_pin_digest_exists(db):
            flash("تم تهيئة الخزنة من قبل.", "info")
            return redirect(url_for("almanakh_finance.finance_gate"))

        if request.method == "POST":
            pin_a = request.form.get("pin") or ""
            pin_b = request.form.get("pin_confirm") or ""
            recovery = (request.form.get("recovery_email") or "").strip().lower()

            okp, errmsg = _pin_policy_ok(pin_a)
            if not okp or pin_a != pin_b:
                flash(errmsg if not okp else "تأكيد الرمز غير متطابق.", "danger")
                return redirect(url_for("almanakh_finance.finance_setup"))

            if "@" not in recovery or "." not in recovery.split("@")[-1]:
                flash("بريد الاستعادة غير صالح.", "danger")
                return redirect(url_for("almanakh_finance.finance_setup"))

            try:
                db.set_system_setting(FC.KEY_PIN_HASH, _pin_hash_storage(pin_a))
                fin_set.save_recovery_email_encrypted(db, sk, recovery)
                db.set_system_setting(FC.KEY_SETUP_COMPLETE, "1")
                flash("تم إنشاء خزنة التقارير المالية وحفظ بريد الاستعادة بتشفير.", "success")
                _grant_unlock()
                return redirect(url_for("almanakh_finance.finance_dashboard"))
            except RuntimeError as e:
                flash(str(e), "danger")
                return redirect(url_for("almanakh_finance.finance_setup"))

        return render_template("founder/finance_setup.html", founder_logo_url=founder_brand_logo_url(db))

    @bp.route("/unlock", methods=["POST"])
    def finance_unlock_pin():
        g = _founder_gate()
        if g is not None:
            return g

        pin = request.form.get("pin") or ""
        hist = db.get_system_setting(FC.KEY_PIN_HASH) or ""

        if not check_password_hash(hist, pin):
            flash("رمز المركز غير صحيح.", "danger")
            return redirect(url_for("almanakh_finance.finance_gate"))

        _grant_unlock()
        return redirect(url_for("almanakh_finance.finance_dashboard"))

    @bp.route("/forgot", methods=["GET", "POST"])
    def finance_forgot():
        g = _founder_gate()
        if g is not None:
            return g

        sk = SECRET_KEY or ""

        step = request.args.get("step") or "1"

        if request.method == "POST" and request.form.get("_step") == "1":
            em = (request.form.get("recovery_email") or "").strip().lower()
            saved = fin_set.load_recovery_email(db, sk).strip().lower()
            if not saved or saved != em:
                flash("البريد لا يطابق البريد الاحتياطي المسجل في الخزنة.", "danger")
                return redirect(url_for("almanakh_finance.finance_forgot", step="1"))

            otp = "".join(secrets.choice(string.digits) for _ in range(8))
            db.set_system_setting(FC.KEY_RECOVERY_OTP_HASH, _otp_hash(otp))
            db.set_system_setting(FC.KEY_RECOVERY_OTP_EXPIRES, str(int(time.time()) + 3600))
            session["finance_recovery_flow"] = 1

            body = (
                "رمز استعادة واحد لاستخدامه خلال ساعة لمركز المناخ المالي:\n\n"
                f"{otp}\n\nإن لم تطلب ذلك، تجاهل الرسالة."
            )
            if send_email([em], "رمز استعادة — المركز المالي المناخ", body):
                flash("أُرسِل رمزاً لمرة واحدة إلى بريدك الاحتياطي.", "success")
            else:
                flash(
                    "تعذّر الإرسال عبر SMTP. راجع متغيرات SENDER_EMAIL / SENDER_PASSWORD. "
                    "(للمطور: راجع أيضاً السجلات.)",
                    "warning",
                )
                logger.warning("recovery OTP generated for %s (mail may have failed)", em[:40])

            return redirect(url_for("almanakh_finance.finance_forgot", step="2"))

        if request.method == "POST" and request.form.get("_step") == "2":
            if not session.get("finance_recovery_flow"):
                flash("ابدأ بخطوة التحقق من البريد أولاً.", "warning")
                return redirect(url_for("almanakh_finance.finance_forgot", step="1"))

            otp_in = (request.form.get("otp") or "").strip()
            hp = (db.get_system_setting(FC.KEY_RECOVERY_OTP_HASH) or "").strip()
            exp_raw = db.get_system_setting(FC.KEY_RECOVERY_OTP_EXPIRES) or "0"

            try:
                exp_ts = float(exp_raw)
            except ValueError:
                exp_ts = 0.0

            if (
                time.time() > exp_ts
                or not otp_in
                or not hp
                or hp != _otp_hash(otp_in)
            ):
                flash("الرمز منتهي أو غير صحيح.", "danger")
                return redirect(url_for("almanakh_finance.finance_forgot", step="2"))

            np1 = request.form.get("new_pin") or ""
            np2 = request.form.get("new_pin_confirm") or ""

            okp, errmsg = _pin_policy_ok(np1)
            if not okp or np1 != np2:
                flash(errmsg if not okp else "تأكيد الرمز الجديد غير متطابق.", "danger")
                return redirect(url_for("almanakh_finance.finance_forgot", step="2"))

            db.set_system_setting(FC.KEY_PIN_HASH, _pin_hash_storage(np1))
            db.set_system_setting(FC.KEY_RECOVERY_OTP_HASH, "")
            db.set_system_setting(FC.KEY_RECOVERY_OTP_EXPIRES, "0")
            session.pop("finance_recovery_flow", None)
            flash("تم تغيير رمز المركز المالي وأُلغي رمز الاستعادة القديم.", "success")
            _grant_unlock()
            return redirect(url_for("almanakh_finance.finance_dashboard"))

        return render_template(
            "founder/finance_forgot.html",
            step=("2" if step == "2" else "1"),
            founder_logo_url=founder_brand_logo_url(db),
        )

    @bp.route("/leave")
    def finance_leave():
        session.pop(FC.SESSION_OK, None)
        session.pop(FC.SESSION_UNLOCK_UNTIL, None)
        session.pop("finance_recovery_flow", None)
        flash("خرجت من بيئة التحليلات المالية وتُستهلك الجلسة.", "info")
        return redirect(url_for("founder_dashboard"))

    @bp.route("/dashboard")
    def finance_dashboard():
        g = _founder_gate()
        if g is not None:
            return g

        if not fin_set.vault_configured(db) or not _vault_pin_digest_exists(db):
            return redirect(url_for("almanakh_finance.finance_setup"))
        if not _unlock_valid():
            return redirect(url_for("almanakh_finance.finance_gate"))

        ac = fin_set.load_amazon_credentials(db, SECRET_KEY or "")
        dash = fetch_financial_dashboard(
            db=db,
            base_url=ac["base_url"],
            api_key=ac["api_key"],
            api_secret=ac["secret"],
        )

        ai = fin_set.load_ai_credentials(db, SECRET_KEY or "")
        placeholders = {"has_ai": bool(ai["api_key"]), "amazon_url_hint": "***"}
        founder_logo_url = founder_brand_logo_url(db)

        branches_dd: list[dict] = []
        try:
            for b in db.get_all_branches() or []:
                bid = int(b.get("id") or 0)
                label = ((b.get("city_name") or b.get("branch_name") or "") or str(bid)).strip()
                branches_dd.append({"id": bid, "label": label})
        except Exception:
            branches_dd = []

        return render_template(
            "founder/finance_dashboard.html",
            metrics=dash,
            ai_provider_pref=ai["provider"],
            ai_model_pref=ai["model"] or "",
            amazon_base_preview=(ac["base_url"] or "")[:64],
            placeholders=placeholders,
            founder_logo_url=founder_logo_url,
            branches_dd=branches_dd,
        )

    @bp.route("/hub", methods=["GET", "POST"])
    def finance_hub():
        g = _founder_gate()
        if g is not None:
            return g
        if not fin_set.vault_configured(db) or not _vault_pin_digest_exists(db):
            return redirect(url_for("almanakh_finance.finance_setup"))
        if not _unlock_valid():
            return redirect(url_for("almanakh_finance.finance_gate"))

        sk = SECRET_KEY or ""
        ai = fin_set.load_ai_credentials(db, sk)
        am = fin_set.load_amazon_credentials(db, sk)

        if request.method == "POST":
            pw = request.form.get("finance_hub_pin_confirm") or ""
            hist = db.get_system_setting(FC.KEY_PIN_HASH) or ""
            if not check_password_hash(hist, pw):
                flash("أدخل رمز المركز المالي الحالي لتأكيد حفظ مفاتيح الربط.", "danger")
                return redirect(url_for("almanakh_finance.finance_hub"))

            o = request.form.get("finance_change_old_pin") or ""
            n1 = (request.form.get("finance_change_pin") or "").strip()
            n2 = (request.form.get("finance_change_pin_confirm") or "").strip()
            if n1:
                hist2 = db.get_system_setting(FC.KEY_PIN_HASH) or ""
                if not o or not check_password_hash(hist2, o):
                    flash("فشل تغيير الرمز: تعذّر التحقق من الرمز القديم.", "danger")
                    return redirect(url_for("almanakh_finance.finance_hub"))

                okp2, errmsg2 = _pin_policy_ok(n1)
                if not okp2 or n1 != n2:
                    flash(errmsg2 if not okp2 else "تأكيد الرمز الجديد غير متطابق.", "danger")
                    return redirect(url_for("almanakh_finance.finance_hub"))
                db.set_system_setting(FC.KEY_PIN_HASH, _pin_hash_storage(n1))

            amazon_base_raw = request.form.get("amazon_base_url", "").strip()
            amazon_api_raw = request.form.get("amazon_api_key", "").strip()
            amazon_secret_raw = request.form.get("amazon_secret", "").strip()

            amazon_base_final = amazon_base_raw if amazon_base_raw.startswith(
                ("http://", "https://")
            ) else ""
            if not amazon_base_final:
                amazon_base_final = (am["base_url"] or "").strip()
                if amazon_base_raw and not amazon_base_raw.startswith(("http://", "https://")):
                    flash("لم يُعتمد الرابط المعطى — يُشترط http/https؛ أُعيد المحفوظ.", "warning")

            amazon_api_final = amazon_api_raw if amazon_api_raw else am["api_key"]
            amazon_secret_final = amazon_secret_raw if amazon_secret_raw else am["secret"]

            ai_prov = (
                request.form.get("finance_ai_provider") or ai["provider"]
            ).strip().lower()[:32]
            ai_model = (
                request.form.get("finance_ai_model") or ai["model"]
            ).strip()[:160]
            ai_api_k = request.form.get("finance_ai_api_key", "").strip()
            ai_key_final = ai_api_k if ai_api_k else ai["api_key"]

            fin_set.save_vault_credentials(
                db,
                app_secret=sk or "",
                amazon_base_url=str(amazon_base_final).strip(),
                amazon_api_key=str(amazon_api_final).strip(),
                amazon_secret=str(amazon_secret_final).strip(),
                ai_provider=ai_prov,
                ai_model=ai_model,
                ai_api_key=ai_key_final,
            )

            tail = ""
            if n1:
                tail += " وتغيّر رمز المركز أيضاً."
            flash("تم حفظ الإعدادات بتشفير AES-256-GCM لمفاتيح الـ API والأسرار." + tail, "success")
            return redirect(url_for("almanakh_finance.finance_dashboard"))

        return render_template(
            "founder/finance_hub.html",
            amazon_base_url_masked=_mask_url_fragment(am["base_url"]),
            ai_provider_pref=ai["provider"],
            ai_model_pref=ai["model"] or "",
            has_amazon_api_key=bool(am["api_key"]),
            has_ai_key=bool(ai["api_key"]),
            founder_logo_url=founder_brand_logo_url(db),
        )

    # ── JSON APIs ────────────────────────────

    @bp.route("/api/metrics.json")
    def api_metrics():
        fg = _founder_gate()
        if fg is not None:
            return jsonify({"ok": False, "error": "غير مصرح"}), 401
        if not _unlock_valid():
            return jsonify({"ok": False, "error": "قفل الخزنة"}), 403

        ac = fin_set.load_amazon_credentials(db, SECRET_KEY or "")
        dash = fetch_financial_dashboard(
            db=db,
            base_url=ac["base_url"],
            api_key=ac["api_key"],
            api_secret=ac["secret"],
        )
        out = dict(dash)
        out.pop("inventory_signal", None)  # اختصار عرض — يُحمَّل لاحقاً عند الطلب
        return jsonify({"ok": True, "metrics": out})

    @bp.route("/api/insights.json")
    def api_insights():
        fg = _founder_gate()
        if fg is not None:
            return jsonify({"ok": False}), 401
        if not _unlock_valid():
            return jsonify({"ok": False, "error": "locked"}), 403

        ac = fin_set.load_amazon_credentials(db, SECRET_KEY or "")
        dash = fetch_financial_dashboard(
            db=db,
            base_url=ac["base_url"],
            api_key=ac["api_key"],
            api_secret=ac["secret"],
        )
        ai = fin_set.load_ai_credentials(db, SECRET_KEY or "")
        if not ai["api_key"]:
            txt = (
                "لم يُضبَط مفتاح المحلّل الذكي في «مركز الربط». "
                "بعد ضبطه يُمكن توليد رؤى تلقائية من مانوس إلى جانب مقاييس اليوم المعروضة."
            )
            return jsonify({"ok": True, "text": txt, "placeholder": True})

        brief, err = generate_insights_brief(
            db, dash, ai["provider"], ai["api_key"], ai["model"]
        )
        if err:
            return jsonify({"ok": False, "error": err[:400]}), 502

        return jsonify({"ok": True, "text": brief or "—"})

    @bp.route("/api/inventory-snippet.json")
    def api_inventory_snippet_lazy():
        fg = _founder_gate()
        if fg is not None:
            return jsonify({"ok": False}), 401
        if not _unlock_valid():
            return jsonify({"ok": False}), 403
        inv = {}
        try:
            n = db.count_products_total()
            inv["products_registered"] = int(n)
        except Exception:
            inv["products_registered"] = None
        return jsonify({"ok": True, "inventory_signal": inv})

    @bp.route("/api/chat", methods=["POST"])
    @csrf_exempt
    def api_finance_chat():
        fg = _founder_gate()
        if fg is not None:
            return jsonify({"ok": False, "error": "غير مصرح"}), 401
        if not _unlock_valid():
            return jsonify({"ok": False, "error": "locked"}), 403

        payload = request.get_json(silent=True) or {}
        q = (payload.get("message") or "").strip()
        if len(q) < 2:
            return jsonify({"ok": False, "error": "نصّ قصير جداً."}), 400

        ai = fin_set.load_ai_credentials(db, SECRET_KEY or "")
        if not ai["api_key"]:
            return jsonify({"ok": False, "error": "لم يُضبَط المفتاح."}), 400

        ac = fin_set.load_amazon_credentials(db, SECRET_KEY or "")
        dash = fetch_financial_dashboard(
            db=db,
            base_url=ac["base_url"],
            api_key=ac["api_key"],
            api_secret=ac["secret"],
        )
        ctx = build_financial_context_payload(db, dash)
        txt, err = run_financial_llm(
            payload_text=ctx,
            user_question=q,
            provider=ai["provider"],
            api_key=ai["api_key"],
            model=ai["model"],
        )

        if err:
            return jsonify({"ok": False, "error": err}), 502
        return jsonify({"ok": True, "reply": txt})

    return bp
