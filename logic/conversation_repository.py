# -*- coding: utf-8 -*-
"""
ذاكرة المحادثة — Mixin يُضاف إلى DatabaseManager.

التحديثات في هذا الإصدار:
- إضافة rollback() صريح في save_chat_message عند فشل INSERT على PostgreSQL.
- إصلاح subquery في get_chat_history: إضافة `AS sub` (لازم في PostgreSQL).
- إصلاح إرجاع 0 في purge_old_chat_history عند الفشل (بدل None).
- نفس الواجهة العامة محفوظة بالكامل.
"""
from __future__ import annotations

import logging
from typing import List
from logic.db_adapter import DBAdapter

logger = logging.getLogger(__name__)


class ConversationRepositoryMixin:
    def _db_adapter(self) -> DBAdapter:
        return DBAdapter(sqlite_path=getattr(self, "db_path", None))

    """
    يُضاف إلى DatabaseManager مثل باقي الـ Mixins.
    يعتمد على self._get_connection() الموجودة في DatabaseManager.
    """

    def save_chat_message(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: str = "",
    ) -> bool:
        if not session_id or not role or not content:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO conversation_history
                    (session_id, role, content, intent)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    str(session_id)[:256],
                    str(role)[:16],
                    str(content)[:4000],
                    str(intent or "")[:64],
                ),
            )
            conn.commit()
            return True
        except Exception:
            # rollback صريح لتجنّب InFailedSqlTransaction على PostgreSQL
            try:
                if getattr(self, "db_type", None) == "postgres":
                    conn.rollback()
            except Exception:
                pass
            logger.exception("save_chat_message failed")
            return False
        finally:
            conn.close()

    def get_chat_history(
        self,
        session_id: str,
        limit: int = 10,
    ) -> List[dict]:
        if not session_id:
            return []
        conn = self._get_connection()
        try:
            # ملاحظة: PostgreSQL يلزم alias صريح للـ subquery (AS sub)،
            # SQLite يقبلها بدون alias. الكتابة هنا متوافقة مع الاثنين.
            cur = conn.execute(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at
                    FROM conversation_history
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ) AS sub
                ORDER BY created_at ASC
                """,
                (str(session_id), max(1, int(limit))),
            )
            return [{"role": row["role"], "content": row["content"]} for row in cur.fetchall()]
        except Exception:
            try:
                if getattr(self, "db_type", None) == "postgres":
                    conn.rollback()
            except Exception:
                pass
            logger.exception("get_chat_history failed")
            return []
        finally:
            conn.close()

    def purge_old_chat_history(self, days: int = 14) -> int:
        days = max(1, int(days))
        try:
            if self._db_adapter().db_type == "postgres":
                sql = (
                    "DELETE FROM conversation_history "
                    "WHERE created_at < CURRENT_TIMESTAMP + (%s::interval)"
                )
                params = (f"-{int(days)} days",)
            else:
                sql = (
                    "DELETE FROM conversation_history "
                    "WHERE created_at < datetime('now', %s)"
                )
                params = (f"-{int(days)} days",)
            return self._db_adapter().execute(sql, params)
        except Exception:
            logger.exception("purge_old_chat_history failed")
            return 0