# -*- coding: utf-8 -*-
"""
رفع الصور عبر منصة التخزين النشطة (لوحة التكاملات) مع احتياط محلي عند الفشل.
القيمة المُعادة مناسبة للحفظ في قاعدة البيانات: رابط https كامل أو uploads/...
"""
from __future__ import annotations

import io
import logging
import os
import uuid
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from werkzeug.datastructures import FileStorage

logger = logging.getLogger(__name__)


def normalize_stored_media_ref(url: str) -> str:
    """يوحّد شكل المرجع المخزّن: https كما هو، وإلا مسار نسبي تحت static (uploads/...)."""
    u = (url or "").strip().replace("\\", "/")
    if not u:
        return ""
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("/static/"):
        return u[len("/static/") :].lstrip("/")
    if u.startswith("static/"):
        return u[len("static/") :].lstrip("/")
    return u.lstrip("/")


def upload_image_bytes(
    data: bytes,
    filename: str,
    mime_type: str,
    *,
    folder: str,
) -> str:
    """يرفع البايتات للسحابة عند التهيئة؛ وإلا يحفظ محلياً تحت static/uploads."""
    if not data:
        raise ValueError("empty image data")
    from logic import cloud_storage as cst

    fn = (filename or "image.jpg").strip() or "image.jpg"
    mime = (mime_type or "image/jpeg").strip() or "image/jpeg"
    res = cst.upload(data, fn, mime, folder=folder)
    url = (res.url or "").strip()
    if res.success and url:
        return normalize_stored_media_ref(url)

    from config import UPLOAD_FOLDER

    ext = "jpg"
    if "." in fn:
        ext = fn.rsplit(".", 1)[-1].lower()
        if ext == "jpeg":
            ext = "jpg"
    if ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
        ext = "jpg"
    unique = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    dest = os.path.join(UPLOAD_FOLDER, unique)
    with open(dest, "wb") as out:
        out.write(data)
    logger.warning("cloud upload failed, saved locally: %s", unique)
    return f"uploads/{unique}"


def png_bytes_from_image_bytes(data: bytes) -> bytes:
    """يحوّل أي صورة صالحة إلى PNG في الذاكرة (للشعارات وما شابه)."""
    from PIL import Image, UnidentifiedImageError

    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    else:
        im = im.convert("RGBA")
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _mime_to_ext_fname(mime: str, stem: str) -> tuple[str, str]:
    mt = (mime or "image/jpeg").strip().lower()
    ext = "jpg"
    if mt == "image/png":
        ext = "png"
    elif mt == "image/webp":
        ext = "webp"
    elif mt == "image/gif":
        ext = "gif"
    elif mt in ("image/jpeg", "image/jpg"):
        ext = "jpg"
    return ext, f"{stem}.{ext}"


def upload_branding_image_via_cloud_then_png(
    data: bytes,
    detected_mime: str,
    *,
    logical_stem: str,
    folder: str = "site-logos",
) -> Tuple[str, str]:
    """
    يرفع الشعار إلى التخزين السحابي المفعّل بأقل معالجة:
    المحاولة الأولى بالصيغة الأصلية (JPEG/PNG/WebP)، ثم إعادة الترميز PNG عند الفشل.
    يعيد (ref للإعداد، public_id فارغ لو محلي).
    """
    if not data:
        raise ValueError("empty branding image")
    import uuid as _uuid
    from logic import cloud_storage as cst

    ext, _fn = _mime_to_ext_fname(detected_mime, logical_stem)
    mime_primary = (detected_mime or "image/jpeg").strip() or "image/jpeg"
    uid = _uuid.uuid4().hex[:10]
    cloud_name = f"{logical_stem}-{uid}.{ext}"

    res = cst.upload(data, cloud_name, mime_primary, folder=folder)
    if res:
        return normalize_stored_media_ref(res.url or ""), (res.public_id or "").strip()

    png = png_bytes_from_image_bytes(data)
    res2 = cst.upload(png, f"{logical_stem}-{uid}-reencode.png", "image/png", folder=folder)
    if res2:
        return normalize_stored_media_ref(res2.url or ""), (res2.public_id or "").strip()

    local_ref = upload_image_bytes(png, f"{logical_stem}.png", "image/png", folder=folder)
    return normalize_stored_media_ref(local_ref), ""


def public_https_url(ref: Optional[str]) -> str:
    """رابط يمكن لميتا/واتساب جلبه (يفضّل https + PUBLIC_BASE_URL للملفات المحلية)."""
    p = (ref or "").strip()
    if not p:
        return ""
    if p.startswith("https://"):
        return p
    if p.startswith("http://"):
        return "https://" + p[len("http://") :]
    base = (os.getenv("PUBLIC_BASE_URL") or os.getenv("BASE_URL") or "").strip().rstrip("/")
    if p.startswith("/static/"):
        rel = p
    elif p.startswith("static/"):
        rel = "/" + p
    else:
        rel = "/static/" + p.lstrip("/")
    if base.startswith("https://"):
        return base + rel
    return rel if rel.startswith("http") else ""


def file_storage_to_upload(
    file_storage: "FileStorage",
    *,
    folder: str,
    require_validation: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    """
    يقرأ FileStorage ويرفع المحتوى.
    يعيد (مرجع للتخزين, رسالة_خطأ).
    """
    if not file_storage or not getattr(file_storage, "filename", None):
        return None, None
    data: bytes
    mime = "image/jpeg"
    if require_validation:
        try:
            from logic.security import validate_image_upload
        except ImportError:
            validate_image_upload = None  # type: ignore
        if validate_image_upload is None:
            return None, "التحقق من الصور غير متاح."
        ok, err, data, mime = validate_image_upload(file_storage)
        if not ok or not data:
            return None, err or "ملف الصورة مرفوض."
    else:
        try:
            file_storage.stream.seek(0)
        except (OSError, AttributeError):
            pass
        data = file_storage.read()
        if not data:
            return None, "ملف فارغ."
    try:
        ref = upload_image_bytes(
            data,
            file_storage.filename or "img.jpg",
            mime or "image/jpeg",
            folder=folder,
        )
        return ref, None
    except ValueError as e:
        return None, str(e)


def collect_product_images_from_request(files, *, max_images: int = 3) -> List[str]:
    """يجمع صور المنتج من request.files.getlist مع التحقق والرفع للسحابة."""
    out: List[str] = []
    for f in files or []:
        if len(out) >= max_images:
            break
        if not f or not getattr(f, "filename", None):
            continue
        ref, _err = file_storage_to_upload(f, folder="products", require_validation=True)
        if ref:
            out.append(ref)
    return out
