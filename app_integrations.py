# -*- coding: utf-8 -*-
"""
app_integrations.py — Blueprint للوحة التكاملات في لوحة المؤسس.
================================================================

يضيف 5 صفحات للوحة المؤسس:
    /founder/integrations                    — الصفحة الرئيسية (overview)
    /founder/integrations/storage            — تخزين الصور
    /founder/integrations/llm                — مزود الذكاء الاصطناعي
    /founder/integrations/payment            — بوابات الدفع
    /founder/integrations/shipping           — شركات الشحن
    /founder/integrations/invoicing          — البريد والفواتير

كل صفحة:
    - GET: عرض المنصات المتاحة + المنصة الحالية + المفاتيح المخزّنة
    - POST: حفظ مفاتيح + اختبار اتصال + تفعيل/تعطيل

الأمان:
    - كل الـ routes محمية بـ _session_founder_only
    - كل POST من النماذج يتضمّن CSRF token (عبر templates/macros/csrf.html)
    - المفاتيح الحساسة لا تُعرض كاملة (نُظهر آخر 4 أحرف فقط)

ملاحظة: هذا Blueprint منفصل عن app.py لتسهيل الصيانة وتقليل خطر كسر الموجود.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import (
    Blueprint, jsonify, redirect, render_template, request, session, url_for, flash
)

from logic import cloud_storage, llm_provider
from logic.integrations import payment, shipping
from logic.integrations.base import read_setting, write_setting
from logic.integrations.whatsapp_meta import send_test_text_message

logger = logging.getLogger(__name__)

bp = Blueprint("integrations", __name__, url_prefix="/founder/integrations")


# ─── Helpers ────────────────────────────────────────────────────────

def _is_founder() -> bool:
    """تحقق من أن المستخدم مؤسس فقط."""
    return session.get("role") == "founder"


def _mask_secret(value: str) -> str:
    """يُخفي المفاتيح الحساسة عند العرض (يُظهر آخر 4 أحرف فقط)."""
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def _looks_like_masked_placeholder(value: str) -> bool:
    """
    يمنع استبدال السر الحقيقي بقيمة منسوخة من واجهة التمويه (•…) أو لصق بالخطأ.
    متصفحات أو مديرو كلمات مرور قد يملأون الحقلون بقيم غير صحيحة أيضاً.
    """
    v = (value or "").strip()
    if not v:
        return False
    if "\u2022" in v or "•" in v or "·" in v:
        return True
    return False


def _get_settings_with_masked_secrets(fields: list) -> Dict[str, str]:
    """يقرأ القيم من DB، ويُخفي الحقول من نوع password."""
    out = {}
    for f in fields:
        key = f.get("key", "")
        value = read_setting(key, f.get("default", ""))
        if f.get("type") == "password" and value:
            out[key] = _mask_secret(value)
            out[key + "_has_value"] = True
        else:
            out[key] = value
            out[key + "_has_value"] = bool(value)
    return out


def _save_settings(form_data: Dict[str, Any], fields: list) -> int:
    """
    يحفظ الإعدادات. القيم الفارغة في حقول password تبقى دون تغيير
    (لأن المؤسس يرى bullets، لو ترك الحقل فارغاً يعني "ما تغير").
    """
    saved = 0
    for f in fields:
        key = f.get("key", "")
        if not key:
            continue
        new_value = (form_data.get(key) or "").strip()

        # حقول checkbox: نحوّل إلى true/false
        if f.get("type") == "checkbox":
            new_value = "true" if form_data.get(key) in ("on", "true", "1") else "false"
            if write_setting(key, new_value):
                saved += 1
            else:
                logger.error("فشل حفظ الإعداد في قاعدة البيانات: %s", key)
            continue

        # حقول password فارغة = أبقِ القديم
        if f.get("type") == "password" and not new_value:
            continue
        # لا تُحفظ نسخة من النص المموّه (placeholder) أو لصق بالخطأ
        if f.get("type") == "password" and _looks_like_masked_placeholder(new_value):
            logger.warning(
                "تجاهل حفظ %s: القيمة تبدو نسخة مموّهة وليست سراً حقيقياً", key
            )
            continue

        if not write_setting(key, new_value):
            logger.error("فشل حفظ الإعداد في قاعدة البيانات: %s", key)
            continue
        saved += 1
    return saved


# ─── الصفحة الرئيسية (Overview) ──────────────────────────────────────

@bp.route("/")
@bp.route("/dashboard")
def integrations_dashboard():
    """نظرة عامة على كل التكاملات."""
    if not _is_founder():
        return redirect(url_for("login"))

    # نجمع حالة كل التكاملات
    storage_active = cloud_storage.get_active_provider()
    llm_active = llm_provider.get_active_provider()
    payment_active = payment.get_active_provider()
    shipping_active = shipping.get_active_provider()

    overview = {
        "storage": {
            "title": "تخزين الصور",
            "icon": "🖼️",
            "active": storage_active,
            "configured": cloud_storage.is_provider_configured(),
            "url": url_for("integrations.storage_settings"),
            "description": "Cloudinary, ImageKit, R2, S3",
        },
        "llm": {
            "title": "الذكاء الاصطناعي",
            "icon": "🧠",
            "active": llm_active,
            "configured": llm_provider.is_available(),
            "url": url_for("integrations.llm_settings"),
            "description": "OpenAI, Anthropic, Gemini",
        },
        "payment": {
            "title": "بوابة الدفع",
            "icon": "💳",
            "active": payment_active or "غير مفعّل",
            "configured": payment.is_provider_configured(),
            "activated": payment.is_provider_activated(),
            "url": url_for("integrations.payment_settings"),
            "description": "Moyasar, Tap, HyperPay, PayTabs",
        },
        "shipping": {
            "title": "شركة الشحن",
            "icon": "📦",
            "active": shipping_active or "غير مفعّل",
            "configured": shipping.is_provider_configured(),
            "url": url_for("integrations.shipping_settings"),
            "description": "SMSA, Aramex, DHL, Naqel",
        },
        "invoicing": {
            "title": "الفواتير والبريد",
            "icon": "📧",
            "active": read_setting("invoicing_email_provider", "غير مفعّل"),
            "configured": bool(read_setting("invoicing_smtp_host", "")),
            "url": url_for("integrations.invoicing_settings"),
            "description": "SMTP, Amazon SES",
        },
        "whatsapp": {
            "title": "واتساب الأعمال (Meta)",
            "icon": "💬",
            "active": "Meta API" if read_setting("META_APP_SECRET", "") else "غير مفعّل",
            "configured": bool(read_setting("META_APP_SECRET", "")),
            "url": url_for("integrations.whatsapp_settings"),
            "description": "WhatsApp Business API + HMAC verification",
        },
    }

    return render_template(
        "founder/integrations/dashboard.html",
        overview=overview,
    )


# ─── تخزين الصور ────────────────────────────────────────────────────

@bp.route("/storage", methods=["GET", "POST"])
def storage_settings():
    if not _is_founder():
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action", "")
        provider = request.form.get("provider", "").lower().strip()

        if action == "activate":
            # تفعيل المنصة المختارة
            valid = ("local", "cloudinary", "imagekit", "s3", "r2")
            if provider in valid:
                write_setting("storage_provider", provider)
                flash(f"تم تفعيل {provider} كمنصة تخزين", "success")
            else:
                flash("منصة غير صالحة", "error")

        elif action == "save":
            providers = cloud_storage.list_supported_providers()
            target = next((p for p in providers if p["id"] == provider), None)
            if target and "fields" in target:
                saved = _save_settings(request.form, target["fields"])
                flash(f"تم حفظ {saved} حقل", "success")

        elif action == "test":
            result = cloud_storage.test_connection(provider)
            if result.get("success"):
                flash(f"✅ نجح الاتصال: {result.get('message', '')}", "success")
            else:
                flash(f"❌ فشل الاتصال: {result.get('message', '')}", "error")

        return redirect(url_for("integrations.storage_settings"))

    # GET
    providers = cloud_storage.list_supported_providers()
    # نُحقن الإعدادات المحفوظة في كل مزود
    for p in providers:
        p["values"] = _get_settings_with_masked_secrets(p.get("fields", []))

    return render_template(
        "founder/integrations/storage.html",
        providers=providers,
        active_provider=cloud_storage.get_active_provider(),
    )


# ─── الذكاء الاصطناعي ───────────────────────────────────────────────

LLM_FIELDS = {
    "openai": [
        {"key": "OPENAI_API_KEY", "label": "API Key", "type": "password",
         "help": "من platform.openai.com → API keys"},
    ],
    "anthropic": [
        {"key": "ANTHROPIC_API_KEY", "label": "API Key", "type": "password",
         "help": "من console.anthropic.com → API keys"},
    ],
    "gemini": [
        {"key": "GEMINI_API_KEY", "label": "API Key", "type": "password",
         "help": "من aistudio.google.com → Get API key"},
    ],
    "mistral": [
        {"key": "MISTRAL_API_KEY", "label": "API Key", "type": "password",
         "help": "من console.mistral.ai → API keys"},
    ],
    "groq": [
        {"key": "GROQ_API_KEY", "label": "API Key", "type": "password",
         "help": "من console.groq.com → API keys (سريع جداً مجاناً)"},
    ],
    "openrouter": [
        {"key": "OPENROUTER_API_KEY", "label": "API Key", "type": "password",
         "help": "من openrouter.ai/keys — يفتح آلاف النماذج بمفتاح واحد"},
    ],
    "cohere": [
        {"key": "COHERE_API_KEY", "label": "API Key", "type": "password",
         "help": "من dashboard.cohere.com → API keys"},
    ],
    "manus": [
        {"key": "MANUS_API_KEY", "label": "API Key", "type": "password",
         "help": "مفتاح Manus AI (واجهة متوافقة OpenAI)"},
        {"key": "MANUS_BASE_URL", "label": "Base URL (اختياري)", "type": "text",
         "help": "اتركه فارغًا لاستخدام النقطة الافتراضية https://api.manus.im/v1"},
    ],
}


@bp.route("/llm", methods=["GET", "POST"])
def llm_settings():
    if not _is_founder():
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action", "")
        provider = request.form.get("provider", "").lower().strip()

        if action == "activate":
            model = (request.form.get("model") or "").strip()
            if provider in LLM_FIELDS:
                write_setting("ai_provider", provider)
                if model:
                    write_setting("ai_model", model)
                flash(f"تم تفعيل {provider} كمزود ذكاء اصطناعي", "success")

        elif action == "save":
            fields = LLM_FIELDS.get(provider, [])
            saved = _save_settings(request.form, fields)
            flash(f"تم حفظ {saved} حقل", "success")

        elif action == "test":
            result = {"success": llm_provider.is_available(provider)}
            if result["success"]:
                flash(f"✅ مزود {provider} متاح ومُهيّأ", "success")
            else:
                flash(f"❌ مزود {provider} غير مهيّأ (تحقق من المفاتيح)", "error")

        return redirect(url_for("integrations.llm_settings"))

    # GET
    providers = llm_provider.list_supported_providers()
    for p in providers:
        p["fields"] = LLM_FIELDS.get(p["id"], [])
        p["values"] = _get_settings_with_masked_secrets(p["fields"])

    return render_template(
        "founder/integrations/llm.html",
        providers=providers,
        active_provider=llm_provider.get_active_provider(),
        active_model=llm_provider.get_active_model(),
    )


# ─── بوابات الدفع ───────────────────────────────────────────────────

@bp.route("/payment", methods=["GET", "POST"])
def payment_settings():
    if not _is_founder():
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action", "")
        provider = request.form.get("provider", "").lower().strip()

        if action == "activate":
            valid = ("moyasar", "tap", "hyperpay", "paytabs")
            if provider in valid:
                write_setting("payment_provider", provider)
                flash(f"تم اختيار {provider} كبوابة دفع", "success")

        elif action == "save":
            providers = payment.list_supported_providers()
            target = next((p for p in providers if p["id"] == provider), None)
            if target and "fields" in target:
                saved = _save_settings(request.form, target["fields"])
                flash(f"تم حفظ {saved} حقل", "success")

        elif action == "test":
            result = payment.test_connection(provider)
            if result.get("success"):
                flash(f"✅ نجح الاتصال: {result.get('message', '')}", "success")
            else:
                flash(f"❌ فشل الاتصال: {result.get('message', '')}", "error")

        elif action == "save_business_info":
            # حفظ معلومات النشاط التجاري والحساب البنكي
            info = {
                k: (request.form.get(k) or "").strip()
                for k in (
                    "business_name", "commercial_register", "vat_number",
                    "iban", "bank_name", "account_holder_name",
                    "contact_email", "contact_phone",
                )
            }
            payment.save_business_info(info)
            flash("تم حفظ معلومات النشاط التجاري", "success")

        elif action == "activate_payments":
            # تفعيل البوابة بعد إكمال KYC
            if payment.is_provider_configured(provider):
                write_setting(f"payment_{provider}_activated", "true")
                flash(
                    f"تم تفعيل {provider} لاستقبال مدفوعات حقيقية. "
                    "تأكد من إكمال KYC في dashboard البوابة.",
                    "success"
                )
            else:
                flash("أدخل المفاتيح أولاً قبل التفعيل", "error")

        elif action == "deactivate":
            write_setting(f"payment_{provider}_activated", "false")
            flash(f"تم تعطيل {provider}", "warning")

        elif action == "toggle_sandbox":
            current = read_setting("payment_sandbox", "true")
            new_val = "false" if current == "true" else "true"
            write_setting("payment_sandbox", new_val)
            mode = "بيئة الاختبار" if new_val == "true" else "بيئة الإنتاج"
            flash(f"تم التبديل إلى {mode}", "info")

        return redirect(url_for("integrations.payment_settings"))

    # GET
    providers = payment.list_supported_providers()
    for p in providers:
        p["values"] = _get_settings_with_masked_secrets(p.get("fields", []))

    business_info = payment.get_business_info()

    return render_template(
        "founder/integrations/payment.html",
        providers=providers,
        active_provider=payment.get_active_provider(),
        is_sandbox=payment.is_sandbox_mode(),
        business_info=business_info,
    )


# ─── شركات الشحن ────────────────────────────────────────────────────

@bp.route("/shipping", methods=["GET", "POST"])
def shipping_settings():
    if not _is_founder():
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action", "")
        provider = request.form.get("provider", "").lower().strip()

        if action == "activate":
            valid = ("smsa", "aramex", "dhl", "naqel")
            if provider in valid:
                write_setting("shipping_provider", provider)
                flash(f"تم اختيار {provider} كشركة شحن", "success")

        elif action == "save":
            providers = shipping.list_supported_providers()
            target = next((p for p in providers if p["id"] == provider), None)
            if target and "fields" in target:
                saved = _save_settings(request.form, target["fields"])
                flash(f"تم حفظ {saved} حقل", "success")

        elif action == "test":
            result = shipping.test_connection(provider)
            if result.get("success"):
                flash(f"✅ {result.get('message', '')}", "success")
            else:
                flash(f"❌ {result.get('message', '')}", "error")

        return redirect(url_for("integrations.shipping_settings"))

    # GET
    providers = shipping.list_supported_providers()
    for p in providers:
        p["values"] = _get_settings_with_masked_secrets(p.get("fields", []))

    return render_template(
        "founder/integrations/shipping.html",
        providers=providers,
        active_provider=shipping.get_active_provider(),
    )


# ─── الفواتير والبريد ───────────────────────────────────────────────

INVOICING_FIELDS = {
    "smtp": [
        {"key": "invoicing_smtp_host", "label": "SMTP Server", "type": "text",
         "help": "smtp.gmail.com / smtp.office365.com / smtp.sendgrid.net"},
        {"key": "invoicing_smtp_port", "label": "Port", "type": "text", "default": "587"},
        {"key": "invoicing_smtp_username", "label": "Username", "type": "text"},
        {"key": "invoicing_smtp_password", "label": "Password", "type": "password",
         "help": "App password للحسابات اللي تستخدم 2FA"},
        {"key": "invoicing_smtp_from_email", "label": "From Email", "type": "text"},
        {"key": "invoicing_smtp_from_name", "label": "From Name", "type": "text",
         "default": "Family Complex"},
    ],
    "ses": [
        {"key": "invoicing_ses_access_key", "label": "AWS Access Key", "type": "password"},
        {"key": "invoicing_ses_secret_key", "label": "AWS Secret Key", "type": "password"},
        {"key": "invoicing_ses_region", "label": "Region", "type": "text", "default": "us-east-1"},
        {"key": "invoicing_ses_from_email", "label": "From Email (verified)", "type": "text"},
    ],
}


@bp.route("/invoicing", methods=["GET", "POST"])
def invoicing_settings():
    if not _is_founder():
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action", "")
        provider = request.form.get("provider", "smtp").lower().strip()

        if action == "activate":
            if provider in INVOICING_FIELDS:
                write_setting("invoicing_email_provider", provider)
                flash(f"تم تفعيل {provider} كمزود البريد", "success")

        elif action == "save":
            fields = INVOICING_FIELDS.get(provider, [])
            saved = _save_settings(request.form, fields)
            flash(f"تم حفظ {saved} حقل", "success")

        elif action == "test":
            from logic.integrations import invoicing
            result = invoicing.test_email_connection(provider)
            if result.get("success"):
                flash(f"✅ {result.get('message', '')}", "success")
            else:
                flash(f"❌ {result.get('message', '')}", "error")

        return redirect(url_for("integrations.invoicing_settings"))

    # GET
    providers = []
    for pid, fields in INVOICING_FIELDS.items():
        providers.append({
            "id": pid,
            "name": "SMTP (Gmail/Outlook/...)" if pid == "smtp" else "Amazon SES",
            "description": (
                "بسيط وسريع — مناسب للحجوم الصغيرة والمتوسطة" if pid == "smtp"
                else "للحجوم الكبيرة، تكلفة أقل (0.10$ / 1000 إيميل)"
            ),
            "fields": fields,
            "values": _get_settings_with_masked_secrets(fields),
        })

    return render_template(
        "founder/integrations/invoicing.html",
        providers=providers,
        active_provider=read_setting("invoicing_email_provider", ""),
    )

# ─── الواتساب (Meta API + HMAC verification) ────────────────────────

WHATSAPP_FIELDS = [
    {
        "key": "META_APP_SECRET",
        "label": "Meta App Secret",
        "type": "password",
        "help": (
            "للتحقق من توقيع HMAC على webhook payloads. "
            "احصل عليه من: developers.facebook.com → تطبيقك → Settings → Basic → App Secret"
        ),
    },
    {
        "key": "WA_VERIFY_TOKEN",
        "label": "Verify Token (للـ webhook)",
        "type": "password",
        "help": "نص اختياري تختاره أنت — يُستخدم عند ربط webhook في Meta dashboard",
    },
    {
        "key": "WA_PHONE_NUMBER_ID",
        "label": "Phone Number ID",
        "type": "text",
        "help": "من Meta Business → WhatsApp → API Setup",
    },
    {
        "key": "WA_ACCESS_TOKEN",
        "label": "Access Token (System User)",
        "type": "password",
        "help": "Permanent System User token من Business Manager",
    },
]


@bp.route("/whatsapp", methods=["GET", "POST"])
def whatsapp_settings():
    """إعدادات واتساب الأعمال (Meta) + HMAC verification."""
    if not _is_founder():
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "save":
            saved = _save_settings(request.form, WHATSAPP_FIELDS)
            flash(f"تم حفظ {saved} حقل من إعدادات واتساب", "success")

        elif action == "test":
            secret = read_setting("META_APP_SECRET", "")
            phone_id = read_setting("WA_PHONE_NUMBER_ID", "")
            token = read_setting("WA_ACCESS_TOKEN", "")
            vtok = read_setting("WA_VERIFY_TOKEN", "")
            missing = []
            if not secret:
                missing.append("META_APP_SECRET")
            if not vtok:
                missing.append("WA_VERIFY_TOKEN")
            if not phone_id:
                missing.append("WA_PHONE_NUMBER_ID")
            if not token:
                missing.append("WA_ACCESS_TOKEN")
            if missing:
                flash(
                    f"❌ حقول ناقصة (لم يُحفَظ شيء أو فارغ): {', '.join(missing)}",
                    "error",
                )
            else:
                flash(
                    "✅ الحقول الأساسية مُعبّأة في النظام (غير فارغة). "
                    "هذا لا يثبت أن الـ App Secret يطابق تطبيقك في ميتا أو أن التوكن لا يزال صالحاً — "
                    "إن كان الرد على الواتساب توقف فجأة بعد «حفظ»، راجع سجلات السيرفر لرسالة "
                    "«HMAC فشل» أو جرّب «إرسال رسالة تجريبية».",
                    "success",
                )

        elif action == "test_send":
            phone = (request.form.get("test_phone") or "").strip()
            phone_id = read_setting("WA_PHONE_NUMBER_ID", "").strip()
            token = read_setting("WA_ACCESS_TOKEN", "")
            if not phone:
                flash("❌ أدخل رقم واتساب المستقبِل للاختبار.", "error")
            elif not phone_id or not token:
                flash("❌ احفظ Phone Number ID و Access Token ثم أعد المحاولة.", "error")
            else:
                ok, api_msg = send_test_text_message(phone_id, token, phone)
                flash(("✅ " if ok else "❌ ") + api_msg, "success" if ok else "error")

        return redirect(url_for("integrations.whatsapp_settings"))

    # GET
    values = _get_settings_with_masked_secrets(WHATSAPP_FIELDS)
    is_secure = bool(read_setting("META_APP_SECRET", ""))
    wa_status = {
        "META_APP_SECRET": bool((read_setting("META_APP_SECRET", "") or "").strip()),
        "WA_VERIFY_TOKEN": bool((read_setting("WA_VERIFY_TOKEN", "") or "").strip()),
        "WA_PHONE_NUMBER_ID": bool((read_setting("WA_PHONE_NUMBER_ID", "") or "").strip()),
        "WA_ACCESS_TOKEN": bool((read_setting("WA_ACCESS_TOKEN", "") or "").strip()),
    }
    wa_status["all_keys"] = all(wa_status.values())
    wa_status["can_send_test"] = wa_status["WA_PHONE_NUMBER_ID"] and wa_status["WA_ACCESS_TOKEN"]

    return render_template(
        "founder/integrations/whatsapp.html",
        fields=WHATSAPP_FIELDS,
        values=values,
        is_secure=is_secure,
        wa_status=wa_status,
    )
