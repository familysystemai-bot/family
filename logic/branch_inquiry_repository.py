# -*- coding: utf-8 -*-
"""
مستودع استفسارات الفروع — عمليات قاعدة البيانات.
يُضاف كـ Mixin إلى DatabaseManager.

جدول branch_inquiries:
- يُنشأ تلقائياً إذا لم يكن موجوداً عند بدء التطبيق.
- يخزّن استفسارات العملاء عن منتجات غير مسجلة.
- يتيح للفرع الرد مع نص + سعر + صورة اختيارية.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS branch_inquiries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT    NOT NULL,
    customer_name       TEXT,
    customer_contact    TEXT,
    branch_name         TEXT,
    inquiry_text        TEXT    NOT NULL,
    category_hint       TEXT,
    customer_image_path TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending',
    branch_reply        TEXT,
    branch_image_path   TEXT,
    branch_price        TEXT,
    created_at          DATETIME DEFAULT (datetime('now')),
    replied_at          DATETIME
);
"""


class BranchInquiryRepositoryMixin:
    """
    يُضاف إلى DatabaseManager مثل باقي الـ Mixins.
    يعتمد على self._get_connection() الموجودة في DatabaseManager.
    """

    def _ensure_inquiry_table(self) -> None:
        """ينشئ الجدول إذا لم يكن موجوداً (يُستدعى من _init_db)."""
        conn = self._get_connection()
        try:
            conn.executescript(_CREATE_TABLE_SQL)
            conn.commit()
        except Exception:
            logger.exception("branch_inquiry: failed to create table")
        finally:
            conn.close()

    # ─── الكتابة ──────────────────────────────────────────────────────────

    def create_branch_inquiry(
        self,
        session_id: str,
        inquiry_text: str,
        customer_name: str = "",
        customer_contact: str = "",
        branch_name: str = "",
        category_hint: str = "",
        customer_image_path: str = "",
    ) -> Optional[int]:
        """
        ينشئ استفساراً جديداً. يعيد ID السجل أو None عند الفشل.
        """
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                INSERT INTO branch_inquiries
                    (session_id, customer_name, customer_contact,
                     branch_name, inquiry_text, category_hint,
                     customer_image_path, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    str(session_id)[:256],
                    str(customer_name or "")[:100],
                    str(customer_contact or "")[:100],
                    str(branch_name or "")[:100],
                    str(inquiry_text)[:1000],
                    str(category_hint or "")[:200],
                    str(customer_image_path or "")[:500],
                ),
            )
            conn.commit()
            return cur.lastrowid
        except Exception:
            logger.exception("branch_inquiry: create failed")
            return None
        finally:
            conn.close()

    def reply_to_inquiry(
        self,
        inquiry_id: int,
        branch_reply: str,
        branch_price: str = "",
        branch_image_path: str = "",
    ) -> bool:
        """يُضيف رد الفرع على الاستفسار ويضع الحالة 'answered'."""
        conn = self._get_connection()
        try:
            conn.execute(
                """
                UPDATE branch_inquiries
                SET branch_reply      = ?,
                    branch_price      = ?,
                    branch_image_path = ?,
                    status            = 'answered',
                    replied_at        = datetime('now')
                WHERE id = ?
                """,
                (
                    str(branch_reply)[:2000],
                    str(branch_price or "")[:100],
                    str(branch_image_path or "")[:500],
                    int(inquiry_id),
                ),
            )
            conn.commit()
            return True
        except Exception:
            logger.exception("branch_inquiry: reply failed")
            return False
        finally:
            conn.close()

    # ─── القراءة ──────────────────────────────────────────────────────────

    def get_branch_inquiries(
        self,
        branch_name: str,
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        يجلب استفسارات فرع معين.
        status: '' = الكل | 'pending' | 'answered'
        """
        conn = self._get_connection()
        try:
            if status:
                cur = conn.execute(
                    """
                    SELECT * FROM branch_inquiries
                    WHERE branch_name = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (str(branch_name), str(status), max(1, int(limit))),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT * FROM branch_inquiries
                    WHERE branch_name = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (str(branch_name), max(1, int(limit))),
                )
            return [dict(row) for row in cur.fetchall()]
        except Exception:
            logger.exception("branch_inquiry: get failed")
            return []
        finally:
            conn.close()

    def get_all_pending_inquiries(self, limit: int = 100) -> List[Dict[str, Any]]:
        """يجلب كل الاستفسارات المعلقة (للإدارة)."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT * FROM branch_inquiries
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
            return [dict(row) for row in cur.fetchall()]
        except Exception:
            logger.exception("branch_inquiry: get_all_pending failed")
            return []
        finally:
            conn.close()

    def get_inquiry_by_id(self, inquiry_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM branch_inquiries WHERE id = ?",
                (int(inquiry_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            logger.exception("branch_inquiry: get_by_id failed")
            return None
        finally:
            conn.close()

    def get_inquiry_reply_for_session(
        self, session_id: str, inquiry_id: int
    ) -> Optional[Dict[str, Any]]:
        """يجلب رد الفرع على استفسار معين لجلسة معينة (لإعادته للعميل)."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT * FROM branch_inquiries
                WHERE id = ? AND session_id = ? AND status = 'answered'
                """,
                (int(inquiry_id), str(session_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            logger.exception("branch_inquiry: get_reply_for_session failed")
            return None
        finally:
            conn.close()

    def count_pending_inquiries_for_branch(self, branch_name: str) -> int:
        """يعيد عدد الاستفسارات المعلقة للفرع (للشارة في لوحة التحكم)."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM branch_inquiries
                WHERE branch_name = ? AND status = 'pending'
                """,
                (str(branch_name),),
            )
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            return 0
        finally:
            conn.close()
