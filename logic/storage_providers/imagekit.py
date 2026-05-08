# -*- coding: utf-8 -*-
"""
ImageKit provider — بديل أرخص لـ Cloudinary.

المزايا:
    - 20GB bandwidth + 3GB storage مجاناً
    - Lite plan: $9/شهر (عرض جيد)
    - تحويلات تلقائية URL-based
    - AVIF + WebP support
    - DAM متكامل

التثبيت:
    pip install imagekitio

التسجيل:
    https://imagekit.io/registration
    احصل على Public Key + Private Key + URL Endpoint من dashboard
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict

from logic.cloud_storage import StorageResult

logger = logging.getLogger(__name__)


def _make_client(config: Dict[str, str]):
    """ينشئ ImageKit client."""
    try:
        from imagekitio import ImageKit  # type: ignore
    except ImportError:
        return None
    try:
        return ImageKit(
            private_key=config.get("private_key", ""),
            public_key=config.get("public_key", ""),
            url_endpoint=config.get("url_endpoint", ""),
        )
    except Exception:
        logger.exception("ImageKit client init failed")
        return None


def upload_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    config: Dict[str, str],
) -> StorageResult:
    """يرفع الملف لـ ImageKit."""
    client = _make_client(config)
    if client is None:
        return StorageResult(
            success=False,
            error="imagekitio library not installed (pip install imagekitio)",
            provider="imagekit",
        )

    folder = config.get("folder") or "/family-complex"

    # ImageKit يقبل base64 أو bytes
    encoded = base64.b64encode(file_bytes).decode("utf-8")

    try:
        # SDK 3.x
        from imagekitio.models.UploadFileRequestOptions import UploadFileRequestOptions  # type: ignore
        options = UploadFileRequestOptions(
            folder=folder,
            use_unique_file_name=True,
        )
        result = client.upload_file(
            file=encoded,
            file_name=filename or "upload.jpg",
            options=options,
        )
        # SDK 3.x يُرجع response object
        if hasattr(result, "response_metadata"):
            data = getattr(result, "response_metadata", {}).get("raw", {}) or {}
        else:
            data = result if isinstance(result, dict) else {}

        url = data.get("url", "") or getattr(result, "url", "")
        file_id = data.get("fileId", "") or getattr(result, "file_id", "")

        if not url:
            return StorageResult(
                success=False,
                error="no url in ImageKit response",
                provider="imagekit",
            )

        return StorageResult(
            success=True,
            url=url,
            public_id=file_id,
            provider="imagekit",
            width=data.get("width") or getattr(result, "width", None),
            height=data.get("height") or getattr(result, "height", None),
            bytes=data.get("size") or getattr(result, "size", None),
            extra={
                "name": data.get("name") or getattr(result, "name", ""),
                "filePath": data.get("filePath") or getattr(result, "file_path", ""),
            },
        )
    except Exception as e:
        logger.exception("ImageKit upload failed")
        return StorageResult(success=False, error=str(e), provider="imagekit")


def delete_file(public_id: str, config: Dict[str, str]) -> bool:
    """يحذف الصورة من ImageKit (public_id = file_id)."""
    client = _make_client(config)
    if client is None:
        return False

    try:
        result = client.delete_file(file_id=public_id)
        # SDK 3.x: status 204 = success
        if hasattr(result, "response_metadata"):
            status = getattr(result.response_metadata, "http_status_code", 0)
            return status in (200, 204)
        return True
    except Exception:
        logger.exception("ImageKit delete failed")
        return False


def test_connection(config: Dict[str, str]) -> Dict[str, Any]:
    """اختبار اتصال ImageKit."""
    client = _make_client(config)
    if client is None:
        return {"success": False, "message": "imagekitio library not installed"}

    try:
        # حاول جلب قائمة ملفات (limit=1) كاختبار مصادقة
        from imagekitio.models.ListAndSearchFileRequestOptions import (  # type: ignore
            ListAndSearchFileRequestOptions,
        )
        options = ListAndSearchFileRequestOptions(limit=1)
        client.list_files(options=options)
        return {"success": True, "message": "Connected to ImageKit"}
    except Exception as e:
        return {"success": False, "message": f"connection failed: {e}"}