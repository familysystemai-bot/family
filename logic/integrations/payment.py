# -*- coding: utf-8 -*-
"""
payment — تكاملات بوابات الدفع السعودية والإقليمية.

البوابات المدعومة:
    - Moyasar    (الموصى به للسعودية — الأرخص)
    - Tap        (الأسرع للتفعيل)
    - HyperPay   (Enterprise)
    - PayTabs    (متعدد العملات)

الواجهة الموحّدة:
    create_charge(amount, currency, description, customer)
        → checkout_url (إعادة توجيه العميل للدفع)
    verify_charge(charge_id)        → status
    refund_charge(charge_id, amount) → refund
    test_connection(provider)       → فحص

تدفّق الدفع التقليدي (3D Secure):
    1. العميل يطلب → نُنشئ charge → نحصل على checkout_url
    2. نوجّه العميل للـ checkout_url
    3. العميل يدفع → البوابة تعيد توجيهه لـ callback_url مع charge_id
    4. عند رجوعه: verify_charge → نتأكد من status="paid" → نُكمل الطلب

إعدادات لوحة المؤسس:
    payment_provider             = "moyasar" | "tap" | "hyperpay" | "paytabs"
    payment_<provider>_secret_key
    payment_<provider>_publishable_key
    payment_<provider>_webhook_secret  (للتحقق من webhooks)
    payment_<provider>_callback_url    (URL في موقعنا للعودة بعد الدفع)
    payment_sandbox              = "true" | "false"

ملاحظة هامة (التحقق من الحساب البنكي):
    التحقق من الحساب البنكي وصاحبه = مسؤولية بوابة الدفع نفسها (KYC).
    عندما تفتح حساب لدى Moyasar/Tap، تطلب منك:
        - السجل التجاري
        - شهادة ضريبة القيمة المضافة
        - IBAN (تحقق منه عبر SAMA)
    بعد القبول، تأخذ الـ API keys من dashboard البوابة وتدخلها هنا.
    نحن لا نتحقق من البنوك مباشرة — نوجّه المؤسس للبوابة لإكمال KYC.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from logic.integrations.base import IntegrationResult, get_secret, read_setting

logger = logging.getLogger(__name__)


# ─── الواجهة العامة ──────────────────────────────────────────────────

def get_active_provider() -> str:
    val = read_setting("payment_provider", "").lower().strip()
    valid = ("moyasar", "tap", "hyperpay", "paytabs")
    return val if val in valid else ""


def is_provider_configured(provider: Optional[str] = None) -> bool:
    p = (provider or get_active_provider()).lower()
    if not p:
        return False
    return bool(get_secret("payment", f"{p}_secret_key"))


def is_provider_activated(provider: Optional[str] = None) -> bool:
    """
    هل الموفّر مُفعّل بالكامل (مفاتيح + تأكيد المؤسس على الحساب البنكي + KYC)؟

    تدفّق التفعيل:
        1. المؤسس يدخل المفاتيح في لوحة التحكم
        2. يضغط "Test Connection" → نتأكد المفاتيح صحيحة
        3. يدخل معلومات الحساب البنكي (IBAN, اسم الشركة، السجل التجاري)
        4. يؤكد أنه أكمل KYC في dashboard البوابة
        5. يضغط "Activate" → نضع payment_<provider>_activated = "true"
        6. الآن يمكن استقبال مدفوعات حقيقية

    قبل التفعيل، الـ checkout يعمل في sandbox فقط.
    """
    p = (provider or get_active_provider()).lower()
    if not is_provider_configured(p):
        return False
    return read_setting(f"payment_{p}_activated", "false").lower() == "true"


def is_sandbox_mode() -> bool:
    """هل النظام في وضع الاختبار؟"""
    return read_setting("payment_sandbox", "true").lower() == "true"


def list_supported_providers() -> List[Dict[str, Any]]:
    """قائمة بوابات الدفع للوحة المؤسس."""
    return [
        {
            "id": "moyasar",
            "name": "Moyasar",
            "description": "الموصى به — أرخص رسوم في السعودية، 2.2% + 1 ريال",
            "fees": "2.2% + 1 SAR (بطاقات) / 1.5% + 1 SAR (مدى)",
            "settlement": "مدى T+1 / بطاقات T+7",
            "configured": is_provider_configured("moyasar"),
            "activated": is_provider_activated("moyasar"),
            "signup_url": "https://moyasar.com/dashboard/register",
            "docs_url": "https://docs.moyasar.com/",
            "kyc_required": True,
            "fields": [
                {"key": "payment_moyasar_secret_key", "label": "Secret Key (sk_)", "type": "password",
                 "help": "من Moyasar Dashboard → Settings → API Keys"},
                {"key": "payment_moyasar_publishable_key", "label": "Publishable Key (pk_)", "type": "text"},
                {"key": "payment_moyasar_webhook_secret", "label": "Webhook Secret", "type": "password",
                 "optional": True},
                {"key": "payment_moyasar_callback_url", "label": "Callback URL",
                 "default": "https://your-domain.com/payment/callback/moyasar"},
            ],
        },
        {
            "id": "tap",
            "name": "Tap Payments",
            "description": "الأسرع للتفعيل، يدعم Knet والخليج",
            "fees": "2.9% + 1-2 SAR",
            "settlement": "T+3 إلى T+7",
            "configured": is_provider_configured("tap"),
            "activated": is_provider_activated("tap"),
            "signup_url": "https://www.tap.company/sa/en/get-started",
            "docs_url": "https://developers.tap.company/",
            "kyc_required": True,
            "fields": [
                {"key": "payment_tap_secret_key", "label": "Secret Key (sk_)", "type": "password"},
                {"key": "payment_tap_publishable_key", "label": "Publishable Key (pk_)", "type": "text"},
                {"key": "payment_tap_callback_url", "label": "Callback URL",
                 "default": "https://your-domain.com/payment/callback/tap"},
            ],
        },
        {
            "id": "hyperpay",
            "name": "HyperPay",
            "description": "Enterprise — 2.75% + 1 ريال، تسوية سريعة",
            "fees": "2.75% + 1 SAR",
            "settlement": "T+1 (مدى)",
            "configured": is_provider_configured("hyperpay"),
            "activated": is_provider_activated("hyperpay"),
            "signup_url": "https://www.hyperpay.com/",
            "kyc_required": True,
            "fields": [
                {"key": "payment_hyperpay_user_id", "label": "User ID", "type": "text"},
                {"key": "payment_hyperpay_password", "label": "Password", "type": "password"},
                {"key": "payment_hyperpay_entity_id", "label": "Entity ID", "type": "text"},
                {"key": "payment_hyperpay_secret_key", "label": "Access Token", "type": "password"},
                {"key": "payment_hyperpay_callback_url", "label": "Callback URL"},
            ],
        },
        {
            "id": "paytabs",
            "name": "PayTabs",
            "description": "متعدد العملات (160+) للشحن الدولي",
            "fees": "2.85% + 0.30 SAR",
            "settlement": "T+3 إلى T+7",
            "configured": is_provider_configured("paytabs"),
            "activated": is_provider_activated("paytabs"),
            "signup_url": "https://merchant.paytabs.com/signup/",
            "kyc_required": True,
            "fields": [
                {"key": "payment_paytabs_secret_key", "label": "Server Key", "type": "password"},
                {"key": "payment_paytabs_profile_id", "label": "Profile ID", "type": "text"},
                {"key": "payment_paytabs_region", "label": "Region", "type": "text", "default": "SAU"},
                {"key": "payment_paytabs_callback_url", "label": "Callback URL"},
            ],
        },
    ]


# ─── العمليات ────────────────────────────────────────────────────────

def create_charge(
    amount: float,
    currency: str = "SAR",
    *,
    description: str = "",
    customer: Optional[Dict[str, str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    provider: Optional[str] = None,
) -> IntegrationResult:
    """
    إنشاء عملية دفع.

    customer متوقّع:
        {"name", "email", "phone"}

    يُرجع:
        data = {
            "charge_id": "ch_xxx",       # معرّف العملية
            "checkout_url": "https://...",  # وجّه العميل لهذا
            "status": "initiated",
        }
    """
    p = (provider or get_active_provider()).lower()
    if not p:
        return IntegrationResult(success=False, error="No payment provider configured")
    if not is_provider_configured(p):
        return IntegrationResult(success=False, error=f"Provider {p} missing keys", provider=p)
    if amount <= 0:
        return IntegrationResult(success=False, error="Amount must be positive", provider=p)

    backend = _get_backend(p)
    if backend is None:
        return IntegrationResult(success=False, error=f"Unknown: {p}", provider=p)

    try:
        return backend.create_charge(amount, currency, description, customer or {}, metadata or {})
    except Exception as e:
        logger.exception("create_charge failed")
        return IntegrationResult(success=False, error=str(e), provider=p)


def verify_charge(charge_id: str, *, provider: Optional[str] = None) -> IntegrationResult:
    """التحقق من حالة الدفع (بعد رجوع العميل من البوابة)."""
    p = (provider or get_active_provider()).lower()
    backend = _get_backend(p)
    if backend is None:
        return IntegrationResult(success=False, error=f"Unknown: {p}")
    try:
        return backend.verify_charge(charge_id)
    except Exception as e:
        return IntegrationResult(success=False, error=str(e), provider=p)


def refund_charge(
    charge_id: str,
    amount: Optional[float] = None,
    *,
    provider: Optional[str] = None,
) -> IntegrationResult:
    """إرجاع مبلغ كامل أو جزئي."""
    p = (provider or get_active_provider()).lower()
    backend = _get_backend(p)
    if backend is None:
        return IntegrationResult(success=False, error=f"Unknown: {p}")
    try:
        return backend.refund_charge(charge_id, amount)
    except Exception as e:
        return IntegrationResult(success=False, error=str(e), provider=p)


def test_connection(provider: Optional[str] = None) -> Dict[str, Any]:
    p = (provider or get_active_provider()).lower()
    if not p:
        return {"success": False, "message": "No provider selected"}
    if not is_provider_configured(p):
        return {"success": False, "message": "Missing API keys"}
    backend = _get_backend(p)
    if backend is None:
        return {"success": False, "message": f"Unknown: {p}"}
    try:
        return backend.test_connection()
    except Exception as e:
        return {"success": False, "message": str(e)}


# ─── Backends ────────────────────────────────────────────────────────

def _get_backend(provider: str):
    if provider == "moyasar":
        return _MoyasarBackend()
    if provider == "tap":
        return _TapBackend()
    if provider == "hyperpay":
        return _HyperPayBackend()
    if provider == "paytabs":
        return _PayTabsBackend()
    return None


# ─── Moyasar (نموذج كامل) ───────────────────────────────────────────

class _MoyasarBackend:
    """
    Moyasar API integration.

    Docs: https://docs.moyasar.com/
    Base: https://api.moyasar.com/v1

    Auth: HTTP Basic Auth — username = secret_key, password = empty
    """

    BASE_URL = "https://api.moyasar.com/v1"

    def _secret_key(self) -> str:
        return get_secret("payment", "moyasar_secret_key")

    def _callback_url(self) -> str:
        return get_secret("payment", "moyasar_callback_url",
                          "https://example.com/payment/callback/moyasar")

    def _auth(self):
        return (self._secret_key(), "")

    def create_charge(
        self, amount: float, currency: str, description: str,
        customer: Dict[str, str], metadata: Dict[str, Any],
    ) -> IntegrationResult:
        try:
            import requests
        except ImportError:
            return IntegrationResult(success=False, error="requests required", provider="moyasar")

        # Moyasar يستخدم halalas (1 SAR = 100 halala)
        amount_halalas = int(round(amount * 100))

        payload = {
            "amount": amount_halalas,
            "currency": currency.upper(),
            "description": (description or "Family Complex order")[:255],
            "callback_url": self._callback_url(),
            "source": {
                "type": "creditcard",  # سيُختار من checkout page
            },
            "metadata": metadata,
        }
        if customer.get("email"):
            payload["metadata"]["customer_email"] = customer["email"]
        if customer.get("name"):
            payload["metadata"]["customer_name"] = customer["name"]

        try:
            r = requests.post(
                f"{self.BASE_URL}/invoices",  # نستخدم invoices للـ hosted checkout
                json={
                    "amount": amount_halalas,
                    "currency": currency.upper(),
                    "description": (description or "Family Complex")[:255],
                    "callback_url": self._callback_url(),
                    "metadata": payload["metadata"],
                },
                auth=self._auth(),
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            return IntegrationResult(
                success=True,
                data={
                    "charge_id": data.get("id"),
                    "checkout_url": data.get("url"),
                    "status": data.get("status", "initiated"),
                    "amount": amount,
                    "currency": currency,
                },
                provider="moyasar",
                raw_response=data,
            )
        except Exception as e:
            logger.exception("Moyasar create_charge failed")
            return IntegrationResult(success=False, error=str(e), provider="moyasar")

    def verify_charge(self, charge_id: str) -> IntegrationResult:
        try:
            import requests
        except ImportError:
            return IntegrationResult(success=False, error="requests required", provider="moyasar")
        try:
            r = requests.get(
                f"{self.BASE_URL}/invoices/{charge_id}",
                auth=self._auth(),
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            return IntegrationResult(
                success=True,
                data={
                    "charge_id": data.get("id"),
                    "status": data.get("status"),  # initiated/paid/failed/canceled
                    "amount": (data.get("amount", 0) or 0) / 100,
                    "currency": data.get("currency"),
                    "paid": data.get("status") == "paid",
                },
                provider="moyasar",
                raw_response=data,
            )
        except Exception as e:
            return IntegrationResult(success=False, error=str(e), provider="moyasar")

    def refund_charge(self, charge_id: str, amount: Optional[float]) -> IntegrationResult:
        try:
            import requests
        except ImportError:
            return IntegrationResult(success=False, error="requests required", provider="moyasar")
        payload = {}
        if amount:
            payload["amount"] = int(round(amount * 100))
        try:
            r = requests.post(
                f"{self.BASE_URL}/payments/{charge_id}/refund",
                json=payload, auth=self._auth(), timeout=15,
            )
            r.raise_for_status()
            return IntegrationResult(success=True, data=r.json(), provider="moyasar")
        except Exception as e:
            return IntegrationResult(success=False, error=str(e), provider="moyasar")

    def test_connection(self) -> Dict[str, Any]:
        try:
            import requests
        except ImportError:
            return {"success": False, "message": "requests library required"}
        try:
            # نفحص بـ list invoices (limit=1)
            r = requests.get(
                f"{self.BASE_URL}/invoices",
                params={"per": 1},
                auth=self._auth(),
                timeout=10,
            )
            if r.status_code == 200:
                return {"success": True, "message": "Connected to Moyasar"}
            if r.status_code == 401:
                return {"success": False, "message": "Invalid secret key"}
            return {"success": False, "message": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"success": False, "message": str(e)}


# ─── Tap / HyperPay / PayTabs: skeleton ──────────────────────────────

class _TapBackend:
    """Tap Payments API. https://developers.tap.company/"""

    def create_charge(self, amount, currency, description, customer, metadata):
        return IntegrationResult(
            success=False,
            error="Tap integration scaffold ready. Configure account at tap.company to activate.",
            provider="tap",
        )

    def verify_charge(self, charge_id):
        return IntegrationResult(success=False, error="Tap not yet activated", provider="tap")

    def refund_charge(self, charge_id, amount):
        return IntegrationResult(success=False, error="Tap not yet activated", provider="tap")

    def test_connection(self):
        if get_secret("payment", "tap_secret_key"):
            return {"success": True, "message": "Tap credentials set (full integration pending)"}
        return {"success": False, "message": "Tap credentials missing"}


