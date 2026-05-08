"""
شعار الموقع المركزي: مسار نسبي تحت static/ ومخزّن في system_settings (site_logo_path).
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

from flask import Flask

if TYPE_CHECKING:
    from werkzeug.datastructures import FileStorage

SITE_LOGO_SETTING_KEY = "site_logo_path"
"""مفتاح القيمة في جدول system_settings."""

SITE_LOGO_CLOUD_ID_KEY = "site_logo_cloud_public_id"
"""معرّف الملف في منصة التخزين (للحذف عند الاستبدال)."""

SITE_LOGO_RELATIVE = "uploads/logo.png"
"""المسار النسبي داخل مجلد static (كما يُمرَّر إلى url_for('static', filename=...))."""

FOUNDER_LOGO_SETTING_KEY = "founder_logo_path"
"""شعار لوحة المؤسس فقط (منفصل عن الشعار العام)."""

FOUNDER_LOGO_CLOUD_ID_KEY = "founder_logo_cloud_public_id"

FOUNDER_LOGO_RELATIVE = "uploads/founder_logo.png"

ALLOWED_LOGO_FILES = frozenset({"logo.png", "founder_logo.png"})


def _static_root(app: Flask) -> str:
    return os.path.normpath(os.path.join(app.root_path, app.static_folder or "static"))


def resolve_site_logo_url(app: Flask, stored_value: Optional[str]) -> Optional[str]:
    """
    يُرجع رابطاً لعرض الشعار: URL سحابي، أو url_for للملف المحلي إن وُجد.
    يُفضّل استدعاؤه داخل سياق طلب لـ url_for وطلبات نسبية /static/.
    """
    from flask import has_request_context, request, url_for

    raw = (stored_value or "").strip()
    if not raw or ".." in raw:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("/static/"):
        if has_request_context():
            return request.url_root.rstrip("/") + raw
        return raw
    if not raw.startswith("uploads/"):
        return None
    parts = [p for p in raw.replace("\\", "/").split("/") if p and p != "."]
    if not parts or parts[0] != "uploads":
        return None
    full = os.path.normpath(os.path.join(_static_root(app), *parts))
    static_root = _static_root(app)
    if not full.startswith(static_root) or not os.path.isfile(full):
        return None
    return url_for("static", filename="/".join(parts))


def get_public_logo_url(app: Flask, db) -> Optional[str]:
    """
    مصدر واحد لرابط الشعار العام في كل الواجهات (مستخدم، فرع، إدارة، حملات).
    يقرأ من system_settings (مفتاح site_logo_path) والملف تحت static/uploads/ (مثل uploads/logo.png).
    """
    stored = db.get_system_setting(SITE_LOGO_SETTING_KEY, "") or ""
    return resolve_site_logo_url(app, stored)


def save_uploaded_logo_as_png(
    file_storage: "FileStorage", upload_folder: str, dest_filename: str = "logo.png"
) -> None:
    """يحوّل الصورة ويحفظها كـ PNG داخل upload_folder (logo.png أو founder_logo.png)."""
    from PIL import Image, UnidentifiedImageError

    if dest_filename not in ALLOWED_LOGO_FILES:
        raise ValueError("اسم ملف الشعار غير مسموح.")

    if not file_storage or not file_storage.filename:
        raise ValueError("لم يُرفع ملف.")

    upload_folder = os.path.abspath(upload_folder)
    os.makedirs(upload_folder, exist_ok=True)
    dest = os.path.join(upload_folder, dest_filename)

    try:
        file_storage.stream.seek(0)
    except (OSError, AttributeError):
        pass

    try:
        im = Image.open(file_storage.stream)
        im.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("صورة غير صالحة. استخدم PNG أو JPEG أو WebP.") from e

    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    else:
        im = im.convert("RGBA")

    im.save(dest, "PNG", optimize=True)


def remove_logo_file(upload_folder: str, dest_filename: str) -> bool:
    """يحذف ملف الشعار من مجلد الرفع. يُرجع True إن وُجد الملف وحُذف."""
    if dest_filename not in ALLOWED_LOGO_FILES:
        raise ValueError("اسم ملف الشعار غير مسموح.")
    upload_folder = os.path.normpath(os.path.abspath(upload_folder))
    full = os.path.normpath(os.path.join(upload_folder, dest_filename))
    if not full.startswith(upload_folder + os.sep):
        return False
    if not os.path.isfile(full):
        return False
    os.remove(full)
    return True


def save_png_bytes_to_upload_folder(png_bytes: bytes, upload_folder: str, dest_filename: str) -> None:
    """يكتب بايتات PNG جاهزة إلى uploads/logo.png أو founder_logo.png (احتياط محلي)."""
    if dest_filename not in ALLOWED_LOGO_FILES:
        raise ValueError("اسم ملف الشعار غير مسموح.")
    upload_folder = os.path.abspath(upload_folder)
    os.makedirs(upload_folder, exist_ok=True)
    dest = os.path.join(upload_folder, dest_filename)
    with open(dest, "wb") as out:
        out.write(png_bytes)


def delete_remote_storage_public_id(public_id: Optional[str]) -> None:
    pid = (public_id or "").strip()
    if not pid:
        return
    try:
        from logic import cloud_storage as cst

        cst.delete(pid)
    except Exception:
        logger.debug("cloud delete logo skipped", exc_info=True)


def clear_branding_from_disk_and_settings(
    db,
    *,
    upload_folder: str,
    path_setting_key: str,
    cloud_id_setting_key: str,
    legacy_filename: str,
) -> None:
    """يمسح إعداد الشعار في DB ويحذف الملف المحلي أو أصل سحابي عند الإمكان."""
    path = (db.get_system_setting(path_setting_key) or "").strip()
    pid = (db.get_system_setting(cloud_id_setting_key) or "").strip()
    delete_remote_storage_public_id(pid)
    if path.startswith(("http://", "https://")):
        pass
    elif path.startswith("uploads/"):
        rest = path[len("uploads/") :].lstrip("/")
        if rest and ".." not in rest:
            full = os.path.normpath(os.path.join(os.path.abspath(upload_folder), rest))
            up = os.path.normpath(os.path.abspath(upload_folder))
            if full.startswith(up + os.sep) or full == up:
                try:
                    if os.path.isfile(full):
                        os.remove(full)
                except OSError:
                    pass
    else:
        try:
            remove_logo_file(upload_folder, legacy_filename)
        except ValueError:
            pass
    db.set_system_setting(path_setting_key, "")
    db.set_system_setting(cloud_id_setting_key, "")
