# -*- coding: utf-8 -*-
"""
مستودع استفسارات الفروع — عمليات قاعدة البيانات.
يُضاف كـ Mixin إلى DatabaseManager.

جدول branch_inquiries:
- يُنشأ تلقائياً إذا لم يكن موجوداً عند بدء التطبيق.
- يخزّن استفسارات العملاء عن منتجات غير مسجلة.
- يتيح للفرع الرد مع نص + سعر + صورة اختيارية.

التحديثات في هذا الإصدار:
- إعادة كتابة _ensure_inquiry_table() ليعمل على PostgreSQL أيضاً
  (السابق كان يستخدم executescript الذي لا يعمل على PG ويستخدم AUTOINCREMENT
  بدل SERIAL). الآن يستخدم translate_ddl المركزي.
- إضافة _safe_rollback_pg() واستدعائها في كل دالة عند الفشل.
- نفس الواجهة العامة محفوظة بالكامل.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from logic.db_adapter import DBAdapter
from logic.sql_translator import translate_ddl

logger = logging.getLogger(__name__)

# DDL أصلي بصياغة SQLite — يُترجَم تلقائياً عبر translate_ddl.
# (SERIAL ↔ AUTOINCREMENT حسب نوع قاعدة البيانات)
_CREATE_TABLE_SQL_RAW = """
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
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    replied_at          TIMESTAMP
)
"""


class BranchInquiryRepositoryMixin:
    def _db_adapter(self) -> DBAdapter:
        return DBAdapter(sqlite_path=getattr(self, "db_path", None))

    def _safe_rollback_pg(self, conn) -> None:
        """rollback آمن للـ PostgreSQL فقط."""
        if getattr(self, "db_type", None) != "postgres":
            return
        try:
            conn.rollback()
        except Exception as e:
            logger.warning("rollback failed in branch_inquiry_repository: %s", e)

    """
    يُضاف إلى DatabaseManager مثل باقي الـ Mixins.
    يعتمد على self._get_connection() الموجودة في DatabaseManager.
    """

    def _ensure_inquiry_table(self) -> None:
        """
        ينشئ الجدول إذا لم يكن موجوداً (يُستدعى من _init_db).

        التحديث: يدعم PostgreSQL عبر translate_ddl ويستخدم _exec_ddl
        من DatabaseManager إن كان متوفراً (آمن transaction-wise).
        """
        db_type = getattr(self, "db_type", None) or "sqlite"
        ddl = translate_ddl(_CREATE_TABLE_SQL_RAW, db_type)
        conn = self._get_connection()
        try:
            # نفضل _exec_ddl إذا كان متاحاً (يستخدم SAVEPOINT آمن)
            exec_ddl = getattr(self, "_exec_ddl", None)
            if callable(exec_ddl):
                exec_ddl(conn, ddl)
                conn.commit()
            else:
                # مسار احتياطي
                conn.execute(ddl)
                conn.commit()
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("branch_inquiry: failed to create table: %s", e)
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
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
            self._safe_rollback_pg(conn)
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
                SET branch_reply      = %s,
                    branch_price      = %s,
                    branch_image_path = %s,
                    status            = 'answered',
                    replied_at        = CURRENT_TIMESTAMP
                WHERE id = %s
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
            self._safe_rollback_pg(conn)
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
                    WHERE branch_name = %s AND status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (str(branch_name), str(status), max(1, int(limit))),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT * FROM branch_inquiries
                    WHERE branch_name = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (str(branch_name), max(1, int(limit))),
                )
            return [dict(row) for row in cur.fetchall()]
        except Exception:
            self._safe_rollback_pg(conn)
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
                LIMIT %s
                """,
                (max(1, int(limit)),),
            )
            return [dict(row) for row in cur.fetchall()]
        except Exception:
            self._safe_rollback_pg(conn)
            logger.exception("branch_inquiry: get_all_pending failed")
            return []
        finally:
            conn.close()

    def get_inquiry_by_id(self, inquiry_id: int) -> Optional[Dict[str, Any]]:
        try:
            return self._db_adapter().fetch_one(
                "SELECT * FROM branch_inquiries WHERE id = %s",
                (int(inquiry_id),),
            )
        except Exception:
            logger.exception("branch_inquiry: get_by_id failed")
            return None

    def get_inquiry_reply_for_session(
        self, session_id: str, inquiry_id: int
    ) -> Optional[Dict[str, Any]]:
        """يجلب رد الفرع على استفسار معين لجلسة معينة (لإعادته للعميل)."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT * FROM branch_inquiries
                WHERE id = %s AND session_id = %s AND status = 'answered'
                """,
                (int(inquiry_id), str(session_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            self._safe_rollback_pg(conn)
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
                WHERE branch_name = %s AND status = 'pending'
                """,
                (str(branch_name),),
            )
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            self._safe_rollback_pg(conn)
            return 0
        finally:
            conn.close()

    def summarize_inquiries_by_branch(self) -> List[Dict[str, Any]]:
        """
        أعداد استفسارات «منتج غير مسجّل» حسب اسم الفرع — للتقارير (لوحة المؤسس).
        """
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT
                    branch_name,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_cnt,
                    SUM(CASE WHEN status = 'answered' THEN 1 ELSE 0 END) AS answered_cnt,
                    COUNT(*) AS total_cnt
                FROM branch_inquiries
                GROUP BY branch_name
                ORDER BY branch_name ASC
                """,
                (),
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                bn = r.get("branch_name")
                if bn is None or not str(bn).strip():
                    r["branch_name"] = "— غير محدد —"
                r["pending_cnt"] = int(r.get("pending_cnt") or 0)
                r["answered_cnt"] = int(r.get("answered_cnt") or 0)
                r["total_cnt"] = int(r.get("total_cnt") or 0)
            return rows
        except Exception:
            self._safe_rollback_pg(conn)
            logger.exception("branch_inquiry: summarize_inquiries_by_branch failed")
            return []
        finally:
            conn.close()

    def list_recent_branch_inquiries_all(self, limit: int = 80) -> List[Dict[str, Any]]:
        """أحدث استفسارات المنتجات غير المسجّلة — كل الفروع (لوحة الإدارة)."""
        limit = max(1, min(int(limit), 300))
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT * FROM branch_inquiries
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        except Exception:
            self._safe_rollback_pg(conn)
            logger.exception("branch_inquiry: list_recent_branch_inquiries_all failed")
            return []
        finally:
            conn.close()