# -*- coding: utf-8 -*-
"""
سكربت اختياري لإرسال رسالة واتساب عبر Cloud API.
لا تضع مفاتيحاً في الكود — استخدم متغيرات البيئة (أو ملف `.env` محلياً فقط).

المطلوب:
  WA_ACCESS_TOKEN   أو WHATSAPP_TOKEN
  WA_PHONE_NUMBER_ID
اختياري:
  WA_TEST_RECIPIENT  رقم المستلم بصيغة E.164 بدون +
"""
from __future__ import annotations

import json
import os
import sys

import requests


def send_whatsapp_message() -> None:
    if os.getenv("RENDER") is None:
        try:
            from dotenv import load_dotenv

            from pathlib import Path

            load_dotenv(Path(__file__).resolve().parent / ".env")
        except ImportError:
            pass

    access_token = (os.getenv("WA_ACCESS_TOKEN") or os.getenv("WHATSAPP_TOKEN") or "").strip()
    phone_number_id = (os.getenv("WA_PHONE_NUMBER_ID") or "").strip()
    recipient_phone = (os.getenv("WA_TEST_RECIPIENT") or "").strip().lstrip("+")

    if not access_token or not phone_number_id:
        print(
            "خطأ: عيّن WA_ACCESS_TOKEN (أو WHATSAPP_TOKEN) و WA_PHONE_NUMBER_ID في البيئة أو `.env`.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not recipient_phone:
        print(
            "خطأ: عيّن WA_TEST_RECIPIENT (رقم بدون +).",
            file=sys.stderr,
        )
        sys.exit(2)

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": recipient_phone,
        "type": "template",
        "template": {"name": "hello_world", "language": {"code": "en_US"}},
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(data), timeout=60)
        result = response.json()
        if response.status_code == 200:
            print("✅ تم إرسال الرسالة بنجاح!")
            mid = (result.get("messages") or [{}])[0].get("id")
            if mid:
                print(f"ID الرسالة: {mid}")
        else:
            print(f"❌ فشل الإرسال. كود: {response.status_code}")
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"حدث خطأ أثناء الاتصال: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    send_whatsapp_message()
