# -*- coding: utf-8 -*-
"""
invoicing — نظام إصدار وإرسال الفواتير (نمط Amazon).

الميزات:
    1. إنشاء فاتورة PDF تلقائياً عند اكتمال الطلب
    2. إرسالها للعميل عبر البريد الإلكتروني (SMTP أو Amazon SES)
    3. حفظ نسخة في قاعدة البيانات
    4. تكامل اختياري مع Zoho Books / QuickBooks
    5. توافق مع متطلبات الفاتورة الإلكترونية في السعودية (ZATCA)

الواجهة:
    create_invoice(order_data) → invoice_id, pdf_url
    send_invoice_email(invoice_id, email) → success
    get_invoice_pdf(invoice_id) → bytes

ZATCA E-Invoicing (مطلوب في السعودية):
    الفاتورة يجب أن تحتوي على:
        - QR code فيه (Seller name, VAT, timestamp, total, VAT amount)
        - رقم تسلسلي
        - VAT 15%
        - بيانات البائع والمشتري الكاملة

الإعدادات:
    invoicing_provider           = "internal" | "zoho" | "quickbooks"
    invoicing_email_provider     = "smtp" | "ses" | "sendgrid"
    invoicing_smtp_host          = ...
    invoicing_smtp_port          = 587
    invoicing_smtp_username      = ...
    invoicing_smtp_password      = ...
    invoicing_smtp_from_email    = invoices@yourdomain.com
    invoicing_smtp_from_name     = "Family Complex"
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from logic.integrations.base import IntegrationResult, get_secret, read_setting

logger = logging.getLogger(__name__)


# ─── إنشاء الفاتورة ──────────────────────────────────────────────────

def create_invoice(order_data: Dict[str, Any]) -> IntegrationResult:
    """
    ينشئ فاتورة جديدة من بيانات طلب.

    order_data المتوقّع:
        {
            "order_id": "ORD-12345",
            "customer": {"name", "email", "phone", "address", "vat_number" (اختياري)},
            "items": [
                {"name", "quantity", "unit_price", "total"}
            ],
            "subtotal": 100.00,
            "vat_rate": 0.15,         # 15% ضريبة قيمة مضافة
            "vat_amount": 15.00,
            "shipping": 20.00,
            "discount": 0.00,
            "total": 135.00,
            "currency": "SAR",
        }

    يُرجع: data = {invoice_id, invoice_number, pdf_bytes, qr_code}
    """
    try:
        invoice_number = _generate_invoice_number()
        invoice_id = secrets.token_hex(8)
        timestamp = datetime.utcnow()

        # حفظ الفاتورة في DB
        _save_invoice_to_db({
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "order_id": order_data.get("order_id", ""),
            "customer_email": order_data.get("customer", {}).get("email", ""),
            "customer_name": order_data.get("customer", {}).get("name", ""),
            "subtotal": order_data.get("subtotal", 0),
            "vat_amount": order_data.get("vat_amount", 0),
            "total": order_data.get("total", 0),
            "currency": order_data.get("currency", "SAR"),
            "items_json": json.dumps(order_data.get("items", []), ensure_ascii=False),
            "created_at": timestamp.isoformat(),
            "status": "issued",
        })

        # توليد QR code (ZATCA-compliant)
        qr_code = _build_zatca_qr_code(
            seller_name=read_setting("business_info_business_name", "Family Complex"),
            vat_number=read_setting("business_info_vat_number", ""),
            timestamp=timestamp,
            total=order_data.get("total", 0),
            vat_amount=order_data.get("vat_amount", 0),
        )

        # توليد PDF
        pdf_bytes = _generate_invoice_pdf(
            invoice_number=invoice_number,
            order_data=order_data,
            qr_code_base64=qr_code,
            timestamp=timestamp,
        )

        return IntegrationResult(
            success=True,
            data={
                "invoice_id": invoice_id,
                "invoice_number": invoice_number,
                "pdf_bytes": pdf_bytes,
                "qr_code": qr_code,
            },
            provider="internal",
        )
    except Exception as e:
        logger.exception("create_invoice failed")
        return IntegrationResult(success=False, error=str(e), provider="internal")


def get_invoice(invoice_id: str) -> Optional[Dict[str, Any]]:
    """يجلب فاتورة محفوظة."""
    try:
        from logic.db_adapter import DBAdapter
        db = DBAdapter()
        return db.fetch_one(
            "SELECT * FROM invoices WHERE invoice_id = %s",
            (invoice_id,),
        )
    except Exception:
        logger.exception("get_invoice failed")
        return None


def list_invoices(
    customer_email: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """يجلب قائمة الفواتير (للوحة الإدارة)."""
    try:
        from logic.db_adapter import DBAdapter
        db = DBAdapter()
        if customer_email:
            return db.fetch_all(
                "SELECT * FROM invoices WHERE customer_email = %s ORDER BY id DESC LIMIT %s",
                (customer_email, limit),
            )
        return db.fetch_all(
            "SELECT * FROM invoices ORDER BY id DESC LIMIT %s",
            (limit,),
        )
    except Exception:
        logger.exception("list_invoices failed")
        return []


# ─── إرسال البريد ────────────────────────────────────────────────────

def send_invoice_email(
    invoice_id: str,
    pdf_bytes: bytes,
    invoice_number: str,
    recipient_email: str,
    recipient_name: str = "",
    *,
    custom_message: str = "",
) -> IntegrationResult:
    """يرسل الفاتورة كـ PDF عبر البريد."""
    provider = read_setting("invoicing_email_provider", "smtp").lower()

    if provider == "smtp":
        return _send_via_smtp(
            invoice_id, pdf_bytes, invoice_number,
            recipient_email, recipient_name, custom_message,
        )
    if provider == "ses":
        return _send_via_ses(
            invoice_id, pdf_bytes, invoice_number,
            recipient_email, recipient_name, custom_message,
        )
    return IntegrationResult(
        success=False,
        error=f"Email provider '{provider}' not implemented yet",
    )


def _send_via_smtp(
    invoice_id: str, pdf_bytes: bytes, invoice_number: str,
    recipient_email: str, recipient_name: str, custom_message: str,
) -> IntegrationResult:
    """يرسل عبر SMTP عادي (Gmail / Office365 / أي SMTP)."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication

    host = get_secret("invoicing", "smtp_host")
    port = int(get_secret("invoicing", "smtp_port", "587") or "587")
    username = get_secret("invoicing", "smtp_username")
    password = get_secret("invoicing", "smtp_password")
    from_email = get_secret("invoicing", "smtp_from_email", username)
    from_name = get_secret("invoicing", "smtp_from_name", "Family Complex")

    if not (host and username and password and from_email):
        return IntegrationResult(
            success=False,
            error="SMTP credentials missing — configure in founder panel",
        )

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = recipient_email
    msg["Subject"] = f"فاتورتك من مجمع العائلة — رقم {invoice_number}"

    body = custom_message or (
        f"عزيزنا {recipient_name or 'العميل'},\n\n"
        f"شكراً لتسوقك من مجمع العائلة. "
        f"مرفق فاتورة طلبك رقم {invoice_number}.\n\n"
        f"للأسئلة، تواصل مع خدمة العملاء.\n\n"
        f"مع تحيات،\nمجمع العائلة"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    pdf = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf.add_header(
        "Content-Disposition", "attachment",
        filename=f"invoice-{invoice_number}.pdf",
    )
    msg.attach(pdf)

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
        return IntegrationResult(
            success=True,
            data={"invoice_id": invoice_id, "recipient": recipient_email},
            provider="smtp",
        )
    except Exception as e:
        logger.exception("SMTP send failed")
        return IntegrationResult(success=False, error=str(e), provider="smtp")


def _send_via_ses(
    invoice_id: str, pdf_bytes: bytes, invoice_number: str,
    recipient_email: str, recipient_name: str, custom_message: str,
) -> IntegrationResult:
    """يرسل عبر AWS SES (موصى به للحجوم الكبيرة — أرخص من SMTP)."""
    try:
        import boto3
    except ImportError:
        return IntegrationResult(success=False, error="boto3 required for SES")

    access_key = get_secret("invoicing", "ses_access_key")
    secret_key = get_secret("invoicing", "ses_secret_key")
    region = get_secret("invoicing", "ses_region", "us-east-1")
    from_email = get_secret("invoicing", "ses_from_email")

    if not (access_key and secret_key and from_email):
        return IntegrationResult(success=False, error="SES credentials missing")

    try:
        ses = boto3.client(
            "ses",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        # SES Raw Email لإرسال attachments
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication

        msg = MIMEMultipart()
        msg["From"] = from_email
        msg["To"] = recipient_email
        msg["Subject"] = f"فاتورتك من مجمع العائلة — رقم {invoice_number}"

        body = custom_message or f"مرفق فاتورتك رقم {invoice_number}"
        msg.attach(MIMEText(body, "plain", "utf-8"))

        pdf = MIMEApplication(pdf_bytes, _subtype="pdf")
        pdf.add_header("Content-Disposition", "attachment", filename=f"invoice-{invoice_number}.pdf")
        msg.attach(pdf)

        ses.send_raw_email(
            Source=from_email,
            Destinations=[recipient_email],
            RawMessage={"Data": msg.as_string()},
        )
        return IntegrationResult(
            success=True,
            data={"invoice_id": invoice_id, "recipient": recipient_email},
            provider="ses",
        )
    except Exception as e:
        logger.exception("SES send failed")
        return IntegrationResult(success=False, error=str(e), provider="ses")


def test_email_connection(provider: Optional[str] = None) -> Dict[str, Any]:
    """اختبار الاتصال بمزود البريد."""
    p = (provider or read_setting("invoicing_email_provider", "smtp")).lower()

    if p == "smtp":
        import smtplib
        host = get_secret("invoicing", "smtp_host")
        port = int(get_secret("invoicing", "smtp_port", "587") or "587")
        username = get_secret("invoicing", "smtp_username")
        password = get_secret("invoicing", "smtp_password")
        if not (host and username and password):
            return {"success": False, "message": "SMTP credentials missing"}
        try:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(username, password)
            return {"success": True, "message": f"Connected to SMTP {host}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    if p == "ses":
        try:
            import boto3
            ses = boto3.client(
                "ses",
                aws_access_key_id=get_secret("invoicing", "ses_access_key"),
                aws_secret_access_key=get_secret("invoicing", "ses_secret_key"),
                region_name=get_secret("invoicing", "ses_region", "us-east-1"),
            )
            quota = ses.get_send_quota()
            return {
                "success": True,
                "message": f"Connected to SES (24h quota: {int(quota['Max24HourSend'])})",
            }
        except Exception as e:
            return {"success": False, "message": str(e)}

    return {"success": False, "message": f"Unknown provider: {p}"}


# ─── المساعدات الداخلية ─────────────────────────────────────────────

def _generate_invoice_number() -> str:
    """يولّد رقم فاتورة فريد. الصيغة: INV-YYYYMM-XXXXXX"""
    now = datetime.utcnow()
    suffix = secrets.token_hex(3).upper()
    return f"INV-{now.strftime('%Y%m')}-{suffix}"


def _build_zatca_qr_code(
    seller_name: str, vat_number: str, timestamp: datetime,
    total: float, vat_amount: float,
) -> str:
    """
    يبني QR code بصيغة ZATCA (TLV encoding).

    https://zatca.gov.sa/en/E-Invoicing/Pages/Phase1.aspx

    Tags:
        1. Seller name
        2. VAT registration number
        3. Timestamp (ISO 8601)
        4. Total (with VAT)
        5. VAT amount
    """
    def tlv(tag: int, value: str) -> bytes:
        v = value.encode("utf-8")
        return bytes([tag, len(v)]) + v

    iso_ts = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    tlv_data = (
        tlv(1, seller_name)
        + tlv(2, vat_number)
        + tlv(3, iso_ts)
        + tlv(4, f"{total:.2f}")
        + tlv(5, f"{vat_amount:.2f}")
    )
    return base64.b64encode(tlv_data).decode("ascii")


def _generate_invoice_pdf(
    invoice_number: str,
    order_data: Dict[str, Any],
    qr_code_base64: str,
    timestamp: datetime,
) -> bytes:
    """
    يولّد فاتورة PDF.

    يستخدم reportlab إن كان متاحاً، وإلا يولّد PDF نصي بسيط.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
        )
        from reportlab.lib import colors
    except ImportError:
        # PDF احتياطي بسيط بدون reportlab
        return _generate_simple_pdf(invoice_number, order_data, timestamp)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    # Header
    story.append(Paragraph(
        f"<b>Invoice #{invoice_number}</b>",
        styles["Title"],
    ))
    story.append(Paragraph(
        f"Date: {timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 6 * mm))

    # Seller info
    seller_name = read_setting("business_info_business_name", "Family Complex")
    seller_vat = read_setting("business_info_vat_number", "")
    seller_cr = read_setting("business_info_commercial_register", "")
    story.append(Paragraph(f"<b>From:</b> {seller_name}", styles["Normal"]))
    if seller_vat:
        story.append(Paragraph(f"VAT: {seller_vat}", styles["Normal"]))
    if seller_cr:
        story.append(Paragraph(f"CR: {seller_cr}", styles["Normal"]))
    story.append(Spacer(1, 4 * mm))

    # Customer info
    customer = order_data.get("customer", {})
    story.append(Paragraph(
        f"<b>To:</b> {customer.get('name', '')}",
        styles["Normal"],
    ))
    if customer.get("email"):
        story.append(Paragraph(f"Email: {customer['email']}", styles["Normal"]))
    if customer.get("phone"):
        story.append(Paragraph(f"Phone: {customer['phone']}", styles["Normal"]))
    story.append(Spacer(1, 6 * mm))

    # Items table
    items = order_data.get("items", [])
    table_data = [["#", "Item", "Qty", "Unit", "Total"]]
    for i, item in enumerate(items, 1):
        table_data.append([
            str(i),
            str(item.get("name", ""))[:40],
            str(item.get("quantity", 1)),
            f"{float(item.get('unit_price', 0)):.2f}",
            f"{float(item.get('total', 0)):.2f}",
        ])
    table = Table(table_data, colWidths=[15*mm, 70*mm, 15*mm, 25*mm, 25*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("ALIGN", (-3, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(table)
    story.append(Spacer(1, 6 * mm))

    # Totals
    currency = order_data.get("currency", "SAR")
    story.append(Paragraph(
        f"Subtotal: {order_data.get('subtotal', 0):.2f} {currency}",
        styles["Normal"],
    ))
    story.append(Paragraph(
        f"VAT (15%): {order_data.get('vat_amount', 0):.2f} {currency}",
        styles["Normal"],
    ))
    if order_data.get("shipping"):
        story.append(Paragraph(
            f"Shipping: {order_data.get('shipping', 0):.2f} {currency}",
            styles["Normal"],
        ))
    story.append(Paragraph(
        f"<b>Total: {order_data.get('total', 0):.2f} {currency}</b>",
        styles["Heading3"],
    ))
    story.append(Spacer(1, 8 * mm))

    # QR Code (ZATCA compliance)
    try:
        import qrcode
        qr = qrcode.QRCode(version=None, box_size=4, border=2)
        qr.add_data(qr_code_base64)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        qr_buf = io.BytesIO()
        img.save(qr_buf, format="PNG")
        qr_buf.seek(0)
        story.append(Image(qr_buf, width=30*mm, height=30*mm))
        story.append(Paragraph(
            "<font size=8>ZATCA QR (مسحه للتحقق من الفاتورة)</font>",
            styles["Normal"],
        ))
    except ImportError:
        story.append(Paragraph(
            f"<font size=7>ZATCA: {qr_code_base64[:60]}...</font>",
            styles["Normal"],
        ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def _generate_simple_pdf(
    invoice_number: str, order_data: Dict[str, Any], timestamp: datetime,
) -> bytes:
    """PDF احتياطي بسيط جداً بدون reportlab."""
    text_content = f"""INVOICE {invoice_number}
Date: {timestamp.strftime('%Y-%m-%d')}

Customer: {order_data.get('customer', {}).get('name', '')}
Total: {order_data.get('total', 0):.2f} {order_data.get('currency', 'SAR')}

(Install reportlab for proper PDF formatting)
"""
    # PDF بدائي جداً — يقترح المؤسس تثبيت reportlab
    return text_content.encode("utf-8")


def _save_invoice_to_db(invoice_data: Dict[str, Any]) -> bool:
    """يحفظ الفاتورة في جدول invoices."""
    try:
        from logic.db_adapter import DBAdapter
        db = DBAdapter()
        # نحاول إنشاء الجدول لو لم يكن موجوداً
        _ensure_invoices_table(db)
        db.execute(
            """
            INSERT INTO invoices (
                invoice_id, invoice_number, order_id,
                customer_email, customer_name,
                subtotal, vat_amount, total, currency,
                items_json, status, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                invoice_data["invoice_id"],
                invoice_data["invoice_number"],
                invoice_data.get("order_id", ""),
                invoice_data.get("customer_email", ""),
                invoice_data.get("customer_name", ""),
                invoice_data.get("subtotal", 0),
                invoice_data.get("vat_amount", 0),
                invoice_data.get("total", 0),
                invoice_data.get("currency", "SAR"),
                invoice_data.get("items_json", "[]"),
                invoice_data.get("status", "issued"),
                invoice_data.get("created_at", datetime.utcnow().isoformat()),
            ),
        )
        return True
    except Exception:
        logger.exception("save invoice to db failed")
        return False


def _ensure_invoices_table(db) -> None:
    """ينشئ جدول invoices إن لم يكن موجوداً."""
    from logic.sql_translator import translate_ddl
    ddl = """
    CREATE TABLE IF NOT EXISTS invoices (
        id SERIAL PRIMARY KEY,
        invoice_id TEXT UNIQUE NOT NULL,
        invoice_number TEXT NOT NULL,
        order_id TEXT,
        customer_email TEXT,
        customer_name TEXT,
        subtotal REAL DEFAULT 0,
        vat_amount REAL DEFAULT 0,
        total REAL DEFAULT 0,
        currency TEXT DEFAULT 'SAR',
        items_json TEXT,
        status TEXT DEFAULT 'issued',
        created_at TEXT
    )
    """
    try:
        db.execute(translate_ddl(ddl, db.db_type))
    except Exception:
        pass  # الجدول موجود بالفعل