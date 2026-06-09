"""
Shared helper functions used by extracted route modules.

These were originally defined at module level in app.py and are
imported by individual route files so that the logic stays identical.
"""

import json
import os
import uuid

from flask import current_app, request, session


# ── session-role helpers (exact copies from app.py) ──────────────────────

def _staff_session_ok() -> bool:
    """دخول لوحة الفروع/الإدارة فقط — زوار الشات قد يكون لديهم logged_in بدون role."""
    return session.get("role") in ("founder", "admin", "branch")


def _session_founder_only():
    return session.get("role") == "founder"


def _session_founder_or_admin():
    return session.get("role") in ("founder", "admin")


# ── delivery-images form handler (exact copy from app.py) ────────────────

def _persist_company_delivery_images_from_request(database) -> None:
    """يحدّث company_info.delivery_images من نموذج لوحة الإدارة (روابط محفوظة + رفع ملفات)."""
    if request.form.get("company_globals_form") != "1":
        return
    from logic import cloud_storage as cst
    from logic.media_uploads import normalize_stored_media_ref

    raw = request.form.get("delivery_images_json") or "[]"
    try:
        keep = json.loads(raw)
        if not isinstance(keep, list):
            keep = []
    except Exception:
        keep = []
    seen: set[str] = set()
    merged: list[str] = []
    for u in keep:
        s = str(u).strip()
        if s and s not in seen and len(s) < 2000:
            seen.add(s)
            merged.append(s)
    allowed_image_exts = {"png", "jpg", "jpeg", "gif", "webp"}
    upload_folder = current_app.config.get("UPLOAD_FOLDER") or os.path.join(
        current_app.root_path, "static", "uploads"
    )
    for f in request.files.getlist("delivery_image_uploads"):
        if not f or not getattr(f, "filename", None):
            continue
        fn = f.filename
        ext = fn.rsplit(".", 1)[1].lower() if "." in fn else ""
        if ext not in allowed_image_exts:
            continue
        data = f.read()
        if not data:
            continue
        mime = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }.get(ext, "image/jpeg")
        res = cst.upload(data, fn, mime, folder="company-delivery")
        url = (
            normalize_stored_media_ref((res.url or "").strip())
            if getattr(res, "success", False)
            else ""
        )
        if url and url not in seen:
            seen.add(url)
            merged.append(url)
            continue
        unique = f"{uuid.uuid4().hex}.{ext}"
        try:
            os.makedirs(upload_folder, exist_ok=True)
            dest = os.path.join(upload_folder, unique)
            with open(dest, "wb") as out:
                out.write(data)
            rel = f"uploads/{unique}"
            if rel not in seen:
                seen.add(rel)
                merged.append(rel)
        except OSError:
            continue
    database.set_company_info_key(
        "delivery_images", json.dumps(merged[:16], ensure_ascii=False)
    )
