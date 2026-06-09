# -*- coding: utf-8 -*-
"""
WhatsApp Cloud API Webhook routes — extracted from app.py.
"""
from __future__ import annotations

import hmac
import json as _json
import logging
import os
import re
import tempfile
import threading
import time
from typing import TYPE_CHECKING

from flask import jsonify, request, Response, session

try:
    from logic.security import csrf_exempt
except ImportError:
    def csrf_exempt(fn):
        return fn

try:
    from logic.logger_config import app_logger
    logger = app_logger
except ImportError:
    logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from logic.database import DatabaseManager

# ── module-level reference set by register() ──
_db: "DatabaseManager | None" = None

_WA_VERIFY_TOKEN = os.environ.get("WA_VERIFY_TOKEN", "kazem_token_123")


def _wa_runtime_access_token() -> str:
    """يوكن الإرسال: من system_settings ثم البيئة (منطق موحّد مع لوحة الرسائل)."""
    from logic.wa_credentials import wa_access_token
    return wa_access_token()


def _wa_runtime_phone_number_id() -> str:
    """Phone Number ID من system_settings ثم البيئة."""
    from logic.wa_credentials import wa_phone_number_id
    return wa_phone_number_id()


def _wa_collect_inbound_user_messages(body: dict) -> list:
    rows = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            fld = (change.get("field") or "").strip()
            if fld and fld != "messages":
                continue
            val = change.get("value") or {}
            msgs = val.get("messages")
            if not msgs:
                continue
            metadata = val.get("metadata") or {}
            pid = (metadata.get("phone_number_id") or "").strip()
            for msg in msgs:
                if isinstance(msg, dict):
                    rows.append({"msg": msg, "phone_number_id": pid, "value": val})
    return rows


def _wa_sync_contacts_to_customers(db_mgr, value: dict) -> None:
    contacts = value.get("contacts")
    if not contacts or not isinstance(contacts, list):
        return
    seen: set[str] = set()
    for c in contacts:
        if not isinstance(c, dict):
            continue
        wa_id = (c.get("wa_id") or "").strip()
        if not wa_id or wa_id in seen:
            continue
        seen.add(wa_id)
        prof = c.get("profile") if isinstance(c.get("profile"), dict) else {}
        disp = (prof.get("name") or "").strip()
        nm = disp[:200] if disp else "ضيف"
        try:
            db_mgr.get_or_create_customer(name=nm, phone=wa_id, branch_id=None)
        except Exception:
            logger.debug("[WA-Webhook] تعذر مزامنة جهة اتصال wa_id=%s", wa_id, exc_info=True)


def _wa_inbox_profile_from_value(value: dict, wa_from: str) -> str:
    wf = (wa_from or "").strip()
    for c in value.get("contacts") or []:
        if not isinstance(c, dict):
            continue
        if (c.get("wa_id") or "").strip() == wf:
            prof = c.get("profile") if isinstance(c.get("profile"), dict) else {}
            return (prof.get("name") or "").strip()[:200]
    return ""


def _wa_inbox_branch_for_contact(db, wa_from: str):
    try:
        cust = db.get_customer_by_phone(wa_from)
        if cust and cust.get("branch_id") is not None:
            bid = int(cust["branch_id"])
            if db.get_branch_by_id(bid):
                return bid
    except (TypeError, ValueError):
        pass
    return None


def _wa_inbox_store_inbound(db, *, value: dict, wa_from: str, message_body: str) -> None:
    body = (message_body or "").strip()
    if not body:
        return
    try:
        name = _wa_inbox_profile_from_value(value, wa_from)
        bid = _wa_inbox_branch_for_contact(db, wa_from)
        db.wa_inbox_save_message(
            contact_number=wa_from,
            whatsapp_name=name,
            message_body=body[:50000],
            direction="inbound",
            branch_id=bid,
        )
    except Exception:
        logger.warning("[WA-Webhook] فشل حفظ رسالة في صندوق الواتساب (messages)", exc_info=True)


