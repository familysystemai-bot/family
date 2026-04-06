# -*- coding: utf-8 -*-
"""
تشخيص شامل لحالة المشروع (قراءة فقط — لا يعدّل النظام).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from config import (
    BASE_DIR,
    DATA_DIR,
    DIAGNOSTICS_ALERT_COOLDOWN_SECONDS,
    LLM_ENABLED,
    SENDER_EMAIL,
    SYSTEM_ALERTS_EMAIL,
)

logger = logging.getLogger(__name__)

_DIAG_ALERT_STATE_PATH = DATA_DIR / "diagnostics_alert_state.json"

if TYPE_CHECKING:
    from logic.database import DatabaseManager

LOGO_FILENAME = "family_logo.png"
APP_PY_PATH = BASE_DIR / "app.py"
LEGACY_FILES = (
    BASE_DIR / "logic" / "handler.py",
    BASE_DIR / "logic" / "intent_router.py",
    BASE_DIR / "logic" / "responses.py",
)
COMPANY_POLICIES_PATH = BASE_DIR / "site_config" / "company_policies.py"

# قوالب تُتوقع أن تعرض الشعار (مسار url_for الثابت في المشروع)
TEMPLATES_WITH_LOGO = (
    "index.html",
    "login.html",
    "dashboard.html",
    "add_product.html",
    "admin_users.html",
    "products.html",
    "sections.html",
)


def _read_app_py_text() -> str:
    try:
        return APP_PY_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _analyze_chat_routes(app_text: str) -> Dict[str, Any]:
    has_chat_query = bool(
        re.search(r"""@app\.route\(\s*['\"]/chat_query['\"]""", app_text)
    )
    has_legacy_chat = bool(
        re.search(r"""@app\.route\(\s*['\"]/chat['\"]""", app_text)
    )
    imports_handler = bool(
        re.search(r"from\s+logic\.handler\s+import|MessageHandler", app_text)
    )
    if has_chat_query and not has_legacy_chat and not imports_handler:
        status = "ok"
    else:
        status = "issue"
    return {
        "chat_status": status,
        "chat_query_registered": has_chat_query,
        "legacy_chat_route_present": has_legacy_chat,
        "handler_import_in_app": imports_handler,
    }


def _analyze_llm_fallback(app_text: str) -> Dict[str, Any]:
    """رصد استدعاءات _try_llm_fallback_route في app.py (أول استدعاء ≈ unknown، الثاني ≈ فشل منتج)."""
    fb = "_try_llm_fallback_route(message, branch_list)"
    triggers: List[str] = []
    if fb not in app_text:
        return {
            "llm_fallback_triggers_detected": [],
            "llm_fallback_only_unknown": False,
            "llm_fallback_note": "لم يُعثر على استدعاء احتياط LLM في app.py.",
        }
    n_calls = len(list(re.finditer(re.escape(fb), app_text)))
    if n_calls >= 1:
        triggers.append("unknown_intent")
    if n_calls >= 2:
        triggers.append("product_search_empty")
    only_unknown = n_calls == 1
    note = (
        "الاحتياط يُستدعى عند intent=unknown وبعد فشل بحث منتج."
        if n_calls >= 2
        else "يُستدعى عند unknown فقط (استدعاء واحد في app.py)."
    )
    return {
        "llm_fallback_triggers_detected": triggers,
        "llm_fallback_only_unknown": only_unknown,
        "llm_fallback_note": note,
        "llm_fallback_call_count": n_calls,
    }


def _check_llm_module() -> Dict[str, Any]:
    out: Dict[str, Any] = {"llm_enabled_config": bool(LLM_ENABLED)}
    try:
        from logic import llm_analyzer

        out["llm_analyzer_import"] = "ok"
        out["has_analyze_user_message"] = hasattr(llm_analyzer, "analyze_user_message")
        try:
            # بدون تشغيل شبكة إن كان المعطّل
            r = llm_analyzer.analyze_user_message("__diagnostics__")
            out["analyze_call_result"] = "returned_none" if r is None else "returned_dict"
        except Exception as e:
            out["analyze_call_result"] = f"error: {type(e).__name__}: {e}"
    except Exception as e:
        out["llm_analyzer_import"] = f"fail: {type(e).__name__}: {e}"
    return out


