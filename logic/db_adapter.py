"""
DBAdapter: طبقة موحّدة للوصول إلى قاعدة البيانات (SQLite + PostgreSQL).

التحديثات في هذا الإصدار:
- استخدام `sql_translator` المركزي بدلاً من تحويل placeholders اليدوي.
- إضافة rollback() صريح عند فشل أي استعلام في PostgreSQL لتجنّب
  حالة InFailedSqlTransaction.
- الإبقاء على نفس الواجهة العامة (execute / fetch_one / fetch_all /
  wrap_sqlite_connection) لكي لا يتأثر أي ملف يستوردها.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, Optional

from config import DATABASE_PATH, DATABASE_URL, DB_TYPE
from logic.sql_translator import translate_sql

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# توافق رجعي: واجهة قديمة كانت تتوقع دالة بنفس هذا الاسم
# ──────────────────────────────────────────────────────────────────────

def adapt_sql_placeholders_for_sqlite(query: str) -> str:
    """تحويل %s إلى ? لـ sqlite3 (مُبقاة للتوافق مع الكود القديم)."""
    return translate_sql(query, "sqlite")


# ──────────────────────────────────────────────────────────────────────
# Proxies حول اتصال SQLite — تسمح بكتابة الكود بأسلوب %s placeholders
# ──────────────────────────────────────────────────────────────────────

class _SQLiteCursorProxy:
    """Cursor wrapper يقبل %s placeholders ويحوّلها إلى ? تلقائياً."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        return self._cursor.execute(translate_sql(sql, "sqlite"), parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> Any:
        return self._cursor.executemany(
            translate_sql(sql, "sqlite"), seq_of_parameters
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _SQLiteConnectionProxy:
    """Connection wrapper: execute/executemany/cursor تحوّل placeholders."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        return self._conn.execute(translate_sql(sql, "sqlite"), parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> Any:
        return self._conn.executemany(
            translate_sql(sql, "sqlite"), seq_of_parameters
        )

    def cursor(self) -> _SQLiteCursorProxy:
        return _SQLiteCursorProxy(self._conn.cursor())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def wrap_sqlite_connection(conn: Any) -> Any:
    """يُرجع اتصال يقبل %s placeholders (يحوّلها إلى ?)."""
    return _SQLiteConnectionProxy(conn)


# ──────────────────────────────────────────────────────────────────────
# DBAdapter: واجهة عالية المستوى (execute / fetch_one / fetch_all)
# ──────────────────────────────────────────────────────────────────────

class DBAdapter:
    """
    Unified DB adapter for project-wide database operations.

    يدعم:
    - sqlite3
    - psycopg2 (عندما DB_TYPE=postgres)

    أبرز التحسينات:
    - تطبيق ترجمة SQL مركزية عبر sql_translator (تتيح كتابة استعلام واحد
      يعمل على القاعدتين بدون تعديل).
    - rollback() صريح عند أي فشل في PostgreSQL لتجنّب تسميم المعاملة.
    """

    def __init__(
        self,
        db_type: Optional[str] = None,
        sqlite_path: Optional[str] = None,
        postgres_dsn: Optional[str] = None,
    ) -> None:
        self.db_type = (db_type or DB_TYPE).strip().lower()
        self.sqlite_path = sqlite_path or DATABASE_PATH
        self.postgres_dsn = postgres_dsn or DATABASE_URL or os.getenv("DATABASE_URL", "")

        # توحيد القيم البديلة
        if self.db_type in ("postgres", "postgresql", "psycopg2", "pg"):
            self.db_type = "postgres"
        elif self.db_type in ("sqlite3", ""):
            self.db_type = "sqlite"

        if self.db_type not in {"sqlite", "postgres"}:
            raise ValueError(
                f"Unsupported DB_TYPE '{self.db_type}'. Use 'sqlite' or 'postgres'."
            )

    def _prepare_sql(self, query: str) -> str:
        """ترجمة الاستعلام إلى لهجة قاعدة البيانات الحالية."""
        return translate_sql(query, self.db_type)

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        if self.db_type == "sqlite":
            conn = sqlite3.connect(self.sqlite_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON")
            except sqlite3.OperationalError:
                pass
            try:
                yield conn
            finally:
                conn.close()
            return

        # ── PostgreSQL ──
        try:
            import psycopg2  # type: ignore
            import psycopg2.extras  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "DB_TYPE=postgres requires psycopg2 to be installed."
            ) from exc

        if not self.postgres_dsn:
            raise RuntimeError(
                "DB_TYPE=postgres requires DATABASE_URL (or postgres_dsn) to be set."
            )

        conn = psycopg2.connect(
            self.postgres_dsn, cursor_factory=psycopg2.extras.RealDictCursor
        )
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _safe_rollback(self, conn: Any) -> None:
        """rollback آمن — يُستخدم عند فشل استعلام في PostgreSQL."""
        try:
            conn.rollback()
        except Exception as e:
            # لو فشل rollback نفسه، لا نوقف التنفيذ (الاتصال سيُغلق بأي حال).
            logger.warning("Rollback failed (will close connection): %s", e)

    # ── execute / fetch_one / fetch_all ─────────────────────────

    def execute(self, query: str, params: Optional[Iterable[Any]] = None) -> int:
        """ينفّذ INSERT/UPDATE/DELETE ويرجع rowcount."""
        values = tuple(params or ())
        sql = self._prepare_sql(query)
        with self._connection() as conn:
            try:
                cur = conn.cursor()
                cur.execute(sql, values)
                conn.commit()
                return int(cur.rowcount or 0)
            except Exception:
                if self.db_type == "postgres":
                    self._safe_rollback(conn)
                raise

    def fetch_one(
        self, query: str, params: Optional[Iterable[Any]] = None
    ) -> Optional[Dict[str, Any]]:
        values = tuple(params or ())
        sql = self._prepare_sql(query)
        with self._connection() as conn:
            try:
                cur = conn.cursor()
                cur.execute(sql, values)
                row = cur.fetchone()
                if row is None:
                    return None
                if isinstance(row, dict):
                    return row
                return dict(row)
            except Exception:
                if self.db_type == "postgres":
                    self._safe_rollback(conn)
                raise

    def fetch_all(
        self, query: str, params: Optional[Iterable[Any]] = None
    ) -> list[Dict[str, Any]]:
        values = tuple(params or ())
        sql = self._prepare_sql(query)
        with self._connection() as conn:
            try:
                cur = conn.cursor()
                cur.execute(sql, values)
                rows = cur.fetchall()
                if not rows:
                    return []
                if isinstance(rows[0], dict):
                    return list(rows)
                return [dict(row) for row in rows]
            except Exception:
                if self.db_type == "postgres":
                    self._safe_rollback(conn)
                raise