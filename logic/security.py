# -*- coding: utf-8 -*-
"""
security — وحدة الأمان الموحّدة للتطبيق.
================================================

تعالج الثغرات الأمنية الـ 5 التي رصدها التقرير:

1. CSRF Protection:
    - إعداد Flask-WTF CSRFProtect
    - csrf_exempt() للـ webhook (يستخدم HMAC بدلاً)
    - csrf_exempt() لـ /chat_query (AJAX endpoint)

2. WhatsApp Webhook Signature Verification:
    - verify_meta_signature(payload, signature_header) → bool
    - يستخدم HMAC-SHA256 مع APP_SECRET من Meta

3. File Upload Security:
    - validate_image_upload(file) → (is_valid, error, bytes, mime)
    - يفحص: الامتداد + magic bytes + الحجم + sanitize filename

4. SQL Dynamic Whitelist:
    - SAFE_COLUMN_NAMES — قائمة بيضاء للأعمدة المسموح استخدامها ديناميكياً
    - validate_column_name(name) → bool

5. Password Strength:
    - check_password_strength(password) → (score, feedback)

الاستخدام:
    from logic.security import init_security, validate_image_upload
    init_security(app)  # مرة واحدة عند بدء التطبيق

    # في route الرفع:
    is_valid, error, data, mime = validate_image_upload(request.files['image'])
    if not is_valid:
        return jsonify({"error": error}), 400
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 1) CSRF Protection
# ═══════════════════════════════════════════════════════════════════

# سيُحفظ المرجع للـ csrf instance بعد init
_csrf_instance: Any = None


def init_csrf(app) -> Any:
    """
    يهيّئ Flask-WTF CSRFProtect على التطبيق.

    يُرجع instance من CSRFProtect إذا نجح، أو None لو لم تكن المكتبة مثبتة.
    لا يُسقط التطبيق — في حال فشل التهيئة يُسجّل تحذير ويستمر العمل.
    """
    global _csrf_instance
    try:
        from flask_wtf.csrf import CSRFProtect
    except ImportError:
        logger.warning(
            "Flask-WTF غير مثبت — CSRF Protection معطّل. "
            "ثبّته بـ: pip install Flask-WTF"
        )
        return None

    try:
        csrf = CSRFProtect()
        csrf.init_app(app)
        _csrf_instance = csrf
        logger.info("CSRF Protection مُفعّل عبر Flask-WTF")
        return csrf
    except Exception as e:
        logger.exception("فشل تهيئة CSRF: %s", e)
        return None


def csrf_exempt(view_func):
    """
    Decorator لاستثناء view من CSRF (للـ webhooks وAPIs الخارجية).

    استخدم بحذر — فقط للـ endpoints التي:
        - تستخدم آلية مصادقة بديلة (HMAC, OAuth, API key)
        - يستدعيها نظام خارجي لا يستطيع إرسال CSRF token
    """
    if _csrf_instance is None:
        return view_func
    try:
        return _csrf_instance.exempt(view_func)
    except Exception:
        return view_func


# ═══════════════════════════════════════════════════════════════════
# 2) WhatsApp Webhook Signature Verification
# ═══════════════════════════════════════════════════════════════════

def verify_meta_signature(
    payload_bytes: bytes,
    signature_header: str,
    app_secret: str,
) -> bool:
    """
    يتحقق من صحة توقيع Meta على payload الواتساب.

    Meta يرسل header اسمه X-Hub-Signature-256 بصيغة:
        sha256=<hexdigest>

    نُعيد True فقط لو:
        - signature_header ليس فارغاً
        - app_secret مُهيّأ
        - HMAC-SHA256(payload, app_secret) == hexdigest المرسَل

    https://developers.facebook.com/docs/messenger-platform/webhooks#security
    """
    if not payload_bytes:
        return False
    if not signature_header:
        logger.warning("Meta webhook: missing signature header")
        return False

    sec = (app_secret or "").strip().replace("\r", "").replace("\n", "")
    if sec.startswith("\ufeff"):
        sec = sec[1:]

    if not sec:
        logger.warning("Meta webhook: APP_SECRET not configured — cannot verify")
        return False

    if not signature_header.startswith("sha256="):
        logger.warning("Meta webhook: signature header malformed")
        return False

    received_sig = signature_header[len("sha256=") :].strip().lower()

    expected_sig = hmac.new(
        sec.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    is_valid = hmac.compare_digest(received_sig, expected_sig)
    if not is_valid:
        logger.warning("Meta webhook: signature mismatch")
    return is_valid


# ═══════════════════════════════════════════════════════════════════
# 3) File Upload Security (Magic Bytes + Size + Sanitization)
# ═══════════════════════════════════════════════════════════════════

# الحد الأقصى لحجم الصورة: 10 MB
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024

# الحد الأقصى لحجم الصوت: 25 MB
MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024

# امتدادات صور مسموح بها فقط
_ALLOWED_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "webp"})

# امتدادات صوت مسموح بها (للمحادثة)
_ALLOWED_AUDIO_EXTENSIONS = frozenset({"wav", "mp3", "ogg", "m4a", "webm"})


def _detect_image_mime(file_bytes: bytes) -> Optional[str]:
    """
    يكتشف MIME type الفعلي من بايتات الملف (magic bytes).

    لا نعتمد على الامتداد أو على header الذي يرسله المتصفح
    (يمكن تزويرهما).
    """
    if not file_bytes or len(file_bytes) < 12:
        return None

    head = file_bytes[:12]

    # JPEG
    if head[:3] == bytes([0xFF, 0xD8, 0xFF]):
        return "image/jpeg"

    # PNG
    if head[:8] == bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]):
        return "image/png"

    # GIF
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"

    # WebP: RIFF....WEBP
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"

    return None


def sanitize_filename(filename: str, max_length: int = 100) -> str:
    """ينظّف اسم الملف من أي محارف خطيرة."""
    if not filename:
        return ""
    filename = os.path.basename(filename)
    filename = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    filename = re.sub(r"\.{2,}", ".", filename)
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        ext = ext[: 10] if ext else ""
        filename = name[: max_length - len(ext)] + ext
    return filename


def validate_image_upload(
    file_storage: Any,
    *,
    max_size_bytes: int = MAX_IMAGE_SIZE_BYTES,
) -> Tuple[bool, str, Optional[bytes], Optional[str]]:
    """
    يتحقق من صحة وأمان ملف صورة مرفوع.

    يفحص:
        1. الملف موجود وله اسم
        2. الامتداد ضمن المسموح
        3. الحجم ≤ MAX_IMAGE_SIZE_BYTES
        4. Magic bytes تطابق الامتداد (يمنع تزوير extension)
        5. اسم الملف نظيف من path traversal

    يُرجع:
        (is_valid: bool, error_msg: str, file_bytes: bytes|None, detected_mime: str|None)
    """
    if file_storage is None or not getattr(file_storage, "filename", ""):
        return False, "لم يتم رفع أي ملف", None, None

    filename = file_storage.filename
    safe_name = sanitize_filename(filename)
    if not safe_name or "." not in safe_name:
        return False, "اسم الملف غير صالح", None, None

    ext = safe_name.rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_IMAGE_EXTENSIONS:
        return (
            False,
            f"الامتداد '{ext}' غير مسموح. المسموح: {', '.join(sorted(_ALLOWED_IMAGE_EXTENSIONS))}",
            None,
            None,
        )

    try:
        file_storage.seek(0)
        data = file_storage.read()
    except Exception as e:
        return False, f"فشل قراءة الملف: {e}", None, None

    if not data:
        return False, "الملف فارغ", None, None

    if len(data) > max_size_bytes:
        size_mb = len(data) / (1024 * 1024)
        max_mb = max_size_bytes / (1024 * 1024)
        return False, f"حجم الملف {size_mb:.1f}MB يتجاوز الحد المسموح {max_mb:.0f}MB", None, None

    detected_mime = _detect_image_mime(data)
    if detected_mime is None:
        return False, "محتوى الملف ليس صورة صالحة", None, None

    expected_ext_map = {
        "image/jpeg": ("jpg", "jpeg"),
        "image/png": ("png",),
        "image/gif": ("gif",),
        "image/webp": ("webp",),
    }
    valid_exts = expected_ext_map.get(detected_mime, ())
    if ext not in valid_exts:
        return (
            False,
            f"محتوى الملف ({detected_mime}) لا يطابق الامتداد ({ext}). الملف قد يكون مزوّراً.",
            None,
            None,
        )

    try:
        file_storage.seek(0)
    except Exception:
        pass

    return True, "", data, detected_mime


def validate_audio_upload(
    file_storage: Any,
    *,
    max_size_bytes: int = MAX_AUDIO_SIZE_BYTES,
) -> Tuple[bool, str, Optional[bytes]]:
    """يتحقق من ملف صوتي (للمحادثات الصوتية)."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        return False, "لم يتم رفع أي ملف", None

    safe_name = sanitize_filename(file_storage.filename)
    if "." not in safe_name:
        return False, "اسم الملف غير صالح", None

    ext = safe_name.rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_AUDIO_EXTENSIONS:
        return False, f"الامتداد '{ext}' غير مسموح", None

    try:
        file_storage.seek(0)
        data = file_storage.read()
    except Exception as e:
        return False, str(e), None

    if not data:
        return False, "الملف فارغ", None
    if len(data) > max_size_bytes:
        return False, f"حجم الملف يتجاوز {max_size_bytes // (1024*1024)}MB", None

    try:
        file_storage.seek(0)
    except Exception:
        pass

    return True, "", data


