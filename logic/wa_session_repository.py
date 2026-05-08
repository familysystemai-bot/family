# -*- coding: utf-8 -*-
"""
جلسات واتساب وتتبّع WAMID — تخزين دائم في قاعدة البيانات.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class WaSessionRepositoryMixin:
    """جداول wa_sessions و wa_processed_wamids."""

    def wa_session_load(self, session_id: str) -> Optional[Tuple[float, Dict[str, Any]]]:
        """يُرجع (updated_at, state) أو None إن لم تُوجد جلسة."""
        sid = (session_id or "").strip()
        if not sid or len(sid) > 512:
            return None
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT state_json, updated_at FROM wa_sessions
                WHERE session_id = %s
                LIMIT 1
                """,
                (sid,),
            )
            row = cur.fetchone()
            if not row:
                return None
            raw = row["state_json"] if isinstance(row, dict) else row[0]
            ts = row["updated_at"] if isinstance(row, dict) else row[1]
            try:
                updated_at = float(ts or 0)
            except (TypeError, ValueError):
                updated_at = 0.0
            try:
                st = json.loads(raw or "{}")
            except json.JSONDecodeError:
                st = {}
            if not isinstance(st, dict):
                st = {}
            return updated_at, st
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_session_load: %s", e)
            return None
        finally:
            conn.close()

    def wa_session_save(self, session_id: str, updated_at: float, state: Dict[str, Any]) -> bool:
        sid = (session_id or "").strip()
        if not sid or len(sid) > 512:
            return False
        try:
            payload = json.dumps(dict(state or {}), ensure_ascii=False)
        except (TypeError, ValueError):
            payload = "{}"
        try:
            ts = float(updated_at)
        except (TypeError, ValueError):
            ts = time.time()
        conn = self._get_connection()
        try:
            if self.db_type == "postgres":
                conn.execute(
                    """
                    INSERT INTO wa_sessions (session_id, state_json, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        state_json = EXCLUDED.state_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (sid, payload, ts),
                )
            else:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wa_sessions (session_id, state_json, updated_at)
                    VALUES (%s, %s, %s)
                    """,
                    (sid, payload, ts),
                )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_session_save: %s", e)
            return False
        finally:
            conn.close()

    def wa_wamids_fetch_processed(
        self, wamids: List[str]
    ) -> Dict[str, float]:
        """wamid -> processed_at للصفوف الموجودة فقط."""
        out: Dict[str, float] = {}
        clean: List[str] = []
        for w in wamids or []:
            m = (w or "").strip()
            if m and m not in clean and len(m) <= 256:
                clean.append(m)
        if not clean:
            return out
        conn = self._get_connection()
        try:
            placeholders = ",".join(["%s"] * len(clean))
            cur = conn.execute(
                f"""
                SELECT wamid, processed_at FROM wa_processed_wamids
                WHERE wamid IN ({placeholders})
                """,
                tuple(clean),
            )
            for row in cur.fetchall():
                if isinstance(row, dict):
                    w = (row.get("wamid") or "").strip()
                    pt = row.get("processed_at")
                else:
                    w = (row[0] or "").strip()
                    pt = row[1]
                if not w:
                    continue
                try:
                    out[w] = float(pt or 0)
                except (TypeError, ValueError):
                    out[w] = 0.0
            return out
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_wamids_fetch_processed: %s", e)
            return out
        finally:
            conn.close()

    def wa_wamids_mark_processed(self, wamids: List[str], processed_at: float) -> None:
        clean: List[str] = []
        for w in wamids or []:
            m = (w or "").strip()
            if m and m not in clean and len(m) <= 256:
                clean.append(m)
        if not clean:
            return
        try:
            ts = float(processed_at)
        except (TypeError, ValueError):
            ts = time.time()
        conn = self._get_connection()
        try:
            if self.db_type == "postgres":
                for w in clean:
                    conn.execute(
                        """
                        INSERT INTO wa_processed_wamids (wamid, processed_at)
                        VALUES (%s, %s)
                        ON CONFLICT (wamid) DO UPDATE SET
                            processed_at = EXCLUDED.processed_at
                        """,
                        (w, ts),
                    )
            else:
                for w in clean:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO wa_processed_wamids (wamid, processed_at)
                        VALUES (%s, %s)
                        """,
                        (w, ts),
                    )
            conn.commit()
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_wamids_mark_processed: %s", e)
        finally:
            conn.close()

    def wa_wamids_delete_before(self, cutoff_ts: float) -> None:
        try:
            cut = float(cutoff_ts)
        except (TypeError, ValueError):
            return
        conn = self._get_connection()
        try:
            conn.execute(
                """
                DELETE FROM wa_processed_wamids WHERE processed_at < %s
                """,
                (cut,),
            )
            conn.commit()
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("wa_wamids_delete_before: %s", e)
        finally:
            conn.close()