class _HyperPayBackend:
    """HyperPay COPYandPAY API."""

    def create_charge(self, amount, currency, description, customer, metadata):
        return IntegrationResult(
            success=False,
            error="HyperPay integration scaffold ready. Configure account to activate.",
            provider="hyperpay",
        )

    def verify_charge(self, charge_id):
        return IntegrationResult(success=False, error="HyperPay not yet activated", provider="hyperpay")

    def refund_charge(self, charge_id, amount):
        return IntegrationResult(success=False, error="HyperPay not yet activated", provider="hyperpay")

    def test_connection(self):
        if get_secret("payment", "hyperpay_secret_key"):
            return {"success": True, "message": "HyperPay credentials set (full integration pending)"}
        return {"success": False, "message": "HyperPay credentials missing"}


class _PayTabsBackend:
    """PayTabs PayPage API."""

    def create_charge(self, amount, currency, description, customer, metadata):
        return IntegrationResult(
            success=False,
            error="PayTabs integration scaffold ready. Configure account to activate.",
            provider="paytabs",
        )

    def verify_charge(self, charge_id):
        return IntegrationResult(success=False, error="PayTabs not yet activated", provider="paytabs")

    def refund_charge(self, charge_id, amount):
        return IntegrationResult(success=False, error="PayTabs not yet activated", provider="paytabs")

    def test_connection(self):
        if get_secret("payment", "paytabs_secret_key"):
            return {"success": True, "message": "PayTabs credentials set (full integration pending)"}
        return {"success": False, "message": "PayTabs credentials missing"}