# ═══════════════════════════════════════════════════════════════════
# 4) SQL Dynamic Whitelist
# ═══════════════════════════════════════════════════════════════════

# قائمة بيضاء للأعمدة المسموح استخدامها في استعلامات ديناميكية.
SAFE_COLUMN_NAMES = frozenset({
    # clients
    "name", "dialect", "last_intent", "chat_history", "phone",
    "complaint_draft", "gender_hint",
    # customers
    "email", "branch_id", "prefers_marketing",
    "last_product_interest", "last_product_interest_at",
    "last_campaign_sent_at", "declined_marketing_prompt",
    "is_active", "merged_into_id",
    # branches
    "city_name", "username", "password", "complaint_email",
    # complaints
    "status", "complaint_type", "message", "branch_name",
    "customer_name", "customer_phone", "customer_email",
    "resolved_at", "ticket_code", "resolution_notes",
    # products
    "product_name", "description", "price", "sub_id", "section_id", "sku",
    # categories/sections
    "main_id", "category_id",
    # generic
    "id", "created_at", "updated_at", "deleted_at",
})


def validate_column_name(name: str) -> bool:
    """يتحقق من أن اسم العمود ضمن القائمة البيضاء."""
    if not name or not isinstance(name, str):
        return False
    name = name.strip().lower()
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", name):
        return False
    return name in SAFE_COLUMN_NAMES