def _wa_normalize_inbound_text(msg: dict) -> tuple[str, str, bool]:
    msg_type = (msg.get("type") or "").strip()
    if msg_type == "text":
        t = (msg.get("text") or {}).get("body") or ""
        return t.strip(), msg_type, False
    if msg_type == "interactive":
        inter = msg.get("interactive") or {}
        itype = (inter.get("type") or "").strip()
        if itype == "button_reply":
            t = (inter.get("button_reply") or {}).get("title") or ""
            return t.strip(), msg_type, False
        if itype == "list_reply":
            t = (inter.get("list_reply") or {}).get("title") or ""
            return t.strip(), msg_type, False
        if itype == "nfm_reply":
            t = (inter.get("nfm_reply") or {}).get("response_json") or ""
            return str(t).strip(), msg_type, False
        return "", msg_type, False
    if msg_type == "button":
        t = (msg.get("button") or {}).get("text") or ""
        return str(t).strip(), msg_type, False
    if msg_type in ("image", "audio", "document", "video"):
        return "", msg_type, True
    return "", msg_type, False


# ── ذاكرة جلسة واتساب ──
_WA_SESSION_L1: dict = {}
_WA_SESSION_CACHE_MAX = 500
_WA_L1_TTL_SEC = 300
_WA_SESSION_IDLE_RESET_SEC = int(os.environ.get("WA_SESSION_IDLE_RESET_SEC", "2700"))
_WA_STATE_KEYS = (
    "pending_inquiry", "chat_last_branch", "chat_selected_branch",
    "last_bot_message", "complaint_active", "complaint_data",
    "complaint_wizard", "complaint_policy_precheck", "pending_intent",
    "chat_pending_action", "chat_current_intent", "user_name",
    "chat_name_declined", "last_inquiry_id", "awaiting_user_name",
    "chat_dialect", "chat_service_turns", "pending_complaint_lookup",
    "user_contact", "name", "chat_pending_branch_phone_offer",
    "chat_islamic_salam_named_count", "chat_intent_score_snapshot",
    "last_intent_category",
    "chat_welcome_sent",
    "complaint_ai_flow",
    "chat_last_product",
    "last_products",
    "pending_product_intent",
    "last_section",
)

_WA_DEDUPE_LOCK = threading.RLock()
_WA_WAMID_L1: dict[str, dict] = {}
_WA_WAMID_TTL_SEC = int(os.environ.get("WA_WAMID_DEDUPE_TTL_SEC", str(72 * 3600)))
_WA_WAMID_MAX = 10000


def _wa_collect_wamids_from_inbound(inbound: list) -> list[str]:
    out: list[str] = []
    for row in inbound or []:
        m = (row or {}).get("msg") or {}
        mid = (m.get("id") or "").strip()
        if mid and mid not in out:
            out.append(mid)
    return out


def _wa_session_l1_prune() -> None:
    if len(_WA_SESSION_L1) <= _WA_SESSION_CACHE_MAX:
        return
    try:
        oldest_sid = min(
            _WA_SESSION_L1.keys(),
            key=lambda sid: float((_WA_SESSION_L1[sid] or {}).get("cached_at") or 0.0),
        )
        del _WA_SESSION_L1[oldest_sid]
    except (ValueError, KeyError, TypeError):
        try:
            del _WA_SESSION_L1[next(iter(_WA_SESSION_L1))]
        except StopIteration:
            pass


def _wa_wamid_prune() -> None:
    db = _db
    now = time.time()
    cut = now - _WA_WAMID_TTL_SEC
    try:
        db.wa_wamids_delete_before(cut)
    except Exception:
        logger.debug("wa_wamids_delete_before failed (non-fatal)", exc_info=True)
    stale_cut = now - 3600
    for k in list(_WA_WAMID_L1.keys()):
        ent = _WA_WAMID_L1.get(k) or {}
        if float(ent.get("cached_at") or 0) < stale_cut:
            _WA_WAMID_L1.pop(k, None)
    if len(_WA_WAMID_L1) <= _WA_WAMID_MAX:
        return
    sorted_items = sorted(
        _WA_WAMID_L1.items(),
        key=lambda x: float((x[1] or {}).get("cached_at") or 0.0),
    )
    overflow = len(_WA_WAMID_L1) - _WA_WAMID_MAX + 500
    for k, _ in sorted_items[: max(0, overflow)]:
        _WA_WAMID_L1.pop(k, None)


def _wa_all_wamids_already_processed(mids: list[str]) -> bool:
    db = _db
    if not mids:
        return False
    now = time.time()
    need_db: list[str] = []
    with _WA_DEDUPE_LOCK:
        for mid in mids:
            m = (mid or "").strip()
            if not m:
                return False
            ent = _WA_WAMID_L1.get(m)
            if ent and (now - float(ent.get("cached_at") or 0)) < _WA_L1_TTL_SEC:
                continue
            need_db.append(m)
        if need_db:
            try:
                found = db.wa_wamids_fetch_processed(need_db)
            except Exception:
                logger.exception("wa_wamids_fetch_processed")
                return False
            for m in need_db:
                if m not in found:
                    return False
                _WA_WAMID_L1[m] = {"processed_at": float(found[m]), "cached_at": now}
            _wa_wamid_prune()
    return True