def _table_column_names(conn, table: str) -> List[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    rows = cur.fetchall()
    names: List[str] = []
    for row in rows:
        if hasattr(row, "keys") and "name" in row.keys():
            names.append(str(row["name"]))
        else:
            names.append(str(row[1]))
    return names


def _database_snapshot(db: "DatabaseManager") -> Dict[str, Any]:
    conn = db._get_connection()
    try:
        prod_cols = set(_table_column_names(conn, "products"))
        branch_cols = set(_table_column_names(conn, "branches"))
        has_sku = "sku" in prod_cols
        has_complaint_email = "complaint_email" in branch_cols
        return {
            "products_table": "has_sku" if has_sku else "missing_sku_column",
            "products_columns_include_sku": has_sku,
            "branches_table_columns": sorted(branch_cols),
            "branches_has_complaint_email_column": has_complaint_email,
            "branches_has_address_column": "address" in branch_cols,
            "branches_has_gps_column": "gps_location" in branch_cols
            or ("gps_lat" in branch_cols and "gps_lng" in branch_cols),
            "branches_has_working_hours_column": "working_hours" in branch_cols,
            "schema_note": (
                "العناوين وGPS في جدول branch_locations؛ أوقات الدوام في working_hours وليس أعمدة داخل branches."
            ),
        }
    finally:
        conn.close()


def _branches_operational(db: "DatabaseManager") -> Dict[str, Any]:
    branches = db.get_all_branches()
    total = len(branches)
    email_rows = db.list_branches_complaint_emails()
    missing_email = [
        r["branch"]
        for r in email_rows
        if (r.get("branch") or "") and not (r.get("email") or "").strip()
    ]
    missing_location: List[str] = []
    missing_hours: List[str] = []
    for b in branches:
        bid = int(b["id"])
        name = b.get("city_name") or f"id:{bid}"
        loc = db.get_branch_location(bid)
        if not loc or not (
            (loc.get("address") or "").strip() or (loc.get("google_maps_url") or "").strip()
        ):
            missing_location.append(name)
        wh = db.get_working_hours(bid)
        if not wh:
            missing_hours.append(name)
    return {
        "total": total,
        "missing_email": missing_email,
        "missing_location": missing_location,
        "missing_working_hours": missing_hours,
    }


def _policies_status() -> Dict[str, Any]:
    if not COMPANY_POLICIES_PATH.is_file():
        return {"policies": "missing", "company_policies_path": str(COMPANY_POLICIES_PATH)}
    try:
        text = COMPANY_POLICIES_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"policies": "unreadable", "error": str(e)}
    has_return = bool(re.search(r"return_policy\s*[:=]", text))
    return {
        "policies": "ok" if has_return else "incomplete",
        "return_policy_defined": has_return,
        "company_policies_path": str(COMPANY_POLICIES_PATH),
    }


def _legacy_files_report() -> Dict[str, Any]:
    present = [str(p.relative_to(BASE_DIR)).replace("\\", "/") for p in LEGACY_FILES if p.is_file()]
    return {
        "unused_files": present,
        "legacy_chat_files_present": bool(present),
    }


def _logo_report() -> Dict[str, Any]:
    logo_path = BASE_DIR / "static" / LOGO_FILENAME
    exists = logo_path.is_file()
    templates_dir = BASE_DIR / "templates"
    refs: Dict[str, Any] = {}
    missing_refs: List[str] = []
    for name in TEMPLATES_WITH_LOGO:
        fp = templates_dir / name
        if not fp.is_file():
            missing_refs.append(f"{name} (ملف غير موجود)")
            continue
        try:
            body = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing_refs.append(f"{name} (تعذر القراءة)")
            continue
        ok = (
            f"filename='{LOGO_FILENAME}'" in body
            or f'filename="{LOGO_FILENAME}"' in body
            or LOGO_FILENAME in body
        )
        refs[name] = "references_logo" if ok else "no_family_logo_reference"
        if not ok:
            missing_refs.append(name)
    return {
        "logo_file": LOGO_FILENAME,
        "logo_path_absolute": str(logo_path),
        "logo_file_exists": exists,
        "templates_checked": list(TEMPLATES_WITH_LOGO),
        "template_logo_reference_status": refs,
        "templates_missing_logo_reference": missing_refs,
        "logo_status": "ok"
        if exists and not missing_refs
        else ("missing_file" if not exists else "template_mismatch"),
    }


