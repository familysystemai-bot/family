# -*- coding: utf-8 -*-
"""
Cloudinary provider — الموصى به للمشروع.

المزايا:
    - 25 credits شهرياً مجاناً (~25,000 صورة محسّنة)
    - DAM متكامل (تنظيم، بحث، tags)
    - تحويلات تلقائية (resize, format, quality)
    - CDN عالمي
    - دعم AVIF و WebP تلقائي
    - background removal و AI بـ tier أعلى

التثبيت:
    pip install cloudinary

التسجيل:
    https://cloudinary.com/users/register/free
    احصل على Cloud Name + API Key + API Secret من dashboard
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from logic.cloud_storage import StorageResult

logger = logging.getLogger(__name__)


def _configure(config: Dict[str, str]) -> bool:
    """يضبط Cloudinary SDK بمفاتيح من الإعدادات."""
    try:
        import cloudinary  # type: ignore
    except ImportError:
        return False
    cloudinary.config(
        cloud_name=config.get("cloud_name", ""),
        api_key=config.get("api_key", ""),
        api_secret=config.get("api_secret", ""),
        secure=True,
    )
    return True


def upload_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    config: Dict[str, str],
) -> StorageResult:
    """يرفع الملف لـ Cloudinary."""
    try:
        import cloudinary.uploader  # type: ignore
    except ImportError:
        return StorageResult(
            success=False,
            error="cloudinary library not installed (pip install cloudinary)",
            provider="cloudinary",
        )

    if not _configure(config):
        return StorageResult(
            success=False,
            error="cloudinary config failed",
            provider="cloudinary",
        )

    folder = (config.get("folder") or "family-complex").strip("/")

    try:
        # نُمرّر الـ bytes مباشرة
        result = cloudinary.uploader.upload(
            file_bytes,
            folder=folder,
            resource_type="image",
            unique_filename=True,
            overwrite=False,
            # تحسين تلقائي عند العرض
            transformation=[
                {"quality": "auto", "fetch_format": "auto"}
            ],
        )

        return StorageResult(
            success=True,
            url=result.get("secure_url", "") or result.get("url", ""),
            public_id=result.get("public_id", ""),
            provider="cloudinary",
            width=result.get("width"),
            height=result.get("height"),
            bytes=result.get("bytes"),
            extra={
                "format": result.get("format"),
                "version": result.get("version"),
            },
        )
    except Exception as e:
        logger.exception("Cloudinary upload failed")
        return StorageResult(success=False, error=str(e), provider="cloudinary")


def delete_file(public_id: str, config: Dict[str, str]) -> bool:
    """يحذف الصورة من Cloudinary."""
    try:
        import cloudinary.uploader  # type: ignore
    except ImportError:
        return False

    if not _configure(config):
        return False

    try:
        result = cloudinary.uploader.destroy(public_id)
        return result.get("result") == "ok"
    except Exception:
        logger.exception("Cloudinary delete failed")
        return False


def test_connection(config: Dict[str, str]) -> Dict[str, Any]:
    """اختبار اتصال Cloudinary (ping)."""
    try:
        import cloudinary  # type: ignore
        import cloudinary.api  # type: ignore
    except ImportError:
        return {"success": False, "message": "cloudinary library not installed"}

    if not _configure(config):
        return {"success": False, "message": "config failed"}

    try:
        # API يفحص الـ usage = اختبار بسيط للمصادقة
        info = cloudinary.api.usage()
        plan = info.get("plan", "free")
        return {
            "success": True,
            "message": f"Connected to Cloudinary (plan: {plan})",
            "extra": {
                "credits_used": info.get("credits", {}).get("used_percent", 0),
                "storage_used_gb": round(info.get("storage", {}).get("usage", 0) / (1024**3), 2),
            },
        }
    except Exception as e:
        return {"success": False, "message": f"connection failed: {e}"}