def _wa_mark_wamids_processed(mids: list[str]) -> None:
    db = _db
    if not mids:
        return
    now = time.time()
    with _WA_DEDUPE_LOCK:
        try:
            db.wa_wamids_mark_processed(mids, now)
        except Exception:
            logger.exception("wa_wamids_mark_processed")
        for mid in mids:
            m = (mid or "").strip()
            if m:
                _WA_WAMID_L1[m] = {"processed_at": now, "cached_at": now}
        _wa_wamid_prune()


def _wa_should_reset_wa_session_cache(msg: str, cached: dict) -> bool:
    MALE = ["رجالي", "رجل", "حج", "إحرام"]
    FEMALE = ["نسائي", "فستان", "عباية"]
    COMPLAINT = ["شكوى", "أشتكي", "زعلان", "مشكلة"]
    last = (cached.get("last_intent_category") or "") if isinstance(cached, dict) else ""
    if any(w in msg for w in MALE) and last == "female":
        return True
    if any(w in msg for w in FEMALE) and last == "male":
        return True
    if any(w in msg for w in COMPLAINT):
        return True
    return False


def _wa_cache_get_prev_state(session_id: str) -> dict:
    db = _db
    sid = (session_id or "").strip()
    now = time.time()
    if not sid:
        return {}
    with _WA_DEDUPE_LOCK:
        ent = _WA_SESSION_L1.get(sid)
        updated_at = 0.0
        st: dict = {}
        if ent and (now - float(ent.get("cached_at") or 0)) < _WA_L1_TTL_SEC:
            updated_at = float(ent.get("updated_at") or 0)
            st = dict(ent.get("state") or {})
        else:
            row = None
            try:
                row = db.wa_session_load(sid)
            except Exception:
                logger.exception("wa_session_load")
            if row:
                updated_at, st = float(row[0] or 0), dict(row[1] or {})
            else:
                updated_at, st = 0.0, {}
            _WA_SESSION_L1[sid] = {"cached_at": now, "updated_at": updated_at, "state": st}
            _wa_session_l1_prune()
        if isinstance(st, dict) and "state" in st and isinstance(st.get("state"), dict):
            st = dict(st["state"])
        if updated_at and (now - updated_at) > _WA_SESSION_IDLE_RESET_SEC:
            logger.info(
                "[WA-Webhook] تصفير ذاكرة الجلسة بعد سكوت (%ss) — جلسة=%s",
                int(now - updated_at), sid[:24],
            )
            return {}
        return st


def _wa_cache_put_state(session_id: str, state: dict) -> None:
    db = _db
    sid = (session_id or "").strip()
    if not sid:
        return
    now = time.time()
    st = dict(state or {})
    with _WA_DEDUPE_LOCK:
        try:
            db.wa_session_save(sid, now, st)
        except Exception:
            logger.exception("wa_session_save")
        _WA_SESSION_L1[sid] = {"cached_at": now, "updated_at": now, "state": st}
        if len(_WA_SESSION_L1) > _WA_SESSION_CACHE_MAX:
            try:
                oldest_sid = min(
                    _WA_SESSION_L1.keys(),
                    key=lambda s: float((_WA_SESSION_L1[s] or {}).get("cached_at") or 0.0),
                )
                del _WA_SESSION_L1[oldest_sid]
            except (ValueError, KeyError, TypeError):
                try:
                    del _WA_SESSION_L1[next(iter(_WA_SESSION_L1))]
                except StopIteration:
                    pass


def _wa_send_message(phone_number_id: str, to: str, text: str) -> bool:
    import requests as _req
    token = _wa_runtime_access_token()
    if not token:
        logger.warning("[WA] WA_ACCESS_TOKEN غير مضبوط — لا يمكن إرسال الرسالة")
        return False
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    try:
        resp = _req.post(url, headers=headers, json=payload, timeout=10)
        if resp.ok:
            logger.info("[WA] رسالة أُرسلت بنجاح إلى %s", to)
            return True
        snip = resp.text[:500] if resp.text else ""
        extra = ""
        if resp.status_code == 401 or "190" in snip or "OAuthException" in snip:
            extra = " — تحديث WA_ACCESS_TOKEN مطلوب (ميتا 401/190)."
        logger.warning("[WA] فشل الإرسال HTTP %s إلى %s — %s%s", resp.status_code, to, snip, extra)
        return False
    except Exception:
        logger.exception("[WA] خطأ أثناء إرسال الرسالة")
        return False


