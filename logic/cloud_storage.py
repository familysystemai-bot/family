# -*- coding: utf-8 -*-
"""
cloud_storage — طبقة تجريد لتخزين الصور السحابي.
=========================================================

الفلسفة:
    - الموظف يرفع صورة من لوحة التحكم
    - الصورة تُرفع للسحابة (المنصة المختارة من لوحة المؤسس)
    - رابط الصورة + بيانات المنتج يُحفظون في قاعدة البيانات
    - عند طلب العميل، النظام يُرجع الرابط + البيانات

المنصات المدعومة:
    - local       (الافتراضي — تخزين محلي للتجربة)
    - cloudinary  (الموصى به — مجاني حتى 25GB)
    - imagekit    (بديل أرخص — 20GB مجاني)

    StorageResult = upload(file_bytes, filename, mime_type) → success/url/error
    delete(public_id) → success
    get_active_provider() → "cloudinary" | "imagekit" | ...
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── أنواع البيانات ────────────────────────────────────────────────────

@dataclass
class StorageResult:
    """نتيجة موحّدة لكل عمليات التخزين."""
    success: bool = False
    url: str = ""              # الرابط العام للصورة
    public_id: str = ""        # معرّف داخلي (للحذف لاحقاً)
    provider: str = ""         # المنصة المستخدمة
    width: Optional[int] = None
    height: Optional[int] = None
    bytes: Optional[int] = None
    error: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success and bool((self.url or "").strip())


# ─── قراءة الإعدادات ──────────────────────────────────────────────────

def _read_setting(key: str, default: str = "") -> str:
    """يقرأ إعداداً من system_settings ثم من البيئة كـ fallback."""
    # 1) من قاعدة البيانات
    try:
        from logic.db_adapter import DBAdapter
        db = DBAdapter()
        row = db.fetch_one(
            "SELECT value FROM system_settings WHERE key = %s",
            (key,),
        )
        if row and (row.get("value") or "").strip():
            return str(row["value"]).strip()
    except Exception:
        pass

    # 2) من البيئة
    val = (os.environ.get(key.upper()) or "").strip()
    return val or default


def get_active_provider() -> str:
    """المنصة المختارة من لوحة المؤسس. الافتراضي: local."""
    val = _read_setting("storage_provider", "local").lower().strip()
    valid = ("local", "cloudinary", "imagekit", "s3", "r2")
    return val if val in valid else "local"


def get_provider_config(provider: Optional[str] = None) -> Dict[str, str]:
    """إعدادات المنصة المعطاة (مفاتيح API، secret، إلخ)."""
    p = (provider or get_active_provider()).lower()
    if p == "cloudinary":
        return {
            "cloud_name": _read_setting("storage_cloudinary_cloud_name"),
            "api_key": _read_setting("storage_cloudinary_api_key"),
            "api_secret": _read_setting("storage_cloudinary_api_secret"),
            "folder": _read_setting("storage_cloudinary_folder", "family-complex"),
        }
    if p == "imagekit":
        return {
            "private_key": _read_setting("storage_imagekit_private_key"),
            "public_key": _read_setting("storage_imagekit_public_key"),
            "url_endpoint": _read_setting("storage_imagekit_url_endpoint"),
            "folder": _read_setting("storage_imagekit_folder", "/family-complex"),
        }
    if p == "s3":
        return {
            "access_key": _read_setting("storage_s3_access_key"),
            "secret_key": _read_setting("storage_s3_secret_key"),
            "region": _read_setting("storage_s3_region", "us-east-1"),
            "bucket": _read_setting("storage_s3_bucket"),
            "endpoint": _read_setting("storage_s3_endpoint"),  # للـ S3 المتوافق (R2/MinIO)
            "public_base_url": _read_setting("storage_s3_public_base_url"),
            "folder": _read_setting("storage_s3_folder", "family-complex"),
        }
    if p == "r2":
        # Cloudflare R2 = S3-compatible
        return {
            "access_key": _read_setting("storage_r2_access_key"),
            "secret_key": _read_setting("storage_r2_secret_key"),
            "account_id": _read_setting("storage_r2_account_id"),
            "bucket": _read_setting("storage_r2_bucket"),
            "public_base_url": _read_setting("storage_r2_public_base_url"),
            "folder": _read_setting("storage_r2_folder", "family-complex"),
        }
    if p == "local":
        return {
            "upload_folder": _read_setting("storage_local_folder", "static/uploads"),
            "public_base_url": _read_setting("storage_local_base_url", "/static/uploads"),
        }
    return {}


def is_provider_configured(provider: Optional[str] = None) -> bool:
    """هل المنصة المعطاة مهيّأة بشكل كامل (المفاتيح موجودة)؟"""
    p = (provider or get_active_provider()).lower()
    cfg = get_provider_config(p)

    if p == "local":
        return True  # دائماً متاح
    if p == "cloudinary":
        return all(cfg.get(k) for k in ("cloud_name", "api_key", "api_secret"))
    if p == "imagekit":
        return all(cfg.get(k) for k in ("private_key", "public_key", "url_endpoint"))
    if p == "s3":
        return all(cfg.get(k) for k in ("access_key", "secret_key", "bucket"))
    if p == "r2":
        return all(cfg.get(k) for k in ("access_key", "secret_key", "account_id", "bucket"))
    return False


# ─── الواجهة الرئيسية ─────────────────────────────────────────────────

def upload(
    file_bytes: bytes,
    filename: str,
    mime_type: str = "image/jpeg",
    *,
    provider: Optional[str] = None,
    folder: Optional[str] = None,
) -> StorageResult:
    """
    رفع ملف للسحابة.

    المعاملات:
        file_bytes: محتوى الملف
        filename: اسم الملف الأصلي (للمعرّف فقط)
        mime_type: image/jpeg, image/png, image/webp
        provider: تجاوز المنصة المختارة (للاختبار)
        folder: تجاوز المجلد الافتراضي

    يُرجع: StorageResult.
        لو success=False → استخدم رابط فارغ أو اعرض رسالة خطأ.
    """
    p = (provider or get_active_provider()).lower()

    if not file_bytes:
        return StorageResult(success=False, error="empty file", provider=p)

    # حدّ أقصى للحجم: 10 MB لكل صورة
    if len(file_bytes) > 10 * 1024 * 1024:
        return StorageResult(
            success=False,
            error="file too large (max 10MB)",
            provider=p,
        )

    # تحقق من إعدادات المنصة
    if not is_provider_configured(p):
        # سقوط ذكي للـ local لو المنصة الأصلية غير مهيّأة
        if p != "local":
            logger.warning(
                "Storage provider %s not configured — falling back to local",
                p,
            )
            p = "local"

    try:
        if p == "cloudinary":
            from logic.storage_providers import cloudinary as backend
        elif p == "imagekit":
            from logic.storage_providers import imagekit as backend
        elif p == "s3":
            from logic.storage_providers import s3 as backend
        elif p == "r2":
            from logic.storage_providers import s3 as backend  # نفس الـ provider لكن إعدادات R2
        else:  # local
            from logic.storage_providers import local as backend
    except ImportError as e:
        return StorageResult(success=False, error=f"provider library missing: {e}", provider=p)

    cfg = get_provider_config(p)
    if folder:
        cfg["folder"] = folder

    try:
        return backend.upload_file(file_bytes, filename, mime_type, cfg)
    except Exception as e:
        logger.exception("Storage upload failed (provider=%s)", p)
        return StorageResult(success=False, error=str(e), provider=p)


def delete(public_id: str, *, provider: Optional[str] = None) -> bool:
    """
    حذف صورة من السحابة.

    المعاملات:
        public_id: المعرّف الذي أرجعه upload() في StorageResult.public_id
        provider: تجاوز المنصة (للحذف من منصة معيّنة)

    يُرجع: True عند النجاح.
    """
    if not public_id:
        return False
    p = (provider or get_active_provider()).lower()
    cfg = get_provider_config(p)

    try:
        if p == "cloudinary":
            from logic.storage_providers import cloudinary as backend
        elif p == "imagekit":
            from logic.storage_providers import imagekit as backend
        elif p == "s3" or p == "r2":
            from logic.storage_providers import s3 as backend
        else:
            from logic.storage_providers import local as backend
    except ImportError:
        return False

    try:
        return backend.delete_file(public_id, cfg)
    except Exception:
        logger.exception("Storage delete failed (provider=%s)", p)
        return False


def test_connection(provider: Optional[str] = None) -> Dict[str, Any]:
    """
    اختبار اتصال للمنصة المعطاة (يُستخدم في لوحة المؤسس).
    يُرجع dict فيه success + رسالة.
    """
    p = (provider or get_active_provider()).lower()
    if p == "local":
        cfg = get_provider_config(p)
        try:
            os.makedirs(cfg.get("upload_folder", "static/uploads"), exist_ok=True)
            return {"success": True, "message": "local storage ready"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    if not is_provider_configured(p):
        return {"success": False, "message": "missing API keys"}

    cfg = get_provider_config(p)
    try:
        if p == "cloudinary":
            from logic.storage_providers import cloudinary as backend
        elif p == "imagekit":
            from logic.storage_providers import imagekit as backend
        elif p in ("s3", "r2"):
            from logic.storage_providers import s3 as backend
        else:
            return {"success": False, "message": f"unknown provider: {p}"}
        return backend.test_connection(cfg)
    except ImportError as e:
        return {"success": False, "message": f"library not installed: {e}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def list_supported_providers() -> List[Dict[str, Any]]:
    """قائمة المنصات المدعومة وحالة كل واحدة (للوحة المؤسس)."""
    return [
        {
            "id": "local",
            "name": "Local Storage",
            "description": "تخزين محلي على الخادم (للتجربة فقط)",
            "configured": is_provider_configured("local"),
            "free_tier": "غير محدود (مساحة الخادم)",
            "recommended_for": "التطوير والاختبار",
        },
        {
            "id": "cloudinary",
            "name": "Cloudinary",
            "description": "الموصى به — DAM متكامل + تحويلات تلقائية",
            "configured": is_provider_configured("cloudinary"),
            "free_tier": "25 credits شهرياً (≈25GB)",
            "recommended_for": "معظم المشاريع",
            "signup_url": "https://cloudinary.com/users/register/free",
            "fields": [
                {"key": "storage_cloudinary_cloud_name", "label": "Cloud Name", "type": "text"},
                {"key": "storage_cloudinary_api_key", "label": "API Key", "type": "text"},
                {"key": "storage_cloudinary_api_secret", "label": "API Secret", "type": "password"},
                {"key": "storage_cloudinary_folder", "label": "Folder (اختياري)", "type": "text", "default": "family-complex"},
            ],
        },
        {
            "id": "imagekit",
            "name": "ImageKit",
            "description": "بديل أرخص — 20GB مجاني + تحويلات",
            "configured": is_provider_configured("imagekit"),
            "free_tier": "20GB bandwidth + 3GB storage",
            "recommended_for": "المشاريع الحساسة للتكلفة",
            "signup_url": "https://imagekit.io/registration",
            "fields": [
                {"key": "storage_imagekit_private_key", "label": "Private Key", "type": "password"},
                {"key": "storage_imagekit_public_key", "label": "Public Key", "type": "text"},
                {"key": "storage_imagekit_url_endpoint", "label": "URL Endpoint", "type": "text"},
                {"key": "storage_imagekit_folder", "label": "Folder (اختياري)", "type": "text", "default": "/family-complex"},
            ],
        },
        {
            "id": "r2",
            "name": "Cloudflare R2",
            "description": "أرخص خيار للحجوم الكبيرة — صفر egress fees",
            "configured": is_provider_configured("r2"),
            "free_tier": "10GB storage مجاني",
            "recommended_for": "المشاريع عالية النمو",
            "signup_url": "https://dash.cloudflare.com/sign-up",
            "fields": [
                {"key": "storage_r2_account_id", "label": "Account ID", "type": "text"},
                {"key": "storage_r2_access_key", "label": "Access Key ID", "type": "text"},
                {"key": "storage_r2_secret_key", "label": "Secret Access Key", "type": "password"},
                {"key": "storage_r2_bucket", "label": "Bucket Name", "type": "text"},
                {"key": "storage_r2_public_base_url", "label": "Public Base URL", "type": "text"},
            ],
        },
        {
            "id": "s3",
            "name": "AWS S3",
            "description": "للتحكم الكامل والمشاريع المؤسسية",
            "configured": is_provider_configured("s3"),
            "free_tier": "5GB سنة أولى",
            "recommended_for": "بيئات AWS فقط",
            "signup_url": "https://aws.amazon.com/s3/",
            "fields": [
                {"key": "storage_s3_access_key", "label": "Access Key", "type": "text"},
                {"key": "storage_s3_secret_key", "label": "Secret Key", "type": "password"},
                {"key": "storage_s3_region", "label": "Region", "type": "text", "default": "us-east-1"},
                {"key": "storage_s3_bucket", "label": "Bucket Name", "type": "text"},
                {"key": "storage_s3_public_base_url", "label": "Public URL Base (اختياري)", "type": "text"},
            ],
        },
    ]