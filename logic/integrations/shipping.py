# -*- coding: utf-8 -*-
"""
shipping — تكاملات شركات الشحن في السعودية والمنطقة.

الشركات المدعومة:
    - SMSA Express   (محلي سعودي)
    - Aramex         (إقليمي)
    - DHL Express    (دولي)
    - Naqel Express  (محلي)

الواجهة الموحّدة:
    create_shipment(order_data)  → AWB number + label URL
    track_shipment(awb)          → status updates
    cancel_shipment(awb)         → cancellation
    calculate_rate(origin, dest, weight) → estimated cost
    test_connection(provider)    → فحص الاتصال

ملاحظة هامة:
    - SMSA و Aramex و DHL يتطلبون حساب تجاري ومعرّف
    - الـ APIs الفعلية تختلف بين staging و production
    - هذا الملف يوفّر الـ skeleton — الـ endpoints تحتاج تعبئة بيانات
      الإنتاج بعد فتح حساب لدى كل شركة

الإعدادات في system_settings:
    shipping_provider              = "smsa" | "aramex" | "dhl" | "naqel"
    shipping_<provider>_api_key    = ...
    shipping_<provider>_account_no = ...
    shipping_<provider>_password   = ...  (للشركات اللي تستخدم Basic Auth)
    shipping_<provider>_sandbox    = "true" | "false"
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from logic.integrations.base import IntegrationResult, get_secret, read_setting

logger = logging.getLogger(__name__)


# ─── الواجهة العامة ──────────────────────────────────────────────────

def get_active_provider() -> str:
    """شركة الشحن المختارة من لوحة المؤسس."""
    val = read_setting("shipping_provider", "").lower().strip()
    valid = ("smsa", "aramex", "dhl", "naqel")
    return val if val in valid else ""


def is_provider_configured(provider: Optional[str] = None) -> bool:
    """هل الشركة مهيّأة (مفاتيح موجودة)؟"""
    p = (provider or get_active_provider()).lower()
    if not p:
        return False
    api_key = get_secret("shipping", f"{p}_api_key")
    account = get_secret("shipping", f"{p}_account_no")
    return bool(api_key and account)


def list_supported_providers() -> List[Dict[str, Any]]:
    """قائمة شركات الشحن المدعومة (للوحة المؤسس)."""
    return [
        {
            "id": "smsa",
            "name": "SMSA Express",
            "description": "أكبر شركة شحن محلية في السعودية، مناسب لطلبات داخل المملكة",
            "configured": is_provider_configured("smsa"),
            "coverage": "السعودية، الخليج، 200+ دولة",
            "signup_url": "https://smsaexpress.com/business",
            "fields": [
                {"key": "shipping_smsa_api_key", "label": "API Key (Passkey)", "type": "password"},
                {"key": "shipping_smsa_account_no", "label": "Account Number", "type": "text"},
                {"key": "shipping_smsa_sandbox", "label": "بيئة الاختبار", "type": "checkbox"},
            ],
        },
        {
            "id": "aramex",
            "name": "Aramex",
            "description": "شركة شحن إقليمية، مناسب للشحن بين دول الخليج",
            "configured": is_provider_configured("aramex"),
            "coverage": "MENA + 240 دولة",
            "signup_url": "https://www.aramex.com/solutions-services/business-solutions",
            "fields": [
                {"key": "shipping_aramex_api_key", "label": "API Key", "type": "password"},
                {"key": "shipping_aramex_account_no", "label": "Account Number", "type": "text"},
                {"key": "shipping_aramex_username", "label": "Username", "type": "text"},
                {"key": "shipping_aramex_password", "label": "Password", "type": "password"},
                {"key": "shipping_aramex_sandbox", "label": "بيئة الاختبار", "type": "checkbox"},
            ],
        },
        {
            "id": "dhl",
            "name": "DHL Express",
            "description": "للشحن الدولي السريع",
            "configured": is_provider_configured("dhl"),
            "coverage": "220+ دولة",
            "signup_url": "https://mydhl.express.dhl/sa/en/auth/login.html",
            "fields": [
                {"key": "shipping_dhl_api_key", "label": "API Key", "type": "password"},
                {"key": "shipping_dhl_account_no", "label": "Account Number", "type": "text"},
                {"key": "shipping_dhl_password", "label": "API Secret", "type": "password"},
                {"key": "shipping_dhl_sandbox", "label": "بيئة الاختبار", "type": "checkbox"},
            ],
        },
        {
            "id": "naqel",
            "name": "Naqel Express",
            "description": "شركة شحن سعودية، أسعار تنافسية",
            "configured": is_provider_configured("naqel"),
            "coverage": "السعودية والخليج",
            "signup_url": "https://naqelexpress.com/business",
            "fields": [
                {"key": "shipping_naqel_api_key", "label": "API Key", "type": "password"},
                {"key": "shipping_naqel_account_no", "label": "Account Number", "type": "text"},
                {"key": "shipping_naqel_sandbox", "label": "بيئة الاختبار", "type": "checkbox"},
            ],
        },
    ]


# ─── العمليات ────────────────────────────────────────────────────────

def create_shipment(
    order_data: Dict[str, Any],
    *,
    provider: Optional[str] = None,
) -> IntegrationResult:
    """
    إنشاء شحنة جديدة.

    order_data المتوقّع:
        {
            "order_id": "ORD-12345",
            "sender": {"name", "phone", "address", "city", "country"},
            "recipient": {"name", "phone", "address", "city", "country"},
            "weight_kg": 1.5,
            "items": [{"name", "quantity", "value"}],
            "cod_amount": 0,  # دفع عند الاستلام
            "currency": "SAR",
        }

    يُرجع IntegrationResult:
        data = {"awb": "...", "label_url": "...", "tracking_url": "..."}
    """
    p = (provider or get_active_provider()).lower()
    if not p:
        return IntegrationResult(
            success=False,
            error="No shipping provider configured. Configure in founder panel.",
        )

    if not is_provider_configured(p):
        return IntegrationResult(
            success=False,
            error=f"Shipping provider '{p}' is missing credentials.",
            provider=p,
        )

    backend = _get_backend(p)
    if backend is None:
        return IntegrationResult(success=False, error=f"unknown provider: {p}", provider=p)

    try:
        return backend.create_shipment(order_data)
    except Exception as e:
        logger.exception("Shipment creation failed (provider=%s)", p)
        return IntegrationResult(success=False, error=str(e), provider=p)


def track_shipment(awb: str, *, provider: Optional[str] = None) -> IntegrationResult:
    """تتبّع حالة الشحنة."""
    p = (provider or get_active_provider()).lower()
    backend = _get_backend(p)
    if backend is None:
        return IntegrationResult(success=False, error=f"unknown provider: {p}")
    try:
        return backend.track_shipment(awb)
    except Exception as e:
        logger.exception("Track shipment failed")
        return IntegrationResult(success=False, error=str(e), provider=p)


def cancel_shipment(awb: str, *, provider: Optional[str] = None) -> IntegrationResult:
    """إلغاء شحنة."""
    p = (provider or get_active_provider()).lower()
    backend = _get_backend(p)
    if backend is None:
        return IntegrationResult(success=False, error=f"unknown provider: {p}")
    try:
        return backend.cancel_shipment(awb)
    except Exception as e:
        logger.exception("Cancel shipment failed")
        return IntegrationResult(success=False, error=str(e), provider=p)


def calculate_rate(
    origin_city: str,
    dest_city: str,
    weight_kg: float,
    *,
    provider: Optional[str] = None,
) -> IntegrationResult:
    """حساب تكلفة الشحن المتوقّعة."""
    p = (provider or get_active_provider()).lower()
    backend = _get_backend(p)
    if backend is None:
        return IntegrationResult(success=False, error=f"unknown provider: {p}")
    try:
        return backend.calculate_rate(origin_city, dest_city, weight_kg)
    except Exception as e:
        logger.exception("Rate calculation failed")
        return IntegrationResult(success=False, error=str(e), provider=p)


def test_connection(provider: Optional[str] = None) -> Dict[str, Any]:
    """فحص اتصال + مفاتيح المزود."""
    p = (provider or get_active_provider()).lower()
    if not p:
        return {"success": False, "message": "No provider selected"}
    if not is_provider_configured(p):
        return {"success": False, "message": "Missing credentials"}
    backend = _get_backend(p)
    if backend is None:
        return {"success": False, "message": f"unknown provider: {p}"}
    try:
        return backend.test_connection()
    except Exception as e:
        return {"success": False, "message": str(e)}


# ─── Backends داخلية ─────────────────────────────────────────────────

def _get_backend(provider: str):
    """يُرجع الـ module المناسب لكل شركة شحن."""
    if provider == "smsa":
        return _SMSABackend()
    if provider == "aramex":
        return _AramexBackend()
    if provider == "dhl":
        return _DHLBackend()
    if provider == "naqel":
        return _NaqelBackend()
    return None


# ─── SMSA Implementation (نموذج كامل) ───────────────────────────────

class _SMSABackend:
    """
    SMSA Express API integration.

    SMSA REST API: https://track.smsaexpress.com/SECoreWebApi/api
    Sandbox:       https://track.smsaexpress.com/SECoreWebApi/api (نفس الـ endpoint مع sandbox key)

    Auth: X-API-KEY header with Passkey
    """

    def _api_key(self) -> str:
        return get_secret("shipping", "smsa_api_key")

    def _account(self) -> str:
        return get_secret("shipping", "smsa_account_no")

    def _is_sandbox(self) -> bool:
        return get_secret("shipping", "smsa_sandbox", "true").lower() == "true"

    def _base_url(self) -> str:
        # نفس الـ URL لكن SMSA يميّز sandbox عبر API key
        return "https://track.smsaexpress.com/SECoreWebApi/api"

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": self._api_key(),
        }

    def create_shipment(self, order_data: Dict[str, Any]) -> IntegrationResult:
        try:
            import requests
        except ImportError:
            return IntegrationResult(
                success=False,
                error="requests library required (pip install requests)",
                provider="smsa",
            )

        sender = order_data.get("sender", {})
        recipient = order_data.get("recipient", {})

        payload = {
            "passkey": self._api_key(),
            "refno": str(order_data.get("order_id", "")),
            "sentdate": "",
            "idno": "",
            "cs_name": recipient.get("name", ""),
            "cs_phone": recipient.get("phone", ""),
            "cs_addbl1": recipient.get("address", "")[:90],
            "cs_addbl2": recipient.get("city", ""),
            "cs_addbl3": "",
            "cs_city": recipient.get("city", ""),
            "cs_zip": recipient.get("zip", ""),
            "cs_cntry": recipient.get("country", "SA"),
            "weight": str(order_data.get("weight_kg", 1)),
            "waybills": "1",
            "carrvalue": str(order_data.get("cod_amount", 0)),
            "carrcurr": order_data.get("currency", "SAR"),
            "pcs": str(len(order_data.get("items", [{}]))),
            "cod_amt": str(order_data.get("cod_amount", 0)),
            "sms_en": "1",
            "shipper": sender.get("name", "Family Complex"),
            "sntfromctry": sender.get("country", "SA"),
        }

        try:
            r = requests.post(
                f"{self._base_url()}/addshipment",
                json=payload,
                headers=self._headers(),
                timeout=30,
            )
            r.raise_for_status()
            data = r.json() if r.text else {}
            awb = data.get("sawb", "") or data.get("AWB", "")
            if not awb:
                return IntegrationResult(
                    success=False,
                    error=f"SMSA did not return AWB: {data}",
                    provider="smsa",
                    raw_response=data,
                )
            return IntegrationResult(
                success=True,
                data={
                    "awb": awb,
                    "label_url": f"{self._base_url()}/getPDF?awbs={awb}",
                    "tracking_url": f"https://track.smsaexpress.com/v2/?tracknumbers={awb}",
                },
                provider="smsa",
                raw_response=data,
            )
        except Exception as e:
            logger.exception("SMSA create_shipment failed")
            return IntegrationResult(success=False, error=str(e), provider="smsa")

    def track_shipment(self, awb: str) -> IntegrationResult:
        try:
            import requests
        except ImportError:
            return IntegrationResult(success=False, error="requests required", provider="smsa")
        try:
            r = requests.get(
                f"{self._base_url()}/getTracking",
                params={"awbNo": awb, "passKey": self._api_key()},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            return IntegrationResult(
                success=True,
                data={"awb": awb, "events": data},
                provider="smsa",
                raw_response=data,
            )
        except Exception as e:
            return IntegrationResult(success=False, error=str(e), provider="smsa")

    def cancel_shipment(self, awb: str) -> IntegrationResult:
        try:
            import requests
        except ImportError:
            return IntegrationResult(success=False, error="requests required", provider="smsa")
        try:
            r = requests.post(
                f"{self._base_url()}/cancelShipment",
                json={"awbNo": awb, "passKey": self._api_key()},
                headers=self._headers(),
                timeout=15,
            )
            r.raise_for_status()
            return IntegrationResult(success=True, data={"awb": awb}, provider="smsa")
        except Exception as e:
            return IntegrationResult(success=False, error=str(e), provider="smsa")

    def calculate_rate(self, origin: str, dest: str, weight: float) -> IntegrationResult:
        # SMSA حالياً لا توفّر endpoint عام لاحتساب الأسعار من API
        # الأسعار تُحسب من العقد التجاري
        return IntegrationResult(
            success=True,
            data={
                "estimated_cost": None,
                "currency": "SAR",
                "note": "SMSA pricing is contract-based. Contact SMSA for rate sheet.",
            },
            provider="smsa",
        )

    def test_connection(self) -> Dict[str, Any]:
        try:
            import requests
        except ImportError:
            return {"success": False, "message": "requests library required"}
        # نختبر بطلب status بسيط
        try:
            r = requests.get(
                f"{self._base_url()}/getTracking",
                params={"awbNo": "TEST", "passKey": self._api_key()},
                timeout=10,
            )
            # حتى لو AWB غير موجود، 200 يعني الـ auth صح
            if r.status_code in (200, 404):
                return {"success": True, "message": "SMSA API reachable"}
            return {"success": False, "message": f"HTTP {r.status_code}: {r.text[:100]}"}
        except Exception as e:
            return {"success": False, "message": str(e)}


# ─── Aramex / DHL / Naqel: skeleton بنفس الواجهة ────────────────────
# (تعبئة الـ endpoints الفعلية تتم بعد فتح الحساب التجاري معهم)

class _AramexBackend:
    """Aramex SOAP/REST API. تعبئة كاملة بعد فتح الحساب."""

    def create_shipment(self, order_data: Dict[str, Any]) -> IntegrationResult:
        return IntegrationResult(
            success=False,
            error="Aramex integration scaffold ready. Configure account at aramex.com to activate.",
            provider="aramex",
        )

    def track_shipment(self, awb: str) -> IntegrationResult:
        return IntegrationResult(success=False, error="Aramex tracking not yet activated", provider="aramex")

    def cancel_shipment(self, awb: str) -> IntegrationResult:
        return IntegrationResult(success=False, error="Aramex cancel not yet activated", provider="aramex")

    def calculate_rate(self, origin: str, dest: str, weight: float) -> IntegrationResult:
        return IntegrationResult(success=False, error="Aramex rates not yet activated", provider="aramex")

    def test_connection(self) -> Dict[str, Any]:
        if get_secret("shipping", "aramex_api_key") and get_secret("shipping", "aramex_account_no"):
            return {"success": True, "message": "Aramex credentials set (full integration pending)"}
        return {"success": False, "message": "Aramex credentials missing"}


class _DHLBackend:
    """DHL Express MyDHL API."""

    def create_shipment(self, order_data: Dict[str, Any]) -> IntegrationResult:
        return IntegrationResult(
            success=False,
            error="DHL integration scaffold ready. Configure account at mydhl.express.dhl to activate.",
            provider="dhl",
        )

    def track_shipment(self, awb: str) -> IntegrationResult:
        return IntegrationResult(success=False, error="DHL tracking not yet activated", provider="dhl")

    def cancel_shipment(self, awb: str) -> IntegrationResult:
        return IntegrationResult(success=False, error="DHL cancel not yet activated", provider="dhl")

    def calculate_rate(self, origin: str, dest: str, weight: float) -> IntegrationResult:
        return IntegrationResult(success=False, error="DHL rates not yet activated", provider="dhl")

    def test_connection(self) -> Dict[str, Any]:
        if get_secret("shipping", "dhl_api_key") and get_secret("shipping", "dhl_account_no"):
            return {"success": True, "message": "DHL credentials set (full integration pending)"}
        return {"success": False, "message": "DHL credentials missing"}


class _NaqelBackend:
    """Naqel Express API."""

    def create_shipment(self, order_data: Dict[str, Any]) -> IntegrationResult:
        return IntegrationResult(
            success=False,
            error="Naqel integration scaffold ready. Configure account to activate.",
            provider="naqel",
        )

    def track_shipment(self, awb: str) -> IntegrationResult:
        return IntegrationResult(success=False, error="Naqel tracking not yet activated", provider="naqel")

    def cancel_shipment(self, awb: str) -> IntegrationResult:
        return IntegrationResult(success=False, error="Naqel cancel not yet activated", provider="naqel")

    def calculate_rate(self, origin: str, dest: str, weight: float) -> IntegrationResult:
        return IntegrationResult(success=False, error="Naqel rates not yet activated", provider="naqel")

    def test_connection(self) -> Dict[str, Any]:
        if get_secret("shipping", "naqel_api_key") and get_secret("shipping", "naqel_account_no"):
            return {"success": True, "message": "Naqel credentials set (full integration pending)"}
        return {"success": False, "message": "Naqel credentials missing"}