def send_typing_indicator(recipient_id: str, phone_number_id: str, message_id: str) -> None:
    import requests as _req
    try:
        to_log = (recipient_id or "").strip()[:32]
        pid = (phone_number_id or "").strip()
        mid = (message_id or "").strip()
        if not pid or not mid:
            return
        token = _wa_runtime_access_token()
        if not token:
            return
        url = f"https://graph.facebook.com/v19.0/{pid}/messages"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        base = {"messaging_product": "whatsapp", "status": "read", "message_id": mid}
        with_typing = dict(base)
        with_typing["typing_indicator"] = {"type": "text"}
        resp = _req.post(url, headers=headers, json=with_typing, timeout=8)
        if resp.ok:
            logger.debug("[WA] read+typing ok msg=%s to=%s", mid[:24], to_log)
            return
        txt = (resp.text or "")[:400]
        if resp.status_code == 401 or "190" in txt or "OAuthException" in txt:
            logger.warning(
                "[WA] read+typing رُفض (401/توكن) — لن يظهر «يكتب…» ولن تُرسل الردود حتى تحدّث WA_ACCESS_TOKEN. | %s", txt,
            )
        else:
            logger.debug("[WA] read+typing HTTP %s — retry read-only | %s", resp.status_code, (resp.text or "")[:200])
        resp2 = _req.post(url, headers=headers, json=base, timeout=8)
        if resp2.ok:
            logger.debug("[WA] read-only ok msg=%s", mid[:24])
        else:
            txt2 = (resp2.text or "")[:400]
            if resp2.status_code == 401 or "190" in txt2 or "OAuthException" in txt2:
                logger.warning("[WA] read-only رُفض (401/توكن) | %s", txt2)
            else:
                logger.debug("[WA] read-only HTTP %s %s", resp2.status_code, (resp2.text or "")[:200])
    except Exception:
        logger.debug("[WA] send_typing_indicator failed (non-fatal)", exc_info=True)


def _wa_send_image_link(phone_number_id: str, to: str, image_link: str) -> bool:
    import requests as _req
    token = _wa_runtime_access_token()
    if not token:
        logger.warning("[WA] WA_ACCESS_TOKEN غير مضبوط — لا يمكن إرسال الصورة")
        return False
    if not phone_number_id:
        logger.warning("[WA] WA_PHONE_NUMBER_ID غير مضبوط — لا يمكن إرسال الصورة")
        return False
    link = (image_link or "").strip()
    if not link.startswith("https://"):
        logger.warning("[WA] رابط الصورة غير صالح (ليس https): %s", link[:200])
        return False
    if not re.search(r"\.(png|jpe?g|gif|webp)(\?.*)?$", link, re.IGNORECASE):
        logger.info("[WA] رابط بدون امتداد صورة ظاهر — ميتا قد تقبله: %s", link[:200])
    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"link": link}}
    try:
        resp = _req.post(url, headers=headers, json=payload, timeout=10)
        if resp.ok:
            logger.info("[WA] صورة أُرسلت بنجاح إلى %s", to)
            return True
        logger.warning("[WA] فشل إرسال الصورة: %s %s | link=%s", resp.status_code, resp.text[:300], link[:200])
        return False
    except Exception:
        logger.exception("[WA] خطأ أثناء إرسال الصورة")
        return False


def _wa_collect_product_image_links(resp_data: dict) -> list:
    products = resp_data.get("products") or []
    if not isinstance(products, list):
        return []
    seen: set = set()
    out: list = []
    for p in products:
        if not isinstance(p, dict):
            continue
        link = (p.get("image_url") or "").strip()
        if not link:
            link = (p.get("primary_image_href") or "").strip()
        if not link:
            images = p.get("images") or []
            if isinstance(images, list) and images:
                first = images[0]
                link = (str(first) if first is not None else "").strip()
        if not link:
            im1 = (p.get("img1") or "").strip()
            if im1:
                link = im1
        if not link:
            continue
        if link.startswith("http://"):
            link = "https://" + link[len("http://"):]
        if not link.startswith("https://"):
            try:
                from logic.product_service import _product_image_url_abs_https
                link = (_product_image_url_abs_https(link) or "").strip()
            except Exception:
                link = ""
        if link.startswith("https://") and link not in seen:
            seen.add(link)
            out.append(link)
    return out


