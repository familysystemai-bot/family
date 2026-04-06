# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


class ComplaintRepositoryMixin:
    """Mixin: يُدمج في DatabaseManager — يستخدم self._get_connection() فقط."""
    def add_complaint(
        self,
        user_id,
        issue,
        branch_id=None,
        employee_name=None,
        department=None,
        status="open",
        complaint_type="unspecified",
        message=None,
        branch_name=None,
        customer_name=None,
        customer_phone=None,
        customer_email=None,
    ):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            ct = (complaint_type or "unspecified").strip() or "unspecified"
            st = (status or "open").strip() or "open"
            msg = (message if message is not None else issue) or ""
            bn = (branch_name or "").strip() or None
            cn = (customer_name or "").strip() or None
            cp = (customer_phone or "").strip() or None
            ce = (customer_email or "").strip() or None
            cursor.execute(
                """
                INSERT INTO complaints (
                    user_id, branch_id, employee_name, department,
                    issue, status, complaint_type, message, branch_name,
                    customer_name, customer_phone, customer_email
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            print(f"❌ add_complaint DB error: {e}")
            import traceback

            traceback.print_exc()
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
                "SELECT issue, message FROM complaints WHERE id = ?", (complaint_id,)
            )
            row = cursor.fetchone()
            if not row:
                print(f"❌ append_complaint_issue: لا يوجد سجل #{complaint_id}")
                return False
            r = dict(row)
            old = (r.get("issue") or "").strip()
            new_issue = f"{old}\n────────────\n{extra_text}" if old else extra_text
            old_msg = (r.get("message") or "").strip()
            new_msg = f"{old_msg}\n────────────\n{extra_text}" if old_msg else extra_text
            conn.execute(
                "UPDATE complaints SET issue = ?, message = ? WHERE id = ?",
                (new_issue, new_msg, complaint_id),
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ append_complaint_issue: {e}")
            import traceback

            traceback.print_exc()
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
                       resolved_at
                FROM complaints WHERE id = ?
                """,
                (complaint_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            print(f"❌ get_complaint_row: {e}")
            return None
        finally:
            conn.close()

    def update_complaint_type(self, complaint_id: int, complaint_type: str) -> bool:
        """تحديث تصنيف الشكوى (مثلاً بعد إلحاق نص جديد وإعادة التصنيف)."""
        ct = (complaint_type or "unspecified").strip() or "unspecified"
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE complaints SET complaint_type = ? WHERE id = ?",
                (ct, complaint_id),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"❌ update_complaint_type: {e}")
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
                    UPDATE complaints SET status = ?, resolved_at = datetime('now')
                    WHERE id = ?
                    """,
                    (st, complaint_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE complaints SET status = ?, resolved_at = NULL
                    WHERE id = ?
                    """,
                    (st, complaint_id),
                )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            return False
        finally:
            conn.close()

    def resolve_complaint(self, complaint_id: int) -> bool:
        """تعليم الشكوى كمحلولة مع طابع وقت التحديد."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                UPDATE complaints
                SET status = 'resolved', resolved_at = datetime('now')
                WHERE id = ?
                  AND LOWER(TRIM(COALESCE(status,''))) != 'resolved'
                """,
                (complaint_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"❌ resolve_complaint: {e}")
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
                q += " AND branch_id = ?"
                params.append(branch_id)
            if branch_name:
                bn = branch_name.strip()
                q += " AND TRIM(COALESCE(branch_name,'')) = ?"
                params.append(bn)
            if status:
                st = (status.strip()).lower()
                if st == "open":
                    q += " AND LOWER(TRIM(COALESCE(status,''))) != 'resolved'"
                elif st == "resolved":
                    q += " AND LOWER(TRIM(COALESCE(status,''))) = 'resolved'"
            q += " ORDER BY datetime(COALESCE(created_at, '1970-01-01')) DESC LIMIT ?"
            params.append(limit)
            cursor = conn.execute(q, tuple(params))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()