# ═══════════════════════════════════════════════════════════════════
# 5) Password Strength
# ═══════════════════════════════════════════════════════════════════

def check_password_strength(password: str) -> Tuple[int, List[str]]:
    """
    يقيس قوة كلمة المرور.

    يُرجع:
        (score: int 0-5, feedback: List[str])
    """
    if not password:
        return 0, ["كلمة المرور فارغة"]

    score = 0
    feedback = []

    if len(password) < 8:
        feedback.append("يجب أن تكون 8 أحرف على الأقل")
    else:
        score += 1
        if len(password) >= 12:
            score += 1

    if re.search(r"[a-z]", password):
        score += 1
    else:
        feedback.append("أضف حروف صغيرة")

    if re.search(r"[A-Z]", password):
        score += 1
    else:
        feedback.append("أضف حروف كبيرة")

    if re.search(r"\d", password):
        score += 1
    else:
        feedback.append("أضف أرقاماً")

    if re.search(r"[^a-zA-Z0-9]", password):
        score += 1
    else:
        feedback.append("أضف رموزاً (مثل: ! @ # $)")

    common = {"password", "123456", "qwerty", "admin", "letmein", "welcome"}
    if password.lower() in common:
        score = min(score, 1)
        feedback.append("كلمة المرور شائعة جداً")

    score = min(5, score)

    if not feedback:
        feedback.append("كلمة مرور قوية ✓")

    return score, feedback


# ═══════════════════════════════════════════════════════════════════
# Init Helper
# ═══════════════════════════════════════════════════════════════════

def init_security(app) -> None:
    """
    تهيئة شاملة لكل عناصر الأمان.

    يُستدعى مرة واحدة في app.py بعد إنشاء app:
        from logic.security import init_security
        init_security(app)
    """
    # 1) CSRF
    init_csrf(app)

    # 2) Max content length (يحمي من رفع ملفات ضخمة)
    if not app.config.get("MAX_CONTENT_LENGTH"):
        app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

    # 3) Cookie security flags
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("REMEMBER_COOKIE_HTTPONLY", True)
    app.config.setdefault("REMEMBER_COOKIE_SAMESITE", "Lax")
    if os.environ.get("FLASK_ENV") == "production":
        app.config.setdefault("SESSION_COOKIE_SECURE", True)
        app.config.setdefault("REMEMBER_COOKIE_SECURE", True)

    # 4) Browser security headers (محافظة ومتوافقة مع الواجهات الحالية)
    csp_policy = "; ".join(
        [
            "default-src 'self' https: data: blob:",
            "img-src 'self' https: data: blob:",
            "font-src 'self' https: data:",
            "media-src 'self' https: data: blob:",
            # نُبقي unsafe-inline/unsafe-eval لتجنب كسر الواجهات الحالية المبنية على سكربتات inline
            "script-src 'self' https: 'unsafe-inline' 'unsafe-eval'",
            "style-src 'self' https: 'unsafe-inline'",
            "connect-src 'self' https: wss:",
            "frame-ancestors 'self'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
    )

    @app.after_request
    def _apply_browser_security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        resp.headers.setdefault("Content-Security-Policy", csp_policy)

        # HSTS فقط مع HTTPS في الإنتاج حتى لا يكسر بيئات التطوير
        if os.environ.get("FLASK_ENV") == "production":
            try:
                from flask import request as _req

                if _req.is_secure or os.environ.get("FORCE_HTTPS", "").strip() == "1":
                    resp.headers.setdefault(
                        "Strict-Transport-Security",
                        "max-age=31536000; includeSubDomains",
                    )
            except Exception:
                pass
        return resp

    logger.info("Security module initialized")
