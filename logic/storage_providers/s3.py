# -*- coding: utf-8 -*-
"""
S3 / R2 provider — AWS S3 و Cloudflare R2 (S3-compatible).

R2 (الموصى به للحجوم الكبيرة):
    - 10GB storage مجاناً
    - صفر egress fees (مجاني عند العرض)
    - $0.015/GB بعد ذلك
    - متوافق مع S3 API

AWS S3:
    - 5GB سنة أولى مجاناً
    - $0.023/GB بعدها
    - يحتاج CloudFront CDN منفصل

التثبيت:
    pip install boto3

R2 setup:
    1. سجّل في Cloudflare → R2
    2. أنشئ bucket
    3. أنشئ API Token (S3-compatible credentials)
    4. اربط Custom Domain أو فعّل R2.dev URL
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Dict

from logic.cloud_storage import StorageResult

logger = logging.getLogger(__name__)


def _make_client(config: Dict[str, str]):
    """ينشئ boto3 S3 client (يدعم R2 عبر endpoint مخصص)."""
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except ImportError:
        return None

    # تحديد endpoint حسب النوع
    endpoint = config.get("endpoint", "").strip()
    account_id = config.get("account_id", "").strip()
    if not endpoint and account_id:
        # R2: https://<account_id>.r2.cloudflarestorage.com
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    region = config.get("region", "auto").strip() or "auto"

    try:
        return boto3.client(
            "s3",
            aws_access_key_id=config.get("access_key", ""),
            aws_secret_access_key=config.get("secret_key", ""),
            region_name=region,
            endpoint_url=endpoint or None,
            config=Config(signature_version="s3v4"),
        )
    except Exception:
        logger.exception("S3/R2 client init failed")
        return None


def _safe_key(filename: str, mime_type: str, folder: str = "") -> str:
    """يولّد key فريد آمن."""
    ext = ""
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
    if not ext or len(ext) > 5:
        ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
        ext = ext_map.get(mime_type, "bin")
    name = f"{secrets.token_hex(12)}.{ext}"
    if folder:
        return f"{folder.strip('/')}/{name}"
    return name


def upload_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    config: Dict[str, str],
) -> StorageResult:
    """يرفع لـ S3 أو R2."""
    client = _make_client(config)
    if client is None:
        return StorageResult(
            success=False,
            error="boto3 library not installed (pip install boto3)",
            provider="s3",
        )

    bucket = config.get("bucket", "").strip()
    if not bucket:
        return StorageResult(success=False, error="bucket missing", provider="s3")

    key = _safe_key(filename, mime_type, config.get("folder", ""))

    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=file_bytes,
            ContentType=mime_type,
            CacheControl="public, max-age=31536000",
        )

        # بناء الرابط العام
        public_base = config.get("public_base_url", "").strip().rstrip("/")
        if public_base:
            url = f"{public_base}/{key}"
        elif config.get("account_id"):
            # R2 بدون custom domain
            url = f"https://{config['account_id']}.r2.cloudflarestorage.com/{bucket}/{key}"
        else:
            # AWS S3
            region = config.get("region", "us-east-1")
            url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

        return StorageResult(
            success=True,
            url=url,
            public_id=key,
            provider="s3",
            bytes=len(file_bytes),
        )
    except Exception as e:
        logger.exception("S3/R2 upload failed")
        return StorageResult(success=False, error=str(e), provider="s3")


def delete_file(public_id: str, config: Dict[str, str]) -> bool:
    """يحذف من S3/R2 (public_id = key)."""
    client = _make_client(config)
    if client is None:
        return False

    bucket = config.get("bucket", "").strip()
    if not bucket:
        return False

    try:
        client.delete_object(Bucket=bucket, Key=public_id)
        return True
    except Exception:
        logger.exception("S3/R2 delete failed")
        return False


def test_connection(config: Dict[str, str]) -> Dict[str, Any]:
    """يفحص الاتصال + صلاحيات الـ bucket."""
    client = _make_client(config)
    if client is None:
        return {"success": False, "message": "boto3 library not installed"}

    bucket = config.get("bucket", "").strip()
    if not bucket:
        return {"success": False, "message": "bucket name missing"}

    try:
        # head_bucket يفحص المصادقة + وجود الـ bucket
        client.head_bucket(Bucket=bucket)
        return {"success": True, "message": f"Connected to bucket: {bucket}"}
    except Exception as e:
        return {"success": False, "message": str(e)}