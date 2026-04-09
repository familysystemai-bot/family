# -*- coding: utf-8 -*-
"""معلومات الشركة وخدمات الفروع — للوحة الإدارة وللسياق في الذكاء الاصطناعي."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

ALLOWED_COMPANY_INFO_KEYS = frozenset(
    {
        "delivery_policy",
        "return_policy",
        "exchange_policy",
        "working_hours",
        "payment_methods",
        "branches_info",
        "general_info",
    }
)

# رد قصير عند سؤال عن سياسة ولا يوجد نص في قاعدة البيانات
POLICY_MISSING_FALLBACK_AR = "حالياً ما عندي تفاصيل، ممكن أوضح لك لاحقاً"


class CompanyInfoRepositoryMixin:
    """Mixin لـ DatabaseManager — يستخدم self._get_connection() فقط."""

    def get_all_company_info_rows(self) -> Dict[str, str]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT key, value FROM company_info ORDER BY key")
            return {str(r["key"]): (r["value"] or "") for r in cur.fetchall()}
        finally:
            conn.close()

    def set_company_info_key(self, key: str, value: str) -> bool:
        k = (key or "").strip()
        if k not in ALLOWED_COMPANY_INFO_KEYS:
            return False
        v = value if value is not None else ""
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO company_info (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (k, v),
            )
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def bulk_set_company_info(self, mapping: Dict[str, str]) -> None:
        for k, v in (mapping or {}).items():
            if k in ALLOWED_COMPANY_INFO_KEYS:
                self.set_company_info_key(k, v if v is not None else "")

    def list_branch_services_with_branches(self) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT s.id, s.branch_id, s.service_title, s.details, s.sort_order,
                       b.city_name AS branch_city_name
                FROM company_branch_services s
                JOIN branches b ON b.id = s.branch_id
                ORDER BY s.branch_id, s.sort_order, s.id
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def replace_branch_services(
        self, branch_id: int, pairs: List[Tuple[str, str]]
    ) -> bool:
        """يستبدل كل خدمات الفرع بقائمة (عنوان، تفاصيل) — يتخطى الصفوف الفارغة."""
        bid = int(branch_id)
        cleaned: List[Tuple[str, str]] = []
        for title, details in pairs:
            t = (title or "").strip()
            d = (details or "").strip()
            if not t and not d:
                continue
            cleaned.append((t or "خدمة", d))
        conn = self._get_connection()
        try:
            conn.execute(
                "DELETE FROM company_branch_services WHERE branch_id = ?", (bid,)
            )
            for i, (t, d) in enumerate(cleaned):
                conn.execute(
                    """
                    INSERT INTO company_branch_services
                    (branch_id, service_title, details, sort_order)
                    VALUES (?, ?, ?, ?)
                    """,
                    (bid, t, d, i),
                )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

    def get_company_info_for_ai(self) -> Dict[str, Any]:
        """بنية موحّدة لـ database_context.company_info في المنسّق والـ fallback."""
        rows = self.get_all_company_info_rows()
        out: Dict[str, Any] = {
            "delivery": (rows.get("delivery_policy") or "").strip(),
            "returns": (rows.get("return_policy") or "").strip(),
            "exchange": (rows.get("exchange_policy") or "").strip(),
            "hours": (rows.get("working_hours") or "").strip(),
            "payment": (rows.get("payment_methods") or "").strip(),
            "branches_blurb": (rows.get("branches_info") or "").strip(),
            "general": (rows.get("general_info") or "").strip(),
            "branch_services": [],
        }
        by_branch: Dict[int, Dict[str, Any]] = {}
        for r in self.list_branch_services_with_branches():
            bid = int(r["branch_id"])
            if bid not in by_branch:
                by_branch[bid] = {
                    "branch_id": bid,
                    "branch_name": (r.get("branch_city_name") or "").strip(),
                    "services": [],
                }
            by_branch[bid]["services"].append(
                {
                    "title": (r.get("service_title") or "").strip(),
                    "details": (r.get("details") or "").strip(),
                }
            )
        out["branch_services"] = list(by_branch.values())
        return out

    def get_policy_answer_exact(self, user_message: str) -> Optional[str]:
        """
        عند وجود كلمات سياسة في الرسالة: يُعاد النص المخزَّن كما هو من company_info (بدون تحرير).
        إن وُجدت المواضيع لكن الحقول فارغة → POLICY_MISSING_FALLBACK_AR.
        إن لم تُذكر مواضيع سياسة → None (يُكمَل المسار العادي مثل site_config).
        """
        m = (user_message or "").strip()
        if not m:
            return None
        topics: List[str] = []
        if any(
            x in m
            for x in (
                "استرجاع",
                "أرجع",
                "ارجع",
                "ارجاع",
                "الاسترجاع",
                "أسترجع",
            )
        ):
            topics.append("returns")
        if any(
            x in m
            for x in (
                "استبدال",
                "الاستبدال",
                "تبديل",
                "أبدل",
            )
        ):
            topics.append("exchange")
        if any(x in m for x in ("توصيل", "شحن", "الشحن", "التوصيل")):
            topics.append("delivery")
        if any(x in m for x in ("دفع", "الدفع", "فيزا", "مدى", "تقسيط", "السداد", "سداد")):
            topics.append("payment")
        if any(
            x in m
            for x in ("دوام", "ساعات", "متى تفتحون", "مفتوحين", "وقت العمل", "تفتحون")
        ):
            topics.append("hours")
        if "ضمان" in m:
            topics.append("general")
        seen: set = set()
        uniq: List[str] = []
        for t in topics:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        topics = uniq
        if not topics:
            return None
        ci = self.get_company_info_for_ai()
        parts = []
        for t in topics:
            v = (ci.get(t) or "").strip()
            if v:
                parts.append(v)
        if parts:
            return "\n\n".join(parts)
        return POLICY_MISSING_FALLBACK_AR

    def get_return_policy_bundle_text(self) -> str:
        """يجمع حقول السياسة غير الفارغة — لمسار «سياسة عامة» دون كلمات مفتاحية محددة."""
        ci = self.get_company_info_for_ai()
        order = (
            "returns",
            "exchange",
            "delivery",
            "payment",
            "hours",
            "branches_blurb",
            "general",
        )
        parts = [(ci.get(k) or "").strip() for k in order if (ci.get(k) or "").strip()]
        return "\n\n".join(parts) if parts else ""

    def get_complaint_precheck_policy_summary_text(self) -> Optional[str]:
        """فقرة قصيرة من سياسة الاسترجاع المسجّلة فقط؛ أو None إن لم يُدخل شيء."""
        ci = self.get_company_info_for_ai()
        r = (ci.get("returns") or "").strip()
        return r if r else None
