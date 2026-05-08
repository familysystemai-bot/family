# -*- coding: utf-8 -*-
"""
CustomerRepositoryMixin — جدول customers.

التحديثات في هذا الإصدار:
- إضافة rollback() صريح في كل دالة CRUD (UPDATE/INSERT) عند فشل العملية.
  هذا ضروري لأن أي فشل في PostgreSQL يضع الاتصال في حالة InFailedSqlTransaction
  مما يُسقط أي استعلام لاحق على نفس الاتصال (حتى لو كان صحيحاً).
- نفس الواجهة العامة محفوظة بالكامل، نفس التواقيع، نفس الإرجاعات.
- لا تغيير في أي منطق أعمال.
"""
from __future__ import annotations

import json
import re
import logging
from typing import Any, Dict, List, Optional
from logic.db_adapter import DBAdapter

logger = logging.getLogger(__name__)


class CustomerRepositoryMixin:
    """جدول customers — يُدمج في DatabaseManager."""

    def _db_adapter(self) -> DBAdapter:
        return DBAdapter(sqlite_path=getattr(self, "db_path", None))

    def _safe_rollback_pg(self, conn) -> None:
        """rollback آمن للـ PostgreSQL فقط."""
        if getattr(self, "db_type", None) != "postgres":
            return
        try:
            conn.rollback()
        except Exception as e:
            logger.warning("rollback failed in customer_repository: %s", e)

    @staticmethod
    def _normalize_customer_email(email: Optional[str]) -> Optional[str]:
        em = (email or "").strip().lower()
        if not em or "@" not in em:
            return None
        return em[:200]

    @staticmethod
    def _normalize_customer_phone(phone: Optional[str]) -> Optional[str]:
        digits = re.sub(r"\D+", "", phone or "")
        if len(digits) < 8:
            return None
        return digits[:40]

    def customer_has_saved_chat_history(self, customer_id: int) -> bool:
        """هل يوجد سجل محادثة محفوظ لعميل مربوط ببريد/جوال (جدول clients)."""
        uid = f"customer:{int(customer_id)}"
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT chat_history FROM clients WHERE user_id = %s", (uid,)
            )
            row = cur.fetchone()
            if not row:
                return False
            raw = (row["chat_history"] or "").strip()
            if not raw:
                return False
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return len(raw) > 4
            return isinstance(data, list) and len(data) > 0
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("customer_has_saved_chat_history: %s", e)
            return False
        finally:
            conn.close()

    def get_customer_by_id(self, customer_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT * FROM customers WHERE id = %s", (int(customer_id),))
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            merged_into = data.get("merged_into_id")
            if int(data.get("is_active") or 0) == 0 and merged_into:
                cur = conn.execute("SELECT * FROM customers WHERE id = %s", (int(merged_into),))
                target = cur.fetchone()
                return dict(target) if target else data
            return data
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_customer_by_id: %s", e)
            return None
        finally:
            conn.close()

    def get_customer_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        em = self._normalize_customer_email(email)
        if not em:
            return None
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT * FROM customers
                WHERE email = %s
                ORDER BY is_active DESC, id ASC
                LIMIT 1
                """,
                (em,),
            )
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            merged_into = data.get("merged_into_id")
            if int(data.get("is_active") or 0) == 0 and merged_into:
                cur = conn.execute("SELECT * FROM customers WHERE id = %s", (int(merged_into),))
                target = cur.fetchone()
                return dict(target) if target else data
            return data
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_customer_by_email: %s", e)
            return None
        finally:
            conn.close()

    def get_customer_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        ph = self._normalize_customer_phone(phone)
        if not ph:
            return None
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT * FROM customers
                WHERE phone = %s
                ORDER BY is_active DESC, id ASC
                LIMIT 1
                """,
                (ph,),
            )
            row = cur.fetchone()
            if not row:
                return None
            data = dict(row)
            merged_into = data.get("merged_into_id")
            if int(data.get("is_active") or 0) == 0 and merged_into:
                cur = conn.execute("SELECT * FROM customers WHERE id = %s", (int(merged_into),))
                target = cur.fetchone()
                return dict(target) if target else data
            return data
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_customer_by_phone: %s", e)
            return None
        finally:
            conn.close()

    def _update_customer_identity_fields(
        self,
        customer_id: int,
        *,
        name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        branch_id: Optional[int] = None,
    ) -> bool:
        sets: list[str] = []
        args: list[Any] = []
        nm = (name or "").strip()[:200]
        em = self._normalize_customer_email(email)
        ph = self._normalize_customer_phone(phone)
        if nm:
            sets.append("name = %s")
            args.append(nm)
        if em:
            sets.append("email = %s")
            args.append(em)
        if ph:
            sets.append("phone = %s")
            args.append(ph)
        if branch_id is not None:
            sets.append("branch_id = %s")
            args.append(int(branch_id))
        if not sets:
            return True
        args.append(int(customer_id))
        conn = self._get_connection()
        try:
            conn.execute(
                f"UPDATE customers SET {', '.join(sets)} WHERE id = %s",
                tuple(args),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("_update_customer_identity_fields: %s", e)
            return False
        finally:
            conn.close()

    def _merge_clients_chat_rows(self, conn: Any, keep_id: int, drop_id: int) -> None:
        """
        إن وُجدت صفوف clients بمفتاح user_id = customer:<id>، توحيدها مع الحفاظ على chat_history.
        الجلسات العادية تستخدم web_user_* ولا تُمس هنا.
        """
        old_uid = f"customer:{int(drop_id)}"
        new_uid = f"customer:{int(keep_id)}"
        cur = conn.execute(
            "SELECT user_id, chat_history, complaint_draft, name, phone, dialect, last_intent FROM clients WHERE user_id IN (%s, %s)",
            (old_uid, new_uid),
        )
        rows = {str(r["user_id"]): dict(r) for r in cur.fetchall()}
        old_row = rows.get(old_uid)
        new_row = rows.get(new_uid)
        if not old_row:
            return
        if not new_row:
            conn.execute("UPDATE clients SET user_id = %s WHERE user_id = %s", (new_uid, old_uid))
            return

        def _as_history_list(raw: Any) -> List[Any]:
            try:
                parsed = json.loads(raw if isinstance(raw, str) else "[]")
            except (json.JSONDecodeError, TypeError):
                return []
            return parsed if isinstance(parsed, list) else []

        merged_hist = _as_history_list(new_row.get("chat_history")) + _as_history_list(old_row.get("chat_history"))
        hist_text = json.dumps(merged_hist, ensure_ascii=False)
        d_new = (new_row.get("complaint_draft") or "").strip()
        d_old = (old_row.get("complaint_draft") or "").strip()
        merged_draft = d_new if len(d_new) >= len(d_old) else d_old
        if d_new and d_old and d_new != d_old:
            merged_draft = f"{d_new}\n\n{d_old}"
        nm = (new_row.get("name") or "").strip() or (old_row.get("name") or "").strip()
        ph = (new_row.get("phone") or "").strip() or (old_row.get("phone") or "").strip()
        dia = (new_row.get("dialect") or "").strip() or (old_row.get("dialect") or "").strip() or "saudi"
        li = (new_row.get("last_intent") or "").strip() or (old_row.get("last_intent") or "").strip() or "GREETING"
        conn.execute(
            """
            UPDATE clients
            SET chat_history = %s, complaint_draft = %s, name = %s, phone = %s,
                dialect = %s, last_intent = %s
            WHERE user_id = %s
            """,
            (hist_text, merged_draft, nm[:200] if nm else "", ph[:80] if ph else "", dia[:40], li[:80], new_uid),
        )
        conn.execute("DELETE FROM clients WHERE user_id = %s", (old_uid,))

    def _merge_customer_records(self, keep_id: int, drop_id: int) -> bool:
        if int(keep_id) == int(drop_id):
            return True
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM customers WHERE id IN (%s, %s)",
                (int(keep_id), int(drop_id)),
            )
            rows = {int(r["id"]): dict(r) for r in cur.fetchall()}
            keep = rows.get(int(keep_id))
            drop = rows.get(int(drop_id))
            if not keep or not drop:
                return False
            merged_name = (keep.get("name") or "").strip() or (drop.get("name") or "").strip() or "ضيف"
            merged_email = (keep.get("email") or "").strip() or (drop.get("email") or "").strip() or None
            merged_phone = (keep.get("phone") or "").strip() or (drop.get("phone") or "").strip() or None
            merged_branch = keep.get("branch_id") if keep.get("branch_id") is not None else drop.get("branch_id")
            merged_prefers = 1 if int(keep.get("prefers_marketing") or 0) or int(drop.get("prefers_marketing") or 0) else 0
            merged_declined = 1 if int(keep.get("declined_marketing_prompt") or 0) or int(drop.get("declined_marketing_prompt") or 0) else 0
            merged_dialect = (keep.get("dialect") or "").strip() or (drop.get("dialect") or "").strip() or "default"
            merged_interest = (keep.get("last_product_interest") or "").strip() or (drop.get("last_product_interest") or "").strip() or None
            merged_interest_at = (keep.get("last_product_interest_at") or "").strip() or (drop.get("last_product_interest_at") or "").strip() or None
            merged_campaign_at = (keep.get("last_campaign_sent_at") or "").strip() or (drop.get("last_campaign_sent_at") or "").strip() or None

            conn.execute(
                """
                UPDATE customers
                SET name = %s, email = %s, phone = %s, branch_id = %s, prefers_marketing = %s,
                    dialect = %s, last_product_interest = %s, last_product_interest_at = %s,
                    last_campaign_sent_at = %s, declined_marketing_prompt = %s
                WHERE id = %s
                """,
                (
                    merged_name[:200],
                    self._normalize_customer_email(merged_email),
                    self._normalize_customer_phone(merged_phone),
                    int(merged_branch) if merged_branch is not None else None,
                    merged_prefers,
                    merged_dialect[:40],
                    merged_interest[:500] if merged_interest else None,
                    merged_interest_at or None,
                    merged_campaign_at or None,
                    merged_declined,
                    int(keep_id),
                ),
            )
            conn.execute(
                """
                UPDATE complaints
                SET user_id = %s
                WHERE user_id = %s
                """,
                (f"customer:{int(keep_id)}", f"customer:{int(drop_id)}"),
            )
            conn.execute(
                """
                UPDATE product_requests
                SET user_id = %s
                WHERE user_id = %s
                """,
                (f"customer:{int(keep_id)}", f"customer:{int(drop_id)}"),
            )
            conn.execute(
                """
                UPDATE image_analysis
                SET user_id = %s
                WHERE user_id = %s
                """,
                (f"customer:{int(keep_id)}", f"customer:{int(drop_id)}"),
            )
            self._merge_clients_chat_rows(conn, int(keep_id), int(drop_id))
            conn.execute(
                """
                INSERT INTO customer_merge_audit (source_customer_id, target_customer_id)
                VALUES (%s, %s)
                """,
                (int(drop_id), int(keep_id)),
            )
            conn.execute(
                """
                UPDATE customers
                SET is_active = 0, merged_into_id = %s, email = NULL, phone = NULL
                WHERE id = %s
                """,
                (int(keep_id), int(drop_id)),
            )
            conn.commit()
            logger.info(
                "customer merge completed: source_customer_id=%s target_customer_id=%s",
                int(drop_id),
                int(keep_id),
            )
            return True
        except Exception as e:
            # rollback صريح (موجود أصلاً، نتركه — ونضيف log)
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("_merge_customer_records: %s", e)
            return False
        finally:
            conn.close()

    def get_or_create_customer(
        self,
        *,
        name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        branch_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        nm = (name or "").strip()[:200] or "ضيف"
        em = self._normalize_customer_email(email)
        ph = self._normalize_customer_phone(phone)
        if not em and not ph:
            return None

        email_row = self.get_customer_by_email(em) if em else None
        phone_row = self.get_customer_by_phone(ph) if ph else None

        if email_row and phone_row and int(email_row["id"]) != int(phone_row["id"]):
            if not self._merge_customer_records(int(email_row["id"]), int(phone_row["id"])):
                return None
            email_row = self.get_customer_by_id(int(email_row["id"]))
            phone_row = email_row

        row = email_row or phone_row
        if row:
            if not self._update_customer_identity_fields(
                int(row["id"]),
                name=nm,
                email=em,
                phone=ph,
                branch_id=branch_id,
            ):
                return None
            return self.get_customer_by_id(int(row["id"]))

        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO customers (name, email, phone, branch_id, prefers_marketing, created_at)
                VALUES (%s, %s, %s, %s, 0, CURRENT_TIMESTAMP)
                """,
                (nm, em, ph, branch_id),
            )
            conn.commit()
            return self.get_customer_by_id(int(cur.lastrowid))
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_or_create_customer (INSERT): %s", e)
            return None
        finally:
            conn.close()

    def customer_decline_marketing_prompt(self, customer_id: int) -> bool:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET declined_marketing_prompt = 1 WHERE id = %s",
                (int(customer_id),),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("customer_decline_marketing_prompt: %s", e)
            return False
        finally:
            conn.close()

    def customer_ensure_by_email(
        self,
        name: str,
        email: str,
        branch_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        return self.get_or_create_customer(name=name, email=email, branch_id=branch_id)

    def customer_set_prefers_marketing(self, customer_id: int, value: bool) -> bool:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET prefers_marketing = %s WHERE id = %s",
                (1 if value else 0, int(customer_id)),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("customer_set_prefers_marketing: %s", e)
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
                "UPDATE customers SET phone = %s WHERE id = %s",
                (p, int(customer_id)),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("customer_set_phone: %s", e)
            return False
        finally:
            conn.close()

    def customer_set_branch(self, customer_id: int, branch_id: Optional[int]) -> bool:
        if branch_id is None:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET branch_id = %s WHERE id = %s",
                (int(branch_id), int(customer_id)),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("customer_set_branch: %s", e)
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
            sets.append("dialect = %s")
            args.append(d)
        if (product_label or "").strip():
            sets.append("last_product_interest = %s")
            sets.append("last_product_interest_at = CURRENT_TIMESTAMP")
            args.append(str(product_label).strip()[:500])
        if not sets:
            return True
        args.append(int(customer_id))
        conn = self._get_connection()
        try:
            conn.execute(
                f"UPDATE customers SET {', '.join(sets)} WHERE id = %s",
                tuple(args),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("customer_touch_engagement: %s", e)
            return False
        finally:
            conn.close()

    def customer_mark_last_campaign_sent(self, customer_id: int) -> bool:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE customers SET last_campaign_sent_at = CURRENT_TIMESTAMP WHERE id = %s",
                (int(customer_id),),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("customer_mark_last_campaign_sent: %s", e)
            return False
        finally:
            conn.close()