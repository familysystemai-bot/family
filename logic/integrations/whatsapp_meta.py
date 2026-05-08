# -*- coding: utf-8 -*-
"""
واتساب — استدعاءات Meta Graph API للاختبار والإرسال البسيط.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

_GRAPH_MESSAGES = "https://graph.facebook.com/v19.0/{phone_number_id}/messages"


def normalize_whatsapp_recipient(raw: str) -> Tuple[bool, str]:
    """
    يحوّل إدخال المستخدم إلى أرقام فقط بصيغة دولية (مثلاً 9665xxxxxxxx).
    يدعم أرقام سعودية محلية تبدأ بـ 05.
    """
    s = (raw or "").strip()
    s = re.sub(r"[\s\-]", "", s)
    if not s:
        return False, ""
    digits = re.sub(r"\D", "", s)
    if len(digits) < 8:
        return False, ""
    if len(digits) == 10 and digits.startswith("05"):
        digits = "966" + digits[1:]
    elif len(digits) == 9 and digits.startswith("5"):
        digits = "966" + digits
    return True, digits


def send_test_text_message(
    phone_number_id: str,
    access_token: str,
    to_raw: str,
    *,
    body: str | None = None,
    timeout: int = 20,
) -> Tuple[bool, str]:
    """
    يرسل رسالة نصية تجريبية عبر Cloud API.

    Returns:
        (نجح, رسالة للعرض في الواجهة)
    """
    ok_norm, to_digits = normalize_whatsapp_recipient(to_raw)
    if not ok_norm:
        return False, "رقم غير صالح. استخدم صيغة دولية أو سعودية مثل 05xxxxxxxx."

    pid = (phone_number_id or "").strip()
    token = (access_token or "").strip()
    if not pid or not token:
        return False, "ناقص Phone Number ID أو Access Token."

    text_body = (body or "").strip() or (
        "أهلاً بك في مجمع العائلة.\n"
        "هاذي الرساله اختبار من لوحه التحكم الرئيسيه.\n"
        "عذران عن اي ازعاج حصل."
    )

    url = _GRAPH_MESSAGES.format(phone_number_id=pid)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to_digits,
        "type": "text",
        "text": {"body": text_body[:4096]},
    }

    try:
        import requests

        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as e:
        logger.exception("send_test_text_message: request failed")
        return False, f"تعذّر الاتصال بـ Meta: {e}"

    try:
        data = r.json()
    except Exception:
        return False, f"رد غير JSON من Meta (HTTP {r.status_code})."

    if r.ok and isinstance(data.get("messages"), list) and data["messages"]:
        mid = (data["messages"][0] or {}).get("id", "")
        if mid:
            return True, f"أرسلت Meta الرسالة بنجاح (message id: {mid}). تحقق من واتساب الرقم {to_digits}."
        return True, "أرسلت Meta الرسالة بنجاح. تحقق من واتساب المستقبِل."

    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    code = err.get("code", "")
    msg = (err.get("message") or err.get("error_user_msg") or str(data))[:400]
    sub = (err.get("error_subcode") or "")
    hint = (err.get("error_user_title") or "")
    parts = [f"HTTP {r.status_code}"]
    if code:
        parts.append(f"code {code}")
    if sub:
        parts.append(f"subcode {sub}")
    detail = " — ".join(parts)
    extra = f" ({hint})" if hint else ""
    return False, f"رفض Meta الإرسال ({detail}): {msg}{extra}"


def send_whatsapp_image_link(
    phone_number_id: str,
    access_token: str,
    to_raw: str,
    *,
    image_https_url: str,
    caption: str | None = None,
    timeout: int = 30,
) -> Tuple[bool, str]:
    """
    إرسال صورة عبر Cloud API. الرابط يجب أن يكون HTTPS عاماً (ميتا تجرّب التحميل).
    """
    ok_norm, to_digits = normalize_whatsapp_recipient(to_raw)
    if not ok_norm:
        return False, "رقم غير صالح."

    pid = (phone_number_id or "").strip()
    token = (access_token or "").strip()
    link = (image_https_url or "").strip()
    if not pid or not token:
        return False, "ناقص Phone Number ID أو Access Token."
    if not link.startswith("https://"):
        return False, "رابط الصورة يجب أن يبدأ بـ https:// ليعمل مع ميتا."

    cap = (caption or "").strip()[:1024] if caption else ""

    url = _GRAPH_MESSAGES.format(phone_number_id=pid)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to_digits,
        "type": "image",
        "image": {"link": link},
    }
    if cap:
        payload["image"]["caption"] = cap

    try:
        import requests

        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except Exception as e:
        logger.exception("send_whatsapp_image_link: request failed")
        return False, f"تعذّر الاتصال بـ Meta: {e}"

    try:
        data = r.json()
    except Exception:
        return False, f"رد غير JSON من Meta (HTTP {r.status_code})."

    if r.ok and isinstance(data.get("messages"), list) and data["messages"]:
        mid = (data["messages"][0] or {}).get("id", "")
        if mid:
            return True, mid
        return True, "sent"

    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    msg = (err.get("message") or err.get("error_user_msg") or str(data))[:400]
    return False, f"رفض Meta الإرسال (HTTP {r.status_code}): {msg}"
