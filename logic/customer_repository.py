# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional


class CustomerRepositoryMixin:
    """جدول customers — يُدمج في DatabaseManager."""

    def get_customer_by_id(self, customer_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM customers WHERE id = ?", (int(customer_id),))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_customer_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        em = (email or "").strip().lower()
        if not em:
            return None
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM customers WHERE email = ?", (em,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def customer_decline_marketing_prompt(self, customer_id: int) -> bool:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET declined_marketing_prompt = 1 WHERE id = ?",
                (int(customer_id),),
            )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    def customer_ensure_by_email(
        self,
        name: str,
        email: str,
        branch_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            return None
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO customers (name, email, branch_id, prefers_marketing, created_at)
                VALUES (?, ?, ?, 0, datetime('now'))
                ON CONFLICT(email) DO UPDATE SET
                    name = excluded.name,
                    branch_id = COALESCE(excluded.branch_id, customers.branch_id)
                """,
                ((name or "").strip()[:200] or "ضيف", email, branch_id),
            )
            conn.commit()
            cur.execute("SELECT * FROM customers WHERE email = ?", (email,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def customer_set_prefers_marketing(self, customer_id: int, value: bool) -> bool:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET prefers_marketing = ? WHERE id = ?",
                (1 if value else 0, int(customer_id)),
            )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    def customer_set_phone(self, customer_id: int, phone: Optional[str]) -> bool:
        p = (phone or "").strip()[:40]
        if not p:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET phone = ? WHERE id = ?",
                (p, int(customer_id)),
            )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    def customer_set_branch(self, customer_id: int, branch_id: Optional[int]) -> bool:
        if branch_id is None:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET branch_id = ? WHERE id = ?",
                (int(branch_id), int(customer_id)),
            )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    def customer_touch_engagement(
        self,
        customer_id: int,
        dialect: Optional[str] = None,
        product_label: Optional[str] = None,
    ) -> bool:
        """تحديث اللهجة وآخر اهتمام بمنتج (بدون كسر الحقول الموجودة)."""
        if dialect is None and not (product_label or "").strip():
            return True
        sets: list[str] = []
        args: list[Any] = []
        if dialect is not None:
            d = str(dialect).strip()[:40] or "default"
            sets.append("dialect = ?")
            args.append(d)
        if (product_label or "").strip():
            sets.append("last_product_interest = ?")
            sets.append("last_product_interest_at = datetime('now')")
            args.append(str(product_label).strip()[:500])
        if not sets:
            return True
        args.append(int(customer_id))
        conn = self._get_connection()
        try:
            conn.execute(
                f"UPDATE customers SET {', '.join(sets)} WHERE id = ?",
                tuple(args),
            )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    def customer_mark_last_campaign_sent(self, customer_id: int) -> bool:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET last_campaign_sent_at = datetime('now') WHERE id = ?",
                (int(customer_id),),
            )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()