# ─── إدارة معلومات الحساب البنكي (للوحة المؤسس) ────────────────────

def save_business_info(info: Dict[str, str]) -> bool:
    """
    يحفظ معلومات النشاط التجاري والحساب البنكي.

    info متوقّع:
        {
            "business_name": "...",
            "commercial_register": "...",
            "vat_number": "...",
            "iban": "SAxxx...",
            "bank_name": "...",
            "account_holder_name": "...",
            "contact_email": "...",
            "contact_phone": "..."
        }

    ملاحظة: الـ KYC الفعلي يتم في dashboard بوابة الدفع.
    نحن نخزّن المعلومات لعرضها للمؤسس وللتذكير فقط.
    """
    from logic.integrations.base import write_setting
    fields = (
        "business_name", "commercial_register", "vat_number",
        "iban", "bank_name", "account_holder_name",
        "contact_email", "contact_phone",
    )
    for f in fields:
        if f in info:
            write_setting(f"business_info_{f}", str(info[f]))
    return True


def get_business_info() -> Dict[str, str]:
    """يقرأ معلومات النشاط التجاري المحفوظة."""
    fields = (
        "business_name", "commercial_register", "vat_number",
        "iban", "bank_name", "account_holder_name",
        "contact_email", "contact_phone",
    )
    return {f: read_setting(f"business_info_{f}", "") for f in fields}