def _wa_pick_first_product_image_link(resp_data: dict) -> str:
    links = _wa_collect_product_image_links(resp_data or {})
    return links[0] if links else ""


def _wa_download_and_extract_text(media_id: str, mime_type: str):
    import requests as _req
    if not media_id or not _wa_runtime_access_token():
        return None
    _mime_to_ext = {
        "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/png": "png", "image/webp": "webp", "image/gif": "gif",
        "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
        "audio/opus": "ogg", "audio/ogg; codecs=opus": "ogg",
        "audio/wav": "wav", "audio/webm": "webm", "audio/mp4": "m4a",
        "audio/aac": "m4a", "audio/x-m4a": "m4a", "audio/3gpp": "m4a",
    }
    mime_raw = (mime_type or "").strip().lower()
    mime_base = mime_raw.split(";", 1)[0].strip() if mime_raw else ""
    ext = _mime_to_ext.get(mime_raw, "") or _mime_to_ext.get(mime_base, "")
    headers = {"Authorization": f"Bearer {_wa_runtime_access_token()}"}
    try:
        meta_resp = _req.get(f"https://graph.facebook.com/v19.0/{media_id}", headers=headers, timeout=10)
        if not meta_resp.ok:
            logger.warning("[WA-Media] فشل جلب رابط الميديا: %s", meta_resp.text[:200])
            return None
        media_url = meta_resp.json().get("url", "")
        if not media_url:
            return None
    except Exception:
        logger.exception("[WA-Media] خطأ عند جلب رابط الميديا")
        return None
    try:
        dl_resp = _req.get(media_url, headers=headers, timeout=30)
        if not dl_resp.ok:
            logger.warning("[WA-Media] فشل تحميل الملف: %s", dl_resp.status_code)
            return None
    except Exception:
        logger.exception("[WA-Media] خطأ أثناء تحميل الملف")
        return None
    if not ext:
        dl_ct = (dl_resp.headers.get("Content-Type") or "").strip().lower()
        dl_ct_base = dl_ct.split(";", 1)[0].strip() if dl_ct else ""
        ext = _mime_to_ext.get(dl_ct, "") or _mime_to_ext.get(dl_ct_base, "")
        if not ext:
            logger.info("[WA-Media] mime غير مدعوم: payload=%s download=%s", mime_type, dl_resp.headers.get("Content-Type"))
            return None
    try:
        from logic.attachment_openai import text_from_saved_file
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(dl_resp.content)
            tmp_path = tmp.name
        result = text_from_saved_file(tmp_path, ext)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        if isinstance(result, dict):
            return (result.get("message") or "").strip() or None
        return result
    except Exception:
        logger.exception("[WA-Media] خطأ أثناء استخراج النص")
        return None


def process_message(data) -> None:
    """معالجة ويبهوك واتساب في خيط خلفي."""
    db = _db
    _app = _flask_app

    if not isinstance(data, dict):
        logger.warning("[WA-Webhook][bg] process_message: payload غير صالح")
        return

    raw_body = data.get("raw_body") or b""
    if not isinstance(raw_body, (bytes, bytearray)):
        raw_body = bytes(raw_body) if raw_body else b""
    remote_addr = str(data.get("remote_addr") or "").strip() or "?"
    sig_header = str(data.get("sig_header") or "")

    logger.info("[WA-Webhook][bg] start len=%s from=%s", len(raw_body or b""), remote_addr or "?")

    # ── التحقق من توقيع Meta (HMAC-SHA256) ──
    try:
        from logic.security import verify_meta_signature
        from logic.integrations.base import read_setting

        app_secret = (read_setting("META_APP_SECRET", "") or "").strip()
        if not app_secret:
            app_secret = (os.environ.get("META_APP_SECRET", "") or "").strip()

        if app_secret:
            if not verify_meta_signature(raw_body, sig_header or "", app_secret):
                logger.warning(
                    "[WA-Webhook][bg] HMAC فشل — رفض POST. تحقق: App Secret = Settings→Basic في نفس التطبيق، "
                    "بدون فراغات زائدة. | من %s",
                    remote_addr or "unknown",
                )
                return
        else:
            _skip_sig = os.environ.get(
                "WA_WEBHOOK_SKIP_SIGNATURE", ""
            ).strip().lower() in ("1", "true", "yes")

            if _skip_sig:
                logger.warning(
                    "[WA-Webhook][bg] ⚠️ META_APP_SECRET غير مهيّأ — "
                    "تم تجاوز التحقق بسبب WA_WEBHOOK_SKIP_SIGNATURE=true. "
                    "لا تستخدم هذا الإعداد في الإنتاج!"
                )
            else:
                logger.warning(
                    "[WA-Webhook][bg] ❌ META_APP_SECRET غير مهيّأ — "
                    "رفض معالجة الرسالة (أمان). "
                    "أضف المفتاح من: لوحة المؤسس → التكاملات → الواتساب، "
                    "أو من Facebook Developer Console → App Settings → Basic → App Secret. "
                    "للتطوير المحلي فقط: عيّن WA_WEBHOOK_SKIP_SIGNATURE=true في .env"
                )
                return
    except ImportError:
        logger.warning("[WA-Webhook][bg] security module unavailable; signature verify skipped")
    except Exception as _ve:
        logger.exception("[WA-Webhook][bg] signature verification error: %s", _ve)
        return

    try:
        body = _json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        logger.warning("[WA-Webhook][bg] فشل parse JSON (طول الجسم %s)", len(raw_body or b""))
        body = {}
    logger.info("[WA-Webhook][bg] object=%r head=%s", body.get("object"), _json.dumps(body, ensure_ascii=False)[:800])

    try:
        inbound = _wa_collect_inbound_user_messages(body)
        if not inbound:
            logger.info("[WA-Webhook][bg] لا messages مستخدم (غالباً statuses فقط); object=%r", body.get("object"))
            return

        wamids_batch = _wa_collect_wamids_from_inbound(inbound)
        with _WA_DEDUPE_LOCK:
            if wamids_batch and _wa_all_wamids_already_processed(wamids_batch):
                logger.info("[WA-Webhook][bg] تخطّي — سبقت معالجة معرفات الرسائل: %s", wamids_batch[:8])
                return
            if wamids_batch:
                _wa_mark_wamids_processed(wamids_batch)

        row0 = inbound[0]
        value_full = row0.get("value") or {}
        try:
            _wa_sync_contacts_to_customers(db, value_full)
        except Exception:
            logger.debug("[WA-Webhook][bg] مزامنة contacts (غير حرجة)", exc_info=True)

        msg = row0["msg"]
        phone_id = (row0.get("phone_number_id") or "").strip() or _wa_runtime_phone_number_id()
        send_phone_id = (_wa_runtime_phone_number_id() or phone_id or "").strip()
        wa_from = (msg.get("from") or "").strip()
        msg_type = (msg.get("type") or "").strip()

        logger.info("[WA-Webhook][bg] نوع=%s من=%s send_phone_id=%s", msg_type, wa_from, send_phone_id or "(فارغ)")

        if not wa_from:
            logger.info("[WA-Webhook][bg] لا يوجد from — تجاهل")
            return

        if not send_phone_id:
            logger.warning("[WA-Webhook][bg] Phone Number ID مفقود — لن يُرسل رد API")
            w_quick, _, im_quick = _wa_normalize_inbound_text(msg)
            if im_quick:
                mt_q = (msg.get("type") or "").strip()
                media_obj = msg.get(mt_q) or {}
                if isinstance(media_obj, dict):
                    cap_q = (media_obj.get("caption") or "").strip()
                    w_quick = cap_q or f"[واتساب:{mt_q or 'media'}]"
                else:
                    w_quick = f"[واتساب:{mt_q or 'media'}]"
            if not w_quick:
                mt_fallback = (msg.get("type") or "").strip()
                w_quick = f"[واتساب:{mt_fallback}]" if mt_fallback else ""
            if w_quick:
                _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=w_quick)
            return

        wa_msg_id = (msg.get("id") or "").strip()
        if wa_msg_id:
            try:
                send_typing_indicator(wa_from, send_phone_id, wa_msg_id)
            except Exception:
                logger.debug("[WA-Webhook][bg] read/typing wrapper failed (non-fatal)", exc_info=True)

        wa_text, norm_type, is_media = _wa_normalize_inbound_text(msg)

        if is_media:
            media_obj = msg.get(msg_type) or {}
            media_id = media_obj.get("id", "")
            mime_type = media_obj.get("mime_type", "")
            caption = (media_obj.get("caption") or "").strip()
            logger.info("[WA-Webhook][bg] media type=%s id=%s mime=%s", msg_type, media_id, mime_type)
            _is_image_type = msg_type in ("image", "sticker") or (mime_type or "").startswith("image/")
            if _is_image_type:
                try:
                    from logic.integrations.base import read_setting as _rs
                    _img_enabled = (_rs("image_analysis_enabled", "0") or "0").strip()
                except Exception:
                    _img_enabled = "1"
                if _img_enabled == "0":
                    _inbox_body = caption if caption else f"[صورة واتساب:{msg_type}]"
                    _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=_inbox_body)
                    _wa_send_message(send_phone_id, wa_from, "معذرة، حالياً ما أقدر أتعرف على الصورة، لكن أبشر تم إرسالها للفرع وسيتم الرد عليك قريباً.")
                    return

            extracted = _wa_download_and_extract_text(media_id, mime_type)
            if extracted:
                wa_text = (caption + "\n" + extracted).strip() if caption else extracted
            elif caption:
                wa_text = caption
            else:
                fail_body = caption or f"[واتساب:{msg_type}]"
                _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=fail_body)
                _wa_send_message(send_phone_id, wa_from, "عذراً، لا أستطيع معالجة هذا النوع من الملفات حالياً.")
                return
        elif not wa_text:
            logger.info("[WA-Webhook][bg] نوع غير مدعوم أو بلا نص قابل للمعالجة: %s", msg_type)
            _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=f"[واتساب:{msg_type}]")
            return

        from logic.wa_inbox_repository import WA_BLOCKED_AI_AUTOREPLY_AR
        try:
            _controls = db.wa_contact_get_controls(wa_from)
        except Exception:
            _controls = {"ai_stopped": 0, "banned": 0}

        if _controls.get("banned"):
            _ban_body = wa_text or f"[واتساب:{msg_type}]"
            _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=_ban_body)
            _already_sent_ban_reply = False
            try:
                _recent = db.wa_inbox_list_messages(wa_from, branch_id=None)
                for _rmsg in reversed(_recent or []):
                    if _rmsg.get("direction") == "outbound":
                        if WA_BLOCKED_AI_AUTOREPLY_AR in str(_rmsg.get("message_body") or ""):
                            _already_sent_ban_reply = True
                        break
            except Exception:
                pass
            if not _already_sent_ban_reply:
                _wa_send_message(send_phone_id, wa_from, WA_BLOCKED_AI_AUTOREPLY_AR)
                try:
                    _ban_bid = _wa_inbox_branch_for_contact(db, wa_from)
                    db.wa_inbox_save_message(
                        contact_number=wa_from, whatsapp_name="النظام",
                        message_body=WA_BLOCKED_AI_AUTOREPLY_AR,
                        direction="outbound", branch_id=_ban_bid, sender_type="system",
                    )
                except Exception:
                    logger.debug("[WA] حفظ رد الحظر (غير حرج)", exc_info=True)
            return

        if _controls.get("ai_stopped"):
            _stop_body = wa_text or f"[واتساب:{msg_type}]"
            _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=_stop_body)
            return

        if len(inbound) > 1:
            _merged_parts = []
            for _i, _inv in enumerate(inbound):
                if _i == 0:
                    if wa_text:
                        _merged_parts.append(wa_text)
                else:
                    _w_extra, _, __ = _wa_normalize_inbound_text(_inv["msg"])
                    if _w_extra:
                        _merged_parts.append(_w_extra)
            if _merged_parts:
                wa_text = "\n".join(_merged_parts).strip()

        _wa_inbox_store_inbound(db, value=value_full, wa_from=wa_from, message_body=wa_text)

        logger.info("[WA-Webhook][bg] → الشات: نوع=%s نص=%s", norm_type or msg_type, (wa_text[:200] + "…") if len(wa_text) > 200 else wa_text)

        wa_session_id = f"wa_{wa_from}"
        fake_body = _json.dumps({"message": wa_text}).encode("utf-8")
        _prev_state = _wa_cache_get_prev_state(wa_session_id)
        welcome_needed = not bool((_prev_state or {}).get("chat_welcome_sent"))
        wa_profile_nm = _wa_inbox_profile_from_value(value_full, wa_from)
        if wa_profile_nm:
            _generic_nm = {"", "أخوي", "حضرتك", "ضيف", "عميلنا", "العميل", "زائر"}
            cur_nm = (_prev_state.get("user_name") or "").strip()
            if (not cur_nm) or (cur_nm in _generic_nm):
                _prev_state = dict(_prev_state)
                _prev_state["user_name"] = wa_profile_nm[:120]
        _captured_state: dict = {}

        with _app.test_request_context(
            "/chat_query", method="POST", data=fake_body,
            content_type="application/json",
            environ_base={"REMOTE_ADDR": wa_from},
        ):
            from flask import session as _sess
            _sess["user_id"] = wa_session_id
            _sess["sid"] = wa_session_id
            if _wa_should_reset_wa_session_cache(wa_text, _prev_state):
                _prev_state = {}
            for _k, _v in _prev_state.items():
                if _k not in ("user_id", "sid"):
                    _sess[_k] = _v
            _sess.modified = True
            from logic.chat_router import dispatch_chat_query
            result = dispatch_chat_query()
            _captured_state = {
                k: _sess[k] for k in _WA_STATE_KEYS
                if k in _sess and _sess[k] is not None
            }

        resp_obj = result[0] if isinstance(result, tuple) else result
        resp_data = (resp_obj.get_json(silent=True) or {}) if hasattr(resp_obj, "get_json") else {}
        reply_text = (resp_data.get("message") or "").strip()
        if welcome_needed:
            display_name = (wa_profile_nm or _captured_state.get("user_name") or "").strip()
            welcome_name = (display_name if display_name else "العميل").strip()
            if len(welcome_name) > 36 or welcome_name.count(" ") >= 4:
                welcome_name = "العميل"
            welcome_line = f"أهلاً بك في مجمع العائلة أستاذ {welcome_name}"
            reply_text = f"{welcome_line}\n{reply_text}" if reply_text else welcome_line
            _captured_state["chat_welcome_sent"] = True

        if _captured_state:
            _wa_cache_put_state(wa_session_id, _captured_state)

        logger.info("[WA-Webhook][bg] طول_رد=%s مفاتيح_json=%s", len(reply_text), list(resp_data.keys())[:15])

        if not reply_text:
            logger.warning("[WA-Webhook][bg] dispatch أعاد رداً فارغاً — user=%s", wa_from)

        for image_link in _wa_collect_product_image_links(resp_data):
            if send_phone_id:
                _wa_send_image_link(send_phone_id, wa_from, image_link)

        if reply_text and send_phone_id:
            _sent_ok = _wa_send_message(send_phone_id, wa_from, reply_text)
            if not _sent_ok:
                logger.warning("[WA-Webhook][bg] فشل إرسال الرد لواتساب للمستخدم %s", wa_from)
            else:
                try:
                    _ai_bid = _wa_inbox_branch_for_contact(db, wa_from)
                    db.wa_inbox_save_message(
                        contact_number=wa_from, whatsapp_name="AI",
                        message_body=reply_text, direction="outbound",
                        branch_id=_ai_bid, sender_type="ai",
                    )
                except Exception:
                    logger.debug("[WA-Webhook][bg] حفظ رد AI (غير حرج)", exc_info=True)

    except Exception:
        logger.exception("[WA-Webhook][bg] error while processing message")