def _build_warnings(d: Dict[str, Any]) -> List[str]:
    """رسائل عربية جاهزة للبشر من بيانات التشخيص."""
    out: List[str] = []
    seen: set = set()

    def add(msg: str) -> None:
        if msg and msg not in seen:
            seen.add(msg)
            out.append(msg)

    if d.get("chat_status") != "ok":
        add(
            "مسار الشات غير مطابق للمعيار (وجود /chat قديم أو غياب /chat_query أو استيراد handler) "
            "→ راجع توحيد الشات في app.py."
        )

    if d.get("ai_status") == "module_error":
        add(
            "وحدة تحليل LLM لا تُستورد أو تفشل عند التشغيل "
            "→ طبقة الذكاء الاحتياطية قد لا تعمل."
        )

    st = d.get("smtp_status")
    if st != "success":
        err = d.get("smtp_error_summary")
        tail = f" ({err})" if err else ""
        add(f"فشل SMTP → الشكاوى والتنبيهات لن تُرسل عبر البريد{tail}.")

    em = d.get("email_diagnostics_embed") or {}
    if em.get("main_receiver_configured") is False:
        add(
            "MAIN_RECEIVER_EMAIL غير مضبوط → صندوق الشكاوى الرئيسي قد لا يستلم البلاغات."
        )
    ps = em.get("password_status")
    if ps and ps != "ok":
        add(
            "تعذّر أو عدم صلاحية SENDER_PASSWORD (كلمة مرور التطبيقات) "
            "→ الإرسال عبر Gmail لن يعمل حتى التصحيح."
        )

    if d.get("products_table") != "has_sku":
        add(
            "عمود SKU غير موجود في جدول المنتجات → أرقام الأصناف لن تُخزَّن حتى ترقية المخطط."
        )

    br = d.get("branches") or {}
    for name in br.get("missing_email") or []:
        lbl = name or "غير معروف"
        add(f"فرع {lbl} لا يحتوي على إيميل شكاوى → لن تصل الشكاوى لهذا الفرع عبر البريد.")
    for name in br.get("missing_location") or []:
        lbl = name or "غير معروف"
        add(
            f"فرع {lbl} بلا عنوان أو رابط خرائط في قاعدة البيانات "
            "→ العملاء قد لا يعثرون على الموقع من النظام."
        )
    for name in br.get("missing_working_hours") or []:
        lbl = name or "غير معروف"
        add(
            f"فرع {lbl} بلا سجل دوام في قاعدة البيانات "
            "→ أوقات العمل قد لا تظهر بشكل كامل."
        )

    if d.get("policies") != "ok":
        add(
            "ملف سياسات الشركة ناقص أو return_policy غير معرف "
            "→ ردود سياسة الاستبدال/الشكاوى قد تكون غير مكتملة."
        )

    ufs = d.get("unused_files") or []
    if ufs:
        add(
            f"ملفات قديمة ما زالت موجودة ({', '.join(ufs)}) "
            "→ قد يسبّب لبساً مع مسار الشات الموحّد."
        )

    lg = d.get("logo") or {}
    if lg.get("logo_status") == "missing_file":
        add("الشعار غير موجود في مجلد static → الواجهة قد تظهر بدون شعار.")
    elif lg.get("logo_status") == "template_mismatch":
        mr = lg.get("templates_missing_logo_reference") or []
        add(
            "بعض القوالب لا تشير لملف الشعار المتوقع → قد تظهر صفحات بدون شعار "
            f"({', '.join(mr[:5])}{'…' if len(mr) > 5 else ''})."
        )

    ds = d.get("database_schema") or {}
    if not ds.get("branches_has_complaint_email_column"):
        add(
            "جدول branches يفتقد عمود complaint_email في المخطط الحالي "
            "→ ترقية قاعدة البيانات قد تكون ناقصة."
        )

    return out


def _alerts_should_send(warnings: List[str]) -> Tuple[bool, str]:
    if not warnings:
        return False, "no_warnings"
    if not (SYSTEM_ALERTS_EMAIL or "").strip():
        return False, "no_recipient"
    payload = "\n".join(warnings)
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    now = time.time()
    try:
        if _DIAG_ALERT_STATE_PATH.is_file():
            raw = json.loads(_DIAG_ALERT_STATE_PATH.read_text(encoding="utf-8"))
            if raw.get("hash") == h and (now - float(raw.get("ts", 0))) < max(
                60, DIAGNOSTICS_ALERT_COOLDOWN_SECONDS
            ):
                return False, "cooldown_same_bundle"
    except Exception:
        pass
    return True, "ok"


