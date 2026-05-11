# -*- coding: utf-8 -*-
"""
وارد واتساب — جدول messages (صندوق المحادثات في لوحة التحكم).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def normalize_wa_contact_number(raw: str) -> str:
    """أرقام فقط كما يُخزَّن في customers.phone."""
    d = re.sub(r"\D+", "", raw or "")
    return d[:40] if d else ""


# رد آلي لواتساب لمن هو في قائمة الحظر — بدون استدعاء الذكاء الاصطناعي (توفير توكنز)
WA_BLOCKED_AI_AUTOREPLY_AR = (
    "عذراً، يبدو أنك تستمتع بإرسال رسائل فارغة، لقد قرر النظام أخذ استراحة منك! "
    "سيتم الرد عليك يدوياً عند توفر الموظف."
)


class WaInboxRepositoryMixin:
    """جدول messages — يُدمج في DatabaseManager."""

    def wa_inbox_save_message(
        self,
        *,
        contact_number: str,
        whatsapp_name: str,
        message_body: str,
        direction: str,
        branch_id: Optional[int],
        sender_type: str = "",
    ) -> Optional[int]:
        cn = normalize_wa_contact_number(contact_number)
        if not cn or len(cn) < 8:
            logger.warning(
                "wa_inbox_save_message: رقم غير صالح للصندوق (يلزم ≥8 أرقام بعد التطبيع): %r",
                (contact_number or "")[:64],
            )
            return None
        d = (direction or "").strip().lower()
        if d not in ("inbound", "outbound"):
            logger.warning(
                "wa_inbox_save_message: اتجاه غير صالح %r (inbound|outbound)",
                (direction or "")[:32],
            )
            return None
        name = (whatsapp_name or "").strip()[:200]
        body = (message_body or "").strip()
        if not body:
            body = "—"
        stype = (sender_type or "").strip()[:32]
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO messages
                (contact_number, whatsapp_name, message_body, direction, msg_timestamp, branch_id, sender_type)
                VALUES (%s, %s, %s, %s, datetime('now'), %s, %s)
                """,
                (cn, name, body[:50000], d, branch_id, stype),
            )
            conn.commit()
            lid = getattr(cur, "lastrowid", None)
            if lid is not None:
                try:
                    return int(lid)
                except (TypeError, ValueError):
                    pass
            return 0
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_inbox_save_message: %s", e)
            return None
        finally:
            conn.close()

    # ─── تحكم جلسات واتساب (إيقاف AI / حظر) ──────────────────────

    def wa_contact_get_controls(self, contact_number: str) -> Dict[str, Any]:
        """يُعيد {'ai_stopped': 0|1, 'banned': 0|1} للرقم المحدد."""
        cn = normalize_wa_contact_number(contact_number)
        default: Dict[str, Any] = {"ai_stopped": 0, "banned": 0}
        if not cn:
            return default
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT ai_stopped, banned FROM wa_contact_controls WHERE contact_number = %s",
                (cn,),
            )
            row = cur.fetchone()
            if row:
                return {"ai_stopped": int(row["ai_stopped"] or 0), "banned": int(row["banned"] or 0)}
            return default
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_contact_get_controls: %s", e)
            return default
        finally:
            conn.close()

    def wa_contact_set_control(self, contact_number: str, field: str, value: int) -> bool:
        """يضبط ai_stopped أو banned للرقم المحدد."""
        import datetime as _dt
        cn = normalize_wa_contact_number(contact_number)
        if not cn or field not in ("ai_stopped", "banned"):
            return False
        if len(cn) < 8:
            logger.warning(
                "wa_contact_set_control: رقم قصير جداً بعد التطبيع (يلزم ≥8): %s",
                (contact_number or "")[:32],
            )
            return False
        v = 1 if value else 0
        # نمرر التاريخ كـ string من Python لتجنب عدم توافق الأنواع بين
        # timestamp وعمود TEXT في PostgreSQL (NOW() يعيد timestamp لا text)
        now_str = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT ai_stopped, banned FROM wa_contact_controls WHERE contact_number = %s",
                (cn,),
            )
            prow = cur.fetchone()
            prev_a = int(prow["ai_stopped"] or 0) if prow else 0
            prev_b = int(prow["banned"] or 0) if prow else 0
            if field == "ai_stopped":
                prev_a = v
            else:
                prev_b = v
            # صف واحد ضمن Upsert يضمن نجاح التحديث حتى لو فشلت أنماط INSERT+UPDATE السابقة
            cur.execute(
                """
                INSERT INTO wa_contact_controls (contact_number, ai_stopped, banned, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (contact_number) DO UPDATE SET
                    ai_stopped = excluded.ai_stopped,
                    banned = excluded.banned,
                    updated_at = excluded.updated_at
                """,
                (cn, prev_a, prev_b, now_str),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_contact_set_control: %s", e)
            return False
        finally:
            conn.close()

    def wa_inbox_infer_branch_for_contact(self, contact_number: str) -> Optional[int]:
        cn = normalize_wa_contact_number(contact_number)
        if not cn:
            return None
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT branch_id FROM messages
                WHERE contact_number = %s AND branch_id IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (cn,),
            )
            row = cur.fetchone()
            if row and row["branch_id"] is not None:
                return int(row["branch_id"])
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_inbox_infer_branch_for_contact: %s", e)
            return None
        finally:
            conn.close()
        return None

    def wa_inbox_list_threads(self, branch_id: Optional[int]) -> List[Dict[str, Any]]:
        """
        قائمة المحادثات: branch_id محدد = فقط رسائل ذلك الفرع؛ None = الإدارة (الكل).
        """
        conn = self._get_connection()
        try:
            if branch_id is None:
                cur = conn.execute(
                    """
                    SELECT m.contact_number, m.whatsapp_name, m.message_body AS last_body,
                           m.msg_timestamp AS timestamp, m.direction AS last_direction
                    FROM messages m
                    INNER JOIN (
                        SELECT contact_number, MAX(id) AS mid
                        FROM messages
                        GROUP BY contact_number
                    ) t ON m.id = t.mid
                    ORDER BY m.msg_timestamp DESC, m.id DESC
                    """
                )
            else:
                bid = int(branch_id)
                cur = conn.execute(
                    """
                    SELECT m.contact_number, m.whatsapp_name, m.message_body AS last_body,
                           m.msg_timestamp AS timestamp, m.direction AS last_direction
                    FROM messages m
                    INNER JOIN (
                        SELECT contact_number, MAX(id) AS mid
                        FROM messages
                        WHERE branch_id = %s OR branch_id IS NULL
                        GROUP BY contact_number
                        HAVING SUM(CASE WHEN branch_id = %s THEN 1 ELSE 0 END) > 0
                    ) t ON m.id = t.mid
                    ORDER BY m.msg_timestamp DESC, m.id DESC
                    """,
                    (bid, bid),
                )
            return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_inbox_list_threads: %s", e)
            return []
        finally:
            conn.close()

    def wa_inbox_list_messages(
        self,
        contact_number: str,
        *,
        branch_id: Optional[int],
    ) -> List[Dict[str, Any]]:
        cn = normalize_wa_contact_number(contact_number)
        if not cn:
            return []
        conn = self._get_connection()
        try:
            if branch_id is None:
                cur = conn.execute(
                    """
                    SELECT id, contact_number, whatsapp_name, message_body, direction,
                           msg_timestamp AS timestamp, branch_id
                    FROM messages
                    WHERE contact_number = %s
                    ORDER BY id ASC
                    """,
                    (cn,),
                )
            else:
                bid = int(branch_id)
                cur = conn.execute(
                    """
                    SELECT id, contact_number, whatsapp_name, message_body, direction,
                           msg_timestamp AS timestamp, branch_id
                    FROM messages
                    WHERE contact_number = %s
                      AND (branch_id = %s OR branch_id IS NULL)
                    ORDER BY id ASC
                    """,
                    (cn, bid),
                )
            return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_inbox_list_messages: %s", e)
            return []
        finally:
            conn.close()

    def wa_inbox_latest_display_name(self, contact_number: str) -> str:
        cn = normalize_wa_contact_number(contact_number)
        if not cn:
            return ""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT whatsapp_name FROM messages
                WHERE contact_number = %s AND TRIM(COALESCE(whatsapp_name, '')) != ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (cn,),
            )
            row = cur.fetchone()
            if row:
                return str(row["whatsapp_name"] or "").strip()[:200]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_inbox_latest_display_name: %s", e)
            return ""
        finally:
            conn.close()
        return ""
