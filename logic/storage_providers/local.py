# -*- coding: utf-8 -*-
"""
local provider — تخزين محلي على خادم التطبيق.

الاستخدام: للتطوير والاختبار فقط.
في الإنتاج، استخدم Cloudinary أو ImageKit أو R2.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Dict

from logic.cloud_storage import StorageResult

logger = logging.getLogger(__name__)


def _safe_filename(filename: str, mime_type: str) -> str:
    """يولّد اسم ملف فريد آمن."""
    ext = ""
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
    if not ext or len(ext) > 5:
        ext_map = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
        }
        ext = ext_map.get(mime_type, "bin")
    return f"{secrets.token_hex(8)}.{ext}"


def upload_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    config: Dict[str, str],
) -> StorageResult:
    """يحفظ الملف محلياً ويعيد رابطاً عاماً نسبياً."""
    folder = config.get("upload_folder", "static/uploads")
    base_url = config.get("public_base_url", "/static/uploads")

    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as e:
        return StorageResult(success=False, error=str(e), provider="local")

    safe_name = _safe_filename(filename, mime_type)
    full_path = os.path.join(folder, safe_name)

    try:
        with open(full_path, "wb") as f:
            f.write(file_bytes)
    except Exception as e:
        return StorageResult(success=False, error=str(e), provider="local")

    public_url = f"{base_url.rstrip('/')}/{safe_name}"

    return StorageResult(
        success=True,
        url=public_url,
        public_id=safe_name,  # نستخدم اسم الملف كـ public_id
        provider="local",
        bytes=len(file_bytes),
    )


def delete_file(public_id: str, config: Dict[str, str]) -> bool:
    """يحذف الملف المحلي."""
    folder = config.get("upload_folder", "static/uploads")
    full_path = os.path.join(folder, public_id)
    try:
        if os.path.exists(full_path):
            os.remove(full_path)
        return True
    except Exception:
        logger.exception("local delete failed")
        return False


def test_connection(config: Dict[str, str]) -> Dict[str, Any]:
    """يتحقق من إمكانية إنشاء/الكتابة في المجلد."""
    folder = config.get("upload_folder", "static/uploads")
    try:
        os.makedirs(folder, exist_ok=True)
        # اختبار كتابة بسيط
        test_path = os.path.join(folder, ".storage_test")
        with open(test_path, "wb") as f:
            f.write(b"test")
        os.remove(test_path)
        return {"success": True, "message": f"Local folder writable: {folder}"}
    except Exception as e:
        return {"success": False, "message": str(e)}