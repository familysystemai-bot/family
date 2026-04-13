# -*- coding: utf-8 -*-
"""
ذاكرة المحادثة — مزيج يُضاف إلى DatabaseManager.
يحفظ كل رسالة في جدول مستقل ويعيد آخر N رسالة مرتبة زمنياً.

المسار: logic/conversation_repository.py  (ملف جديد كامل)
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


class ConversationRepositoryMixin:
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
                VALUES (?, ?, ?, ?)
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
            cur = conn.execute(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at
                    FROM conversation_history
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC
                """,
                (str(session_id), max(1, int(limit))),
            )
            return [{"role": row["role"], "content": row["content"]} for row in cur.fetchall()]
        except Exception:
            logger.exception("get_chat_history failed")
            return []
        finally:
            conn.close()

    def purge_old_chat_history(self, days: int = 14) -> int:
        days = max(1, int(days))
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                DELETE FROM conversation_history
                WHERE created_at < datetime('now', ? || ' days')
                """,
                (f"-{days}",),
            )
            conn.commit()
            return cur.rowcount or 0
        except Exception:
            logger.exception("purge_old_chat_history failed")
            return 0
        finally:
            conn.close()