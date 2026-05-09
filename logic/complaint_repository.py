# -*- coding: utf-8 -*-
"""
ComplaintRepositoryMixin — إدارة الشكاوى.

التحديثات في هذا الإصدار:
- إضافة rollback() صريح في كل دالة عند فشل أي استعلام (تجنّب InFailedSqlTransaction).
- إصلاح حلقة توليد ticket_code: إنشاء cursor جديد بعد كل rollback (الـ cursor
  السابق قد يصبح غير صالح بعد rollback على PostgreSQL).
- استخدام self.db_type المتاح في DatabaseManager بدل إنشاء DBAdapter جديد كل مرة.
- نفس الواجهة العامة محفوظة بالكامل.
"""
from __future__ import annotations

import logging
import secrets
import string
from typing import Any, Dict, List, Optional
from logic.db_adapter import DBAdapter

logger = logging.getLogger(__name__)

_TICKET_ALPHABET = string.ascii_uppercase + string.digits


def _generate_complaint_ticket_code() -> str:
    return str(secrets.randbelow(900000) + 100000)


class ComplaintRepositoryMixin:
    """Mixin: يُدمج في DatabaseManager — يستخدم self._get_connection() فقط."""

    def _db_adapter(self) -> DBAdapter:
        return DBAdapter(sqlite_path=getattr(self, "db_path", None))

    def _safe_rollback_pg(self, conn) -> None:
        """rollback آمن للـ PostgreSQL فقط — لا يعمل شيء على SQLite."""
        if getattr(self, "db_type", None) != "postgres":
            return
        try:
            conn.rollback()
        except Exception as e:
            logger.warning("rollback failed in complaint_repository: %s", e)

    def add_complaint(
        self,
        user_id,
        issue,
        branch_id=None,
        employee_name=None,
        department=None,
        status="pending",
        complaint_type="unspecified",
        message=None,
        branch_name=None,
        customer_name=None,
        customer_phone=None,
        customer_email=None,
        complaint_ai_classification=None,
        ticket_code=None,
    ):
        # الشكوى تُسجَّل فقط عند ربطها بفرع (لوحات الفرع / المؤسس / الإدارة).
        if branch_id is None:
            logger.warning("add_complaint: rejected — branch_id is required")
            return None
        conn = self._get_connection()
        try:
            ct = (complaint_type or "unspecified").strip() or "unspecified"
            st = (status or "pending").strip() or "pending"
            msg = (message if message is not None else issue) or ""
            bn = (branch_name or "").strip() or None
            cn = (customer_name or "").strip() or None
            cp = (customer_phone or "").strip() or None
            ce = (customer_email or "").strip() or None
            ai_c = (complaint_ai_classification or "").strip() or None
            tc = (ticket_code or "").strip().upper() or None

            for _attempt in range(32):
                code = tc or _generate_complaint_ticket_code()
                # ملاحظة الإصلاح: ننشئ cursor جديد لكل محاولة لأن
                # rollback() على PostgreSQL يُبطل الـ cursor السابق.
                cursor = conn.cursor()
                try:
                    cursor.execute(
                        """
                        INSERT INTO complaints (
                            user_id, branch_id, employee_name, department,
                            issue, status, complaint_type, message, branch_name,
                            customer_name, customer_phone, customer_email,
                            complaint_ai_classification, ticket_code
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            branch_id,
                            employee_name,
                            department,
                            issue,
                            st,
                            ct,
                            msg,
                            bn,
                            cn,
                            cp,
                            ce,
                            ai_c,
                            code,
                        ),
                    )
                    conn.commit()
                    return cursor.lastrowid
                except Exception:
                    # rollback لتنظيف حالة المعاملة على PostgreSQL
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if tc:
                        # الـ ticket_code أتى من الخارج وفشل — لا فائدة من إعادة المحاولة
                        logger.warning("add_complaint: ticket_code مكرر %s", tc)
                        return None
                    # وإلا نولّد كود جديد ونعيد المحاولة
                    continue

            logger.warning("add_complaint: تعذر توليد رقم تذكرة فريد بعد 32 محاولة")
            return None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("add_complaint DB error: %s", e)
            return None
        finally:
            conn.close()

    def append_complaint_issue(self, complaint_id: int, extra_text: str) -> bool:
        """إلحاق نص برسالة شكوى قائمة (نفس السجل)."""
        extra_text = (extra_text or "").strip()
        if not extra_text:
            return True
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT issue, message FROM complaints WHERE id = %s", (complaint_id,)
            )
            row = cursor.fetchone()
            if not row:
                logger.warning("append_complaint_issue: لا يوجد سجل #%s", complaint_id)
                return False
            r = dict(row)
            old = (r.get("issue") or "").strip()
            new_issue = f"{old}\n────────────\n{extra_text}" if old else extra_text
            old_msg = (r.get("message") or "").strip()
            new_msg = f"{old_msg}\n────────────\n{extra_text}" if old_msg else extra_text
            conn.execute(
                "UPDATE complaints SET issue = %s, message = %s WHERE id = %s",
                (new_issue, new_msg, complaint_id),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("append_complaint_issue: %s", e)
            return False
        finally:
            conn.close()

    def get_complaint_row(self, complaint_id: int):
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT id, issue, branch_id, user_id, created_at, status, complaint_type,
                       message, branch_name, customer_name, customer_phone, customer_email,
                       resolved_at, ticket_code, resolution_notes, employee_name, department
                FROM complaints WHERE id = %s
                """,
                (complaint_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_complaint_row: %s", e)
            return None
        finally:
            conn.close()

    def get_complaint_by_ticket_code(self, ticket_code: str) -> Optional[Dict[str, Any]]:
        raw = (ticket_code or "").strip().upper().replace(" ", "")
        if not raw:
            return None
        # دعم الصيغة القديمة TKT-XXXXXXXX والجديدة 6 أرقام
        if not raw.startswith("TKT-") and not raw.isdigit():
            if len(raw) == 8 and raw.isalnum():
                raw = "TKT-" + raw
            else:
                return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                """
                SELECT id, issue, branch_id, user_id, created_at, status, complaint_type,
                       message, branch_name, customer_name, customer_phone, customer_email,
                       resolved_at, ticket_code, resolution_notes, employee_name, department
                FROM complaints WHERE UPPER(TRIM(COALESCE(ticket_code,''))) = %s
                """,
                (raw,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_complaint_by_ticket_code: %s", e)
            return None
        finally:
            conn.close()

    def update_complaint_type(self, complaint_id: int, complaint_type: str) -> bool:
        """تحديث تصنيف الشكوى (مثلاً بعد إلحاق نص جديد وإعادة التصنيف)."""
        ct = (complaint_type or "unspecified").strip() or "unspecified"
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE complaints SET complaint_type = %s WHERE id = %s",
                (ct, complaint_id),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("update_complaint_type: %s", e)
            return False
        finally:
            conn.close()

    def update_complaint_status(self, complaint_id, status):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            st = (status or "").strip().lower()
            if st == "resolved":
                cursor.execute(
                    """
                    UPDATE complaints SET status = %s, resolved_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (st, complaint_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE complaints SET status = %s, resolved_at = NULL
                    WHERE id = %s
                    """,
                    (st, complaint_id),
                )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            self._safe_rollback_pg(conn)
            return False
        finally:
            conn.close()

    def resolve_complaint(
        self, complaint_id: int, resolution_notes: Optional[str] = None
    ) -> bool:
        """تعليم الشكوى كمحلولة مع طابع وقت التحديد وملاحظات اختيارية."""
        notes = (resolution_notes or "").strip()
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                UPDATE complaints
                SET status = 'resolved',
                    resolved_at = CURRENT_TIMESTAMP,
                    resolution_notes = %s
                WHERE id = %s
                  AND LOWER(TRIM(COALESCE(status,''))) != 'resolved'
                """,
                (notes, complaint_id),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("resolve_complaint: %s", e)
            return False
        finally:
            conn.close()

    def get_complaints_stats(self) -> Dict[str, int]:
        conn = self._get_connection()
        try:
            total_row = conn.execute("SELECT COUNT(*) AS c FROM complaints").fetchone()
            total = int(total_row["c"]) if total_row else 0
            res_row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM complaints
                WHERE LOWER(TRIM(COALESCE(status,''))) = 'resolved'
                """
            ).fetchone()
            resolved = int(res_row["c"]) if res_row else 0
            open_count = max(0, total - resolved)
            return {"total": total, "open": open_count, "resolved": resolved}
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_complaints_stats: %s", e)
            return {"total": 0, "open": 0, "resolved": 0}
        finally:
            conn.close()

    def list_complaints_branch_filter_options(self) -> List[str]:
        names = set()
        conn = self._get_connection()
        try:
            for row in conn.execute(
                """
                SELECT DISTINCT branch_name FROM complaints
                WHERE branch_name IS NOT NULL AND TRIM(branch_name) != ''
                """
            ):
                names.add((row["branch_name"] or "").strip())
            for row in conn.execute("SELECT city_name FROM branches ORDER BY id"):
                cn = (row["city_name"] or "").strip()
                if cn:
                    names.add(cn)
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("list_complaints_branch_filter_options: %s", e)
        finally:
            conn.close()
        return sorted(names, key=lambda x: x)

    def get_complaints(
        self,
        branch_id=None,
        branch_name=None,
        status=None,
        limit=500,
    ):
        conn = self._get_connection()
        try:
            q = "SELECT * FROM complaints WHERE 1=1"
            params = []
            if branch_id is not None:
                q += " AND branch_id = %s"
                params.append(branch_id)
            if branch_name:
                bn = branch_name.strip()
                q += " AND TRIM(COALESCE(branch_name,'')) = %s"
                params.append(bn)
            if status:
                st = (status.strip()).lower()
                if st == "open":
                    q += " AND LOWER(TRIM(COALESCE(status,''))) != 'resolved'"
                elif st == "resolved":
                    q += " AND LOWER(TRIM(COALESCE(status,''))) = 'resolved'"
            # استخدام self.db_type المتاح في DatabaseManager (تحسين أداء)
            db_type = getattr(self, "db_type", None) or self._db_adapter().db_type
            if db_type == "postgres":
                q += (
                    " ORDER BY COALESCE(created_at::timestamptz, TIMESTAMP '1970-01-01') "
                    "DESC LIMIT %s"
                )
            else:
                q += (
                    " ORDER BY datetime(COALESCE(created_at, '1970-01-01')) DESC LIMIT %s"
                )
            params.append(limit)
            cursor = conn.execute(q, tuple(params))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_complaints: %s", e)
            return []
        finally:
            conn.close()

    def get_complaints_by_category(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(complaint_ai_classification),''), 'غير مصنّف') AS category,
                       COUNT(*) AS cnt
                FROM complaints
                GROUP BY category
                ORDER BY cnt DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_complaints_by_category: %s", e)
            return []
        finally:
            conn.close()

    def get_complaints_by_branch(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(branch_name),''), '—') AS branch,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN LOWER(TRIM(COALESCE(status,''))) = 'resolved' THEN 1 ELSE 0 END) AS resolved
                FROM complaints
                GROUP BY branch
                ORDER BY cnt DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_complaints_by_branch: %s", e)
            return []
        finally:
            conn.close()

    def get_complaints_by_employee(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT employee_name, branch_name,
                       COUNT(*) AS cnt
                FROM complaints
                WHERE employee_name IS NOT NULL AND TRIM(employee_name) != ''
                GROUP BY employee_name, branch_name
                ORDER BY cnt DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_complaints_by_employee: %s", e)
            return []
        finally:
            conn.close()