# ── module-level reference to Flask app ──
_flask_app = None


def register(app, db):
    global _db, _flask_app
    _db = db
    _flask_app = app

    @app.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
    @csrf_exempt
    def whatsapp_webhook():
        """
        WhatsApp Cloud API — مسار ثابت حرفياً /webhook (بدون Blueprint ولا url_prefix).
        """
        if request.method == "GET":
            try:
                from logic.integrations.base import read_setting
                expected = read_setting("WA_VERIFY_TOKEN", _WA_VERIFY_TOKEN)
            except Exception:
                expected = _WA_VERIFY_TOKEN

            mode = request.args.get("hub.mode", "")
            token = request.args.get("hub.verify_token", "")
            challenge = request.args.get("hub.challenge", "")

            if not mode and not token:
                return Response("ok", status=200, mimetype="text/plain")

            if mode == "subscribe" and expected and token:
                if hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
                    logger.info("[WA-Webhook] Verification successful ✅")
                    return Response(challenge, status=200, mimetype="text/plain")

            logger.warning("[WA-Webhook] Verification failed ❌ — mode=%s", mode)
            return Response("Forbidden", status=403, mimetype="text/plain")

        # POST
        payload = {
            "raw_body": request.get_data(cache=True) or b"",
            "remote_addr": request.remote_addr or "unknown",
            "sig_header": request.headers.get("X-Hub-Signature-256", ""),
        }
        threading.Thread(target=process_message, args=(payload,), daemon=True).start()
        return jsonify({"status": "ok"}), 200
