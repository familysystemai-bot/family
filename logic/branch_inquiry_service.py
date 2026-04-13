# -*- coding: utf-8 -*-
"""
خدمة استفسارات الفروع — المنطق التجاري.

الهدف: عندما يطلب العميل منتجاً لا يوجد في قاعدة البيانات،
لكن القسم موجود، نُنشئ استفساراً للفرع ونُرسل إشعاراً بالبريد.

تدفق العمل:
1. النظام يكتشف: منتج غير موجود + القسم موجود عندنا
2. نُنشئ سجل في branch_inquiries
3. نُرسل بريداً للفرع (إن كان معلوم)
4. العميل يتلقى رسالة: "عندنا القسم بس راح نسأل الفرع"
5. الفرع يرى الاستفسار في لوحة التحكم ويرد بنص + سعر + صورة
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def create_product_inquiry(
    db,
    session_id: str,
    inquiry_text: str,
    category_hint: str = "",
    branch_name: str = "",
    customer_name: str = "",
    customer_contact: str = "",
    customer_image_path: str = "",
    send_email: bool = True,
) -> Optional[int]:
    """
    ينشئ استفسار عن منتج غير موجود في DB ويُرسل بريداً للفرع.
    يعيد ID الاستفسار أو None عند الفشل.
    """
    try:
        inquiry_id = db.create_branch_inquiry(
            session_id=session_id,
            inquiry_text=inquiry_text,
            customer_name=customer_name,
            customer_contact=customer_contact,
            branch_name=branch_name,
            category_hint=category_hint,
            customer_image_path=customer_image_path,
        )

        if inquiry_id and send_email:
            _send_inquiry_email(
                db=db,
                inquiry_id=inquiry_id,
                inquiry_text=inquiry_text,
                category_hint=category_hint,
                branch_name=branch_name,
                customer_name=customer_name,
                customer_image_path=customer_image_path,
            )

        return inquiry_id

    except Exception:
        logger.exception("branch_inquiry_service: create_product_inquiry failed")
        return None


def _send_inquiry_email(
    db,
    inquiry_id: int,
    inquiry_text: str,
    category_hint: str,
    branch_name: str,
    customer_name: str,
    customer_image_path: str = "",
) -> None:
    """يُرسل بريداً للفرع يُخبره بالاستفسار الجديد."""
    try:
        from logic.mail_service import send_email

        # جلب بريد الفرع إن وُجد
        recipients = _get_branch_email(db, branch_name)
        if not recipients:
            from config import MAIN_RECEIVER_EMAIL
            recipients = MAIN_RECEIVER_EMAIL

        subject = f"📩 استفسار جديد من عميل — رقم #{inquiry_id}"

        image_note = ""
        if customer_image_path:
            image_note = "\n🖼️ العميل أرسل صورة — يمكنك مشاهدتها في لوحة التحكم."

        body = (
            f"استفسار جديد يحتاج ردّك يا فريق {branch_name or 'الفرع'}:\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 رقم الاستفسار: #{inquiry_id}\n"
            f"👤 اسم العميل: {customer_name or 'غير محدد'}\n"
            f"📦 الطلب: {inquiry_text}\n"
            f"🗂️ القسم المتوقع: {category_hint or 'غير محدد'}\n"
            f"{image_note}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"الرجاء الدخول على لوحة التحكم والرد على الاستفسار.\n"
            f"العميل ينتظر ردّك — جزاك الله خيراً 🙏"
        )

        send_email(recipients, subject, body)
        logger.debug("branch_inquiry: email sent for inquiry #%s", inquiry_id)

    except Exception:
        logger.warning("branch_inquiry: failed to send email for inquiry #%s", inquiry_id)


def _get_branch_email(db, branch_name: str) -> Optional[str]:
    """يجلب بريد الفرع من قاعدة البيانات."""
    if not branch_name:
        return None
    try:
        branches = db.get_all_branches() or []
        for b in branches:
            name = (b.get("city_name") or "").strip()
            if name and name in branch_name or branch_name in name:
                email = (b.get("email") or "").strip()
                if email:
                    return email
        return None
    except Exception:
        return None


def build_inquiry_response_for_customer(
    inquiry_id: int,
    category_name: str,
    product_query: str,
    branch_name: str = "",
    dialect: str = "default",
    customer_name: str = "",
) -> dict:
    """
    يبني استجابة JSON للعميل تُخبره أن الاستفسار أُرسل للفرع.
    """
    from logic.category_classifier import get_category_response_message

    name_part = f" يا {customer_name}" if customer_name else ""
    msg = get_category_response_message(
        product_query=product_query,
        category_name=category_name,
        branch_name=branch_name,
        dialect=dialect,
    )

    return {
        "products": [],
        "message": f"{name_part}{msg}".strip(),
        "intent": "branch_inquiry",
        "inquiry_id": inquiry_id,
    }


def get_inquiry_reply_message(
    inquiry: dict,
    dialect: str = "default",
) -> dict:
    """
    يبني استجابة JSON برد الفرع ليُرسل للعميل.
    """
    branch_reply = (inquiry.get("branch_reply") or "").strip()
    branch_price = (inquiry.get("branch_price") or "").strip()
    branch_image = (inquiry.get("branch_image_path") or "").strip()

    price_part = f"\n💰 السعر: {branch_price}" if branch_price else ""

    templates = {
        "masri": f"الفرع رد عليك:\n\n{branch_reply}{price_part}",
        "jordani": f"رد الفرع:\n\n{branch_reply}{price_part}",
        "default": f"رد الفرع:\n\n{branch_reply}{price_part}",
    }
    msg = templates.get(dialect) or templates["default"]

    products = []
    if branch_image:
        from logic.product_service import _chat_image_url  # type: ignore
        try:
            img_url = _chat_image_url(branch_image)
            if img_url:
                products = [{"image": img_url, "name": "من الفرع", "price": branch_price}]
        except Exception:
            pass

    return {
        "products": products,
        "message": msg,
        "intent": "branch_inquiry_reply",
    }