def _alerts_record_sent(warnings: List[str]) -> None:
    h = hashlib.sha256("\n".join(warnings).encode("utf-8")).hexdigest()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _DIAG_ALERT_STATE_PATH.write_text(
            json.dumps({"hash": h, "ts": time.time()}, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Could not write diagnostics alert state: %s", e)


def _send_warnings_email(warnings: List[str], summary_one_liner: str) -> bool:
    from logic.mail_service import send_email

    to = SYSTEM_ALERTS_EMAIL.strip()
    if not to:
        return False
    subj = "[Family System] تنبيهات تشخيص النظام"
    body_lines = [
        "تم تشغيل التشخيص وظهرت الملاحظات التالية:",
        "",
        *[f"- {x}" for x in warnings],
        "",
        f"ملخص: {summary_one_liner}",
        "",
        "— رسالة آلية من المسار /admin/diagnostics/full",
    ]
    body = "\n".join(body_lines)
    try:
        return bool(send_email([to], subj, body))
    except Exception as e:
        logger.warning("Diagnostics alert email failed: %s", e)
        return False


def run_full_diagnostics(
    db: Optional["DatabaseManager"] = None,
    send_alerts: bool = False,
) -> Dict[str, Any]:
    """
    لقطة شاملة للمشروع. تمرير db اختياري (مثل مثيل التطبيق).
    send_alerts=True: إرسال warnings إلى SYSTEM_ALERTS_EMAIL عند وجودها (مع فترة تهدئة).
    """
    if db is None:
        from logic.database import DatabaseManager

        db = DatabaseManager()

    app_text = _read_app_py_text()
    chat = _analyze_chat_routes(app_text)
    llm_fb = _analyze_llm_fallback(app_text)
    llm_mod = _check_llm_module()

    if LLM_ENABLED:
        ai_status = "enabled"
    else:
        ai_status = "disabled"
    if llm_mod.get("llm_analyzer_import") != "ok":
        ai_status = "module_error"

    from logic.email_diagnostics import run_email_diagnostics

    email_diag = run_email_diagnostics(db)
    smtp_raw = email_diag.get("smtp_connection") or "unknown"
    smtp_status = "success" if smtp_raw == "success" else "failed"

    db_snap = _database_snapshot(db)
    branches_block = _branches_operational(db)
    policies = _policies_status()
    legacy = _legacy_files_report()
    logo = _logo_report()

    products_table = db_snap.get("products_table", "unknown")

    payload: Dict[str, Any] = {
        "chat_status": chat.get("chat_status"),
        "chat_routes": {
            "chat_query": chat.get("chat_query_registered"),
            "legacy_chat": chat.get("legacy_chat_route_present"),
        },
        "ai_status": ai_status,
        "llm": {
            **llm_mod,
            **llm_fb,
        },
        "smtp_status": smtp_status,
        "smtp_connection": smtp_raw,
        "smtp_sender_configured": bool((SENDER_EMAIL or "").strip()),
        "smtp_error_summary": email_diag.get("smtp_error"),
        "products_table": products_table,
        "database_schema": db_snap,
        "branches": {
            "total": branches_block["total"],
            "missing_email": branches_block["missing_email"],
            "missing_location": branches_block["missing_location"],
            "missing_working_hours": branches_block.get("missing_working_hours", []),
        },
        "policies": policies.get("policies"),
        "policies_detail": policies,
        "unused_files": legacy.get("unused_files", []),
        "legacy_chat_files_present": legacy.get("legacy_chat_files_present"),
        "logo": logo,
        "email_diagnostics_embed": {
            "main_receiver_configured": email_diag.get("main_receiver_configured"),
            "password_status": email_diag.get("password_status"),
        },
    }

    warnings = _build_warnings(payload)
    payload["warnings"] = warnings
    payload["warnings_count"] = len(warnings)

    payload["alerts_email_target"] = SYSTEM_ALERTS_EMAIL if SYSTEM_ALERTS_EMAIL else None
    payload["alerts_email_sent"] = False
    payload["alerts_email_skip_reason"] = None

    if send_alerts:
        can_send, reason = _alerts_should_send(warnings)
        payload["alerts_email_skip_reason"] = reason if not can_send else None
        if can_send and warnings:
            summary = (
                f"عدد التنبيهات: {len(warnings)}"
                if warnings
                else "لا تنبيهات"
            )
            ok_mail = _send_warnings_email(warnings, summary)
            payload["alerts_email_sent"] = ok_mail
            if ok_mail:
                _alerts_record_sent(warnings)
            elif not ok_mail and warnings:
                payload["alerts_email_skip_reason"] = "send_failed"
    else:
        payload["alerts_email_skip_reason"] = "send_alerts_disabled"

    return payload
