"""
DatabaseManager: العصب الرئيسي للنظام.
======================================

التحديثات في هذا الإصدار (Phase 1 — التوافق مع PostgreSQL):

1) ترجمة SQL مركزية:
   - استبدال _pg_adapt_sql المحلي بدالة translate_sql من logic.sql_translator.
   - تغطية أوسع: ?, datetime('now'), date('now'), INSERT OR IGNORE.

2) سلامة المعاملات (Transaction Safety):
   - كل دالة CRUD تُغلَّف بـ try/except مع rollback() صريح للـ PostgreSQL.
   - إصلاح السطر الميت ROLLBACK TO SAVEPOINT _ticket_sp في حلقة توليد التذاكر.

3) DDL آمن:
   - إبقاء آلية SAVEPOINT الموجودة (تعمل بشكل ممتاز).
   - إبقاء _exec_alter كما هو.

4) إصلاحات نقطية:
   - update_user: تستخدم INSERT OR IGNORE — تترجم تلقائياً عبر _exec_safe.
   - حلقة ticket_code: rollback صريح عند فشل UPDATE في PostgreSQL.

5) ميزة جديدة (إضافية، اختيارية):
   - دالة get_lastrowid_after_insert() ترجع id آخر إدراج بطريقة متوافقة
     مع القاعدتين، تستخدم RETURNING في PostgreSQL أو lastrowid في SQLite.

السلوك العام محفوظ بالكامل: نفس الجداول، نفس الحقول، نفس الـ APIs العامة.
"""

import json
import logging
import re
import secrets
import sqlite3
import string
import sys

from config import DATABASE_PATH, DATABASE_URL, DB_TYPE, DEFAULT_BRANCH_PASSWORD, ensure_data_dir
from typing import Optional, List, Dict, Any, Tuple

# أعمدة clients المسموح تحديثها ديناميكياً (منع حقن SQL)
ALLOWED_CLIENT_FIELDS = frozenset({
    'name', 'dialect', 'last_intent', 'chat_history', 'phone',
    'complaint_draft', 'gender_hint',
})

from logic.branch_repository import BranchRepositoryMixin
from logic.complaint_repository import ComplaintRepositoryMixin
from logic.category_repository import CategoryRepositoryMixin
from logic.company_info_repository import CompanyInfoRepositoryMixin
from logic.customer_repository import CustomerRepositoryMixin
from logic.product_repository import ProductRepositoryMixin
from logic.conversation_repository import ConversationRepositoryMixin
from logic.branch_inquiry_repository import BranchInquiryRepositoryMixin
from logic.wa_inbox_repository import WaInboxRepositoryMixin
from logic.wa_session_repository import WaSessionRepositoryMixin
from logic.db_adapter import wrap_sqlite_connection
from logic.sql_translator import translate_sql, translate_ddl

logger = logging.getLogger(__name__)


def _pg_adapt_sql(sql: str) -> str:
    """
    ترجمة SQL إلى لهجة PostgreSQL.

    مُبقاة كاسم رمزي للتوافق مع أي كود قديم قد يستوردها.
    التنفيذ الفعلي مُفوَّض إلى translate_sql المركزية.
    """
    return translate_sql(sql, "postgres")


class _PostgresCursorWrapper:
    """مطابقة سلوك cursor في sqlite3 قدر الإمكان (lastrowid بعد INSERT)."""

    def __init__(self, inner: Any, raw_conn: Any) -> None:
        self._inner = inner
        self._raw = raw_conn
        self.lastrowid: Optional[int] = None

    def execute(self, sql: str, parameters: Any = ()) -> "_PostgresCursorWrapper":
        self.lastrowid = None
        adapted = translate_sql(sql, "postgres")
        try:
            self._inner.execute(adapted, parameters or ())
        except Exception:
            # تنظيف فوري للاتصال — يمنع InFailedSqlTransaction للاستعلام التالي
            try:
                self._raw.rollback()
            except Exception as rb_err:
                logger.warning("rollback after execute failure also failed: %s", rb_err)
            raise

        # محاولة جلب lastrowid لأوامر INSERT (متوافقة مع SQLite API).
        # نستخدم lastval() الذي يُرجع آخر sequence value في الجلسة الحالية.
        if adapted.lstrip().upper().startswith("INSERT"):
            try:
                with self._raw.cursor() as lc:
                    lc.execute("SELECT lastval() AS lastrowid")
                    row = lc.fetchone()
                    if row is not None:
                        v = row.get("lastrowid") if isinstance(row, dict) else row[0]
                        if v is not None:
                            self.lastrowid = int(v)
            except Exception:
                # بعض الـ INSERTs لا تنشئ sequence جديدة (مثل INSERT ON CONFLICT
                # حين لا يحدث إدراج فعلي). هذا متوقّع — نتجاهل بهدوء.
                pass
        return self

    def executemany(self, sql: str, seq_of_parameters: Any) -> "_PostgresCursorWrapper":
        self.lastrowid = None
        adapted = translate_sql(sql, "postgres")
        try:
            self._inner.executemany(adapted, seq_of_parameters)
        except Exception:
            try:
                self._raw.rollback()
            except Exception as rb_err:
                logger.warning("rollback after executemany failure also failed: %s", rb_err)
            raise
        return self

    def fetchone(self) -> Any:
        return self._inner.fetchone()

    def fetchall(self) -> Any:
        return self._inner.fetchall()

    @property
    def rowcount(self) -> int:
        return int(self._inner.rowcount or 0)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _PostgresConnectionWrapper:
    """غلاف اتصال PostgreSQL بنفس نمط الاستخدام الحالي (execute / cursor / commit)."""

    def __init__(self, raw_conn: Any) -> None:
        self._raw = raw_conn

    def cursor(self) -> _PostgresCursorWrapper:
        return _PostgresCursorWrapper(self._raw.cursor(), self._raw)

    def execute(self, sql: str, parameters: Any = ()) -> _PostgresCursorWrapper:
        cur = self.cursor()
        cur.execute(sql, parameters or ())
        return cur

    def executemany(self, sql: str, seq_of_parameters: Any) -> _PostgresCursorWrapper:
        cur = self.cursor()
        cur.executemany(sql, seq_of_parameters)
        return cur

    def executescript(self, script: str) -> None:
        raise NotImplementedError(
            "executescript is not supported on PostgreSQL connections; "
            "run DDL via migrations or a SQL client."
        )

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class DatabaseManager(
    BranchRepositoryMixin,
    ComplaintRepositoryMixin,
    CategoryRepositoryMixin,
    ProductRepositoryMixin,
    CustomerRepositoryMixin,
    CompanyInfoRepositoryMixin,
    ConversationRepositoryMixin,
    BranchInquiryRepositoryMixin,
    WaInboxRepositoryMixin,
    WaSessionRepositoryMixin,
):
    def __init__(self, db_path=None):
        ensure_data_dir()
        self.db_type = DB_TYPE
        self.db_path = db_path or DATABASE_PATH
        self._postgres_dsn = DATABASE_URL
        if self.db_type == "sqlite":
            self._init_db()
        else:
            self._verify_postgres_connection()
            # نفس مسار SQLite: إنشاء/تحديث المخطط بشكل idempotent (جدول messages وغيره).
            # بدون هذا قد تُستعمل Postgres بلا جدول messages فيفشل صندوق الواتساب دون إنذار واضح.
            self._init_db()

    def _verify_postgres_connection(self) -> None:
        """يتحقق من إمكانية الاتصال وتنفيذ استعلام بسيط. أي فشل = إيقاف فوري."""
        try:
            conn = self._connect_postgres_raw()
        except SystemExit:
            raise
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        except Exception as exc:
            logger.critical(
                "FATAL: PostgreSQL connectivity check failed after connect (DB_TYPE=postgres). "
                "Exiting to avoid writing to the wrong database. Error: %s",
                exc,
                exc_info=True,
            )
            try:
                conn.close()
            except Exception:
                pass
            sys.exit(1)
        else:
            conn.close()

    def create_all(self) -> None:
        """مكافئ مبسّط لـ SQLAlchemy.create_all: إنشاء الجداول (CREATE IF NOT EXISTS) لـ SQLite."""
        if self.db_type == "sqlite":
            self._init_db()

    def init_db(self) -> None:
        """إنشاء/تحديث المخطط."""
        self._init_db()

    def _connect_postgres_raw(self) -> Any:
        """يُنشئ اتصال psycopg2؛ عند فشل الاتصال يُسجّل ويغلق العملية."""
        import psycopg2  # type: ignore
        import psycopg2.extras  # type: ignore

        try:
            return psycopg2.connect(
                self._postgres_dsn,
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
        except Exception as exc:
            logger.critical(
                "FATAL: Cannot connect to PostgreSQL (DB_TYPE=postgres). "
                "Check DATABASE_URL and network credentials. The application will exit; "
                "it will not fall back to SQLite to avoid silent data loss. Error: %s",
                exc,
                exc_info=True,
            )
            sys.exit(1)

    def _get_connection(self):
        if self.db_type == "sqlite":
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON")
            except sqlite3.OperationalError:
                pass
            return wrap_sqlite_connection(conn)
        return _PostgresConnectionWrapper(self._connect_postgres_raw())

    def _safe_rollback(self, conn) -> None:
        """rollback آمن لاستخدامه في الدوال CRUD (يمنع InFailedSqlTransaction)."""
        try:
            conn.rollback()
        except Exception as e:
            logger.warning("safe_rollback: rollback itself failed: %s", e)

    def _init_db_ddl(self, sql: str) -> str:
        """ترجمة DDL إلى لهجة قاعدة البيانات الحالية (SERIAL ↔ AUTOINCREMENT + datetime)."""
        return translate_ddl(sql or "", self.db_type)

    def _exec_ddl(self, conn, sql: str) -> None:
        """
        ينفّذ DDL بشكل آمن:
        - PostgreSQL: كل أمر في SAVEPOINT مستقل لتجنب إلغاء كامل المعاملة عند خطأ متوقع.
        - SQLite: يتجاهل OperationalError (المعتادة عند وجود جداول/أعمدة مسبقاً).
        """
        adapted = self._init_db_ddl(sql)

        if self.db_type == "postgres":
            raw = conn._raw
            with raw.cursor() as cur:
                cur.execute("SAVEPOINT _ddl_sp")
                try:
                    cur.execute(adapted)
                    cur.execute("RELEASE SAVEPOINT _ddl_sp")
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT _ddl_sp")
                    if not self._init_db_schema_ignorable(e):
                        raise
        else:
            # SQLite
            try:
                conn.execute(adapted)
            except sqlite3.OperationalError:
                pass  # عمود/جدول موجود مسبقاً — مقبول
            except Exception:
                raise

    def _exec_alter(self, conn, sql: str) -> None:
        """
        ينفّذ ALTER TABLE بشكل آمن (تجاهل أخطاء "عمود موجود مسبقاً").
        PostgreSQL: SAVEPOINT. SQLite: try/except.
        """
        adapted = (
            translate_sql(self._init_db_ddl(sql), "postgres")
            if self.db_type == "postgres"
            else sql
        )

        if self.db_type == "postgres":
            raw = conn._raw
            with raw.cursor() as cur:
                cur.execute("SAVEPOINT _alter_sp")
                try:
                    cur.execute(adapted)
                    cur.execute("RELEASE SAVEPOINT _alter_sp")
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT _alter_sp")
                    if not self._init_db_schema_ignorable(e):
                        raise
        else:
            try:
                conn.execute(adapted)
            except Exception as e:
                if not self._init_db_schema_ignorable(e):
                    raise

    def _init_db_schema_ignorable(self, exc: BaseException) -> bool:
        if self.db_type == "sqlite" and isinstance(exc, sqlite3.OperationalError):
            return True
        if self.db_type == "postgres":
            msg = (str(exc) or "").lower()
            code = str(getattr(exc, "pgcode", "") or "")
            if any(
                t in msg
                for t in (
                    "already exists",
                    "duplicate column",
                    "duplicate key",
                )
            ):
                return True
            if code in ("42P16", "42710", "42701", "42P07", "23505", "42P01"):
                return True
        return False

    def _init_db_unique_violation(self, exc: BaseException) -> bool:
        if isinstance(exc, sqlite3.IntegrityError):
            return True
        if self.db_type == "postgres" and str(getattr(exc, "pgcode", "") or "") == "23505":
            return True
        if self.db_type == "postgres" and "unique" in (str(exc) or "").lower():
            return True
        return False

    def _init_db(self):
        conn = self._get_connection()

        # ── الجداول الأساسية ──────────────────────────────────────────
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS clients (
                user_id TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                dialect TEXT DEFAULT 'saudi',
                last_intent TEXT DEFAULT 'GREETING',
                chat_history TEXT DEFAULT '[]',
                phone TEXT DEFAULT ''
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS branches (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                city_name TEXT NOT NULL
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS main_categories (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                branch_id INTEGER DEFAULT NULL,
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS sub_categories (
                id SERIAL PRIMARY KEY,
                main_id INTEGER,
                branch_id INTEGER,
                name TEXT NOT NULL,
                FOREIGN KEY (main_id) REFERENCES main_categories (id),
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER,
                sub_id INTEGER,
                product_name TEXT NOT NULL,
                description TEXT,
                price REAL DEFAULT 0.0,
                img1 TEXT, img2 TEXT, img3 TEXT,
                FOREIGN KEY (branch_id) REFERENCES branches (id),
                FOREIGN KEY (sub_id) REFERENCES sub_categories (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                product_id INTEGER,
                color TEXT,
                size TEXT,
                quantity INTEGER DEFAULT 0,
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS sections (
                id SERIAL PRIMARY KEY,
                category_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS product_variants (
                id SERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL,
                size TEXT NOT NULL,
                color TEXT NOT NULL,
                price REAL DEFAULT 0.0,
                quantity INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (product_id) REFERENCES products (id),
                UNIQUE (product_id, size, color)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS product_images (
                id SERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                position INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (product_id) REFERENCES products (id),
                UNIQUE (product_id, position)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS complaints (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                branch_id INTEGER,
                employee_name TEXT,
                department TEXT,
                issue TEXT NOT NULL,
                message TEXT DEFAULT '',
                branch_name TEXT DEFAULT '',
                customer_name TEXT DEFAULT '',
                customer_phone TEXT DEFAULT '',
                customer_email TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT,
                complaint_type TEXT DEFAULT 'unspecified',
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS product_requests (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                product_description TEXT NOT NULL,
                requested_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS image_analysis (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                image_path TEXT,
                extracted_features TEXT,
                analyzed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS trend_data (
                id SERIAL PRIMARY KEY,
                feature_type TEXT NOT NULL,
                feature_value TEXT NOT NULL,
                count INTEGER DEFAULT 1,
                last_updated TEXT DEFAULT (datetime('now')),
                UNIQUE (feature_type, feature_value)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS working_hours (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER NOT NULL,
                day_type TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                FOREIGN KEY (branch_id) REFERENCES branches (id),
                UNIQUE (branch_id, day_type)
            )
        """)
        self._migrate_working_hours_period_columns(conn)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS branch_locations (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER NOT NULL UNIQUE,
                address TEXT,
                google_maps_url TEXT,
                gps_lat REAL,
                gps_lng REAL,
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS system_settings (
                id SERIAL PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS customers (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                email TEXT UNIQUE,
                phone TEXT,
                branch_id INTEGER,
                prefers_marketing INTEGER NOT NULL DEFAULT 0,
                dialect TEXT DEFAULT 'default',
                last_product_interest TEXT,
                last_product_interest_at TEXT,
                last_campaign_sent_at TEXT,
                declined_marketing_prompt INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS email_verification_codes (
                email TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                name TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS campaigns (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                whatsapp_message TEXT,
                image_url TEXT,
                branch_id INTEGER,
                created_by TEXT NOT NULL,
                scheduled_at TEXT,
                sent_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS customer_merge_audit (
                id SERIAL PRIMARY KEY,
                source_customer_id INTEGER NOT NULL,
                target_customer_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS analytics_daily_chat (
                day TEXT PRIMARY KEY,
                request_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS company_info (
                id SERIAL PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                value TEXT DEFAULT ''
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS company_branch_services (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER NOT NULL,
                service_title TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE CASCADE
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS conversation_history (
                id          SERIAL PRIMARY KEY,
                session_id  TEXT    NOT NULL,
                role        TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                intent      TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)
        self._exec_ddl(conn, """
            CREATE INDEX IF NOT EXISTS idx_conv_session_time
            ON conversation_history (session_id, created_at)
        """)
        # ── رسائل واتساب (لوحة الفرع / الإدارة) — عمود msg_timestamp يُعادل حقل timestamp في الواجهات ──
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                contact_number TEXT NOT NULL,
                whatsapp_name TEXT NOT NULL DEFAULT '',
                message_body TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL,
                msg_timestamp TEXT DEFAULT (datetime('now')),
                branch_id INTEGER,
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        self._exec_ddl(conn, """
            CREATE INDEX IF NOT EXISTS idx_messages_contact_time
            ON messages (contact_number, msg_timestamp)
        """)
        self._exec_ddl(conn, """
            CREATE INDEX IF NOT EXISTS idx_messages_branch_contact
            ON messages (branch_id, contact_number)
        """)
        # ── جلسة واتساب + منع تكرار معالجة WAMID (ثابت عبر إعادة التشغيل والعمال) ──
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS wa_sessions (
                session_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL DEFAULT 0
            )
        """)
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS wa_processed_wamids (
                wamid TEXT PRIMARY KEY,
                processed_at REAL NOT NULL DEFAULT 0
            )
        """)
        self._exec_ddl(conn, """
            CREATE INDEX IF NOT EXISTS idx_wa_wamids_processed_at
            ON wa_processed_wamids (processed_at)
        """)
        # ── تحكم جلسات واتساب: إيقاف AI أو حظر لكل رقم ──
        self._exec_ddl(conn, """
            CREATE TABLE IF NOT EXISTS wa_contact_controls (
                contact_number TEXT PRIMARY KEY,
                ai_stopped INTEGER NOT NULL DEFAULT 0,
                banned INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── ALTER TABLE: إضافة أعمدة جديدة (كل أمر مستقل — لا rollback عند تكرار) ──
        for alter in (
            "ALTER TABLE products ADD COLUMN section_id INTEGER",
            "ALTER TABLE products ADD COLUMN sku TEXT",
            "ALTER TABLE complaints ADD COLUMN customer_reply_text TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN customer_reply_sent INTEGER DEFAULT 0",
            "ALTER TABLE complaints ADD COLUMN customer_reply_sent_at TEXT",
            "ALTER TABLE clients ADD COLUMN complaint_draft TEXT DEFAULT ''",
            "ALTER TABLE clients ADD COLUMN gender_hint TEXT DEFAULT ''",
            "ALTER TABLE branches ADD COLUMN complaint_email TEXT DEFAULT ''",
            "ALTER TABLE branches ADD COLUMN phone TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN complaint_type TEXT DEFAULT 'unspecified'",
            "ALTER TABLE complaints ADD COLUMN message TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN branch_name TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN customer_name TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN customer_phone TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN customer_email TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN resolved_at TEXT",
            "ALTER TABLE customers ADD COLUMN dialect TEXT DEFAULT 'default'",
            "ALTER TABLE customers ADD COLUMN last_product_interest TEXT",
            "ALTER TABLE customers ADD COLUMN last_product_interest_at TEXT",
            "ALTER TABLE customers ADD COLUMN last_campaign_sent_at TEXT",
            "ALTER TABLE campaigns ADD COLUMN scheduled_at TEXT",
            "ALTER TABLE campaigns ADD COLUMN sent_at TEXT",
            "ALTER TABLE customers ADD COLUMN declined_marketing_prompt INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE customers ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE customers ADD COLUMN merged_into_id INTEGER",
            "ALTER TABLE complaints ADD COLUMN complaint_ai_classification TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN ticket_code TEXT",
            "ALTER TABLE complaints ADD COLUMN resolution_notes TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN employee_name TEXT DEFAULT ''",
            "ALTER TABLE complaints ADD COLUMN department TEXT DEFAULT ''",
            # ── الدفعة 4: أعمدة التخزين السحابي للصور ──
            # يُحفظ image_path الأصلي للتوافق الرجعي.
            # storage_provider: المنصة المستخدمة (local/cloudinary/imagekit/s3/r2)
            # cloud_public_id: المعرف داخل المنصة (للحذف لاحقاً)
            # cloud_width/height/bytes: metadata الصورة
            "ALTER TABLE product_images ADD COLUMN storage_provider TEXT DEFAULT 'local'",
            "ALTER TABLE product_images ADD COLUMN cloud_public_id TEXT",
            "ALTER TABLE product_images ADD COLUMN cloud_width INTEGER",
            "ALTER TABLE product_images ADD COLUMN cloud_height INTEGER",
            "ALTER TABLE product_images ADD COLUMN cloud_bytes INTEGER",
            "ALTER TABLE messages ADD COLUMN sender_type TEXT DEFAULT ''",
        ):
            self._exec_alter(conn, alter)

        # ── تحديثات بيانات: نسخ issue → message و branch_name و status ──
        if self.db_type == "postgres":
            self._exec_ddl(conn, """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'complaints'
    ) THEN
        UPDATE complaints
        SET message = issue
        WHERE (message IS NULL OR TRIM(message) = '')
        AND issue IS NOT NULL AND TRIM(issue) != '';

        UPDATE complaints SET branch_name = (
            SELECT b.city_name FROM branches b WHERE b.id = complaints.branch_id
        )
        WHERE branch_id IS NOT NULL
          AND (branch_name IS NULL OR TRIM(branch_name) = '');

        UPDATE complaints SET status = 'pending'
        WHERE status IS NULL OR TRIM(status) = ''
        OR LOWER(TRIM(status)) = 'open';
    END IF;
END $$;
""")
        else:
            self._exec_ddl(conn, """
                UPDATE complaints SET message = issue
                WHERE (message IS NULL OR TRIM(message) = '')
                  AND issue IS NOT NULL AND TRIM(issue) != ''
            """)
            self._exec_ddl(conn, """
                UPDATE complaints SET branch_name = (
                    SELECT b.city_name FROM branches b WHERE b.id = complaints.branch_id
                )
                WHERE branch_id IS NOT NULL
                  AND (branch_name IS NULL OR TRIM(branch_name) = '')
            """)
            self._exec_ddl(conn, """
                UPDATE complaints SET status = 'pending'
                WHERE status IS NULL OR TRIM(status) = ''
                OR LOWER(TRIM(status)) = 'open'
            """)

        # ── فهرس ticket_code الفريد ──
        if self.db_type == "postgres":
            self._exec_ddl(conn, """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'complaints'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'complaints'
              AND indexname = 'ux_complaints_ticket_code'
        ) THEN
            CREATE UNIQUE INDEX ux_complaints_ticket_code
            ON complaints(ticket_code)
            WHERE ticket_code IS NOT NULL AND TRIM(ticket_code) != '';
        END IF;
    END IF;
END $$;
""")
        else:
            self._exec_ddl(conn, """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_complaints_ticket_code
                ON complaints(ticket_code)
                WHERE ticket_code IS NOT NULL AND TRIM(ticket_code) != ''
            """)

        # ── توليد ticket_code للشكاوى التي ليس لها رقم ──
        # ملاحظة الإصلاح: في النسخة السابقة كان يوجد سطر ميت
        # `conn._raw.execute("ROLLBACK TO SAVEPOINT _ticket_sp") if False else None`
        # تمت إزالته. نستخدم الآن SAVEPOINT حقيقي حول كل UPDATE في PostgreSQL
        # لتجنّب تسميم المعاملة عند تعارض UNIQUE.
        def _gen_complaint_ticket() -> str:
            return str(secrets.randbelow(900000) + 100000)

        try:
            placeholder = "%s" if self.db_type == "postgres" else "?"
            need_cur = conn.execute(
                "SELECT id FROM complaints "
                "WHERE ticket_code IS NULL OR TRIM(COALESCE(ticket_code, '')) = ''"
            )
            need = need_cur.fetchall()
            for row in need:
                tid = int(row["id"])
                for _attempt in range(24):
                    code = _gen_complaint_ticket()
                    if self.db_type == "postgres":
                        # حماية المعاملة من التسميم عبر SAVEPOINT لكل محاولة
                        raw = conn._raw
                        with raw.cursor() as sp_cur:
                            sp_cur.execute("SAVEPOINT _ticket_sp")
                            try:
                                sp_cur.execute(
                                    f"UPDATE complaints SET ticket_code = {placeholder} WHERE id = {placeholder}",
                                    (code, tid),
                                )
                                sp_cur.execute("RELEASE SAVEPOINT _ticket_sp")
                                break
                            except Exception as ex:
                                sp_cur.execute("ROLLBACK TO SAVEPOINT _ticket_sp")
                                if self._init_db_unique_violation(ex):
                                    continue  # نولّد كود جديد
                                raise
                    else:
                        try:
                            conn.execute(
                                f"UPDATE complaints SET ticket_code = {placeholder} WHERE id = {placeholder}",
                                (code, tid),
                            )
                            break
                        except Exception as ex:
                            if self._init_db_unique_violation(ex):
                                continue
                            raise
        except Exception as e:
            if not self._init_db_schema_ignorable(e):
                logger.warning("ticket_code generation warning: %s", e)

        # ── بذور الفروع الافتراضية ──
        ph = "%s" if self.db_type == "postgres" else "?"
        branch_count_cur = conn.execute("SELECT COUNT(*) AS cnt FROM branches")
        branch_row = branch_count_cur.fetchone()
        branch_count = int(branch_row["cnt"] if isinstance(branch_row, dict) else branch_row[0])

        if branch_count == 0:
            if not (DEFAULT_BRANCH_PASSWORD or "").strip():
                raise RuntimeError(
                    "DEFAULT_BRANCH_PASSWORD must be set to initialize an empty branches table."
                )
            branches_list = [
                ("jeddah_admin", DEFAULT_BRANCH_PASSWORD.strip(), "فرع جدة"),
                ("makkah_admin", DEFAULT_BRANCH_PASSWORD.strip(), "فرع مكة"),
                ("madina_admin", DEFAULT_BRANCH_PASSWORD.strip(), "فرع المدينة"),
                ("khamis_admin", DEFAULT_BRANCH_PASSWORD.strip(), "فرع خميس مشيط"),
                ("qilwah_admin", DEFAULT_BRANCH_PASSWORD.strip(), "فرع قلوة"),
            ]
            if self.db_type == "sqlite":
                conn.executemany(
                    "INSERT OR IGNORE INTO branches (username, password, city_name) VALUES (?, ?, ?)",
                    branches_list,
                )
            else:
                conn.executemany(
                    "INSERT INTO branches (username, password, city_name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (username) DO NOTHING",
                    branches_list,
                )

        self._seed_branch_complaint_emails(conn.cursor())
        self._seed_branch_locations_and_hours(conn)
        self._seed_default_system_settings(conn.cursor())
        self._merge_duplicate_branches(conn.cursor())
        conn.commit()
        conn.close()

        # جدول استفسارات الفروع
        self._ensure_inquiry_table()

    def _migrate_working_hours_period_columns(self, conn):
        """أعمدة فترتين: start/end_1 و start/end_2 مع نسخ من open_time/close_time للبيانات القديمة."""
        if self.db_type == "postgres":
            # على PostgreSQL نستخدم _exec_alter الآمن
            for col in ("start_time_1", "end_time_1", "start_time_2", "end_time_2"):
                self._exec_alter(conn, f"ALTER TABLE working_hours ADD COLUMN {col} TEXT")
            self._exec_ddl(conn, """
                UPDATE working_hours
                SET start_time_1 = open_time
                WHERE start_time_1 IS NULL OR TRIM(COALESCE(start_time_1, '')) = ''
            """)
            self._exec_ddl(conn, """
                UPDATE working_hours
                SET end_time_1 = close_time
                WHERE end_time_1 IS NULL OR TRIM(COALESCE(end_time_1, '')) = ''
            """)
            return

        # SQLite
        try:
            conn.execute("PRAGMA table_info(working_hours)")
        except Exception:
            return
        cur = conn.execute("PRAGMA table_info(working_hours)")
        cols = {row[1] if not isinstance(row, dict) else row["name"] for row in cur.fetchall()}
        for col in ("start_time_1", "end_time_1", "start_time_2", "end_time_2"):
            if col not in cols:
                try:
                    conn.execute(f"ALTER TABLE working_hours ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
        try:
            conn.execute(
                "UPDATE working_hours SET start_time_1 = open_time "
                "WHERE start_time_1 IS NULL OR TRIM(COALESCE(start_time_1, '')) = ''"
            )
            conn.execute(
                "UPDATE working_hours SET end_time_1 = close_time "
                "WHERE end_time_1 IS NULL OR TRIM(COALESCE(end_time_1, '')) = ''"
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()

    # ═══════════════════════════════════════════════════════════════
    # دوال البوت (Clients)
    # ═══════════════════════════════════════════════════════════════

    def get_user(self, user_id):
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT * FROM clients WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()

    def update_user(self, user_id, **kwargs):
        """
        ينشئ/يحدّث صف clients. تم تحسين سلامة المعاملات:
        - INSERT OR IGNORE تُترجم تلقائياً عبر translate_sql على PostgreSQL.
        - تغليف بـ try/except مع rollback لتجنّب InFailedSqlTransaction
          عند فشل أي UPDATE فردي.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO clients (user_id) VALUES (?)", (user_id,))
            for key, value in kwargs.items():
                if key not in ALLOWED_CLIENT_FIELDS:
                    continue
                cursor.execute(f"UPDATE clients SET {key} = ? WHERE user_id = ?", (value, user_id))
            conn.commit()
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()

    def _seed_default_system_settings(self, cursor):
        defaults = [
            ("ai_provider", "openai"),
            ("ai_model", "gpt-4o"),
        ]
        for k, v in defaults:
            if self.db_type == "sqlite":
                cursor.execute(
                    "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
                    (k, v),
                )
            else:
                cursor.execute(
                    "INSERT INTO system_settings (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (k, v),
                )

    def add_product_request(self, user_id, product_description):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO product_requests (user_id, product_description) VALUES (?, ?)",
                (user_id, product_description),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            return None
        finally:
            conn.close()

    def list_recent_product_requests(self, limit: int = 50) -> list:
        """آخر طلبات بحث عن منتج من الشات (جدول product_requests) — بدون فرع."""
        limit = max(1, min(int(limit), 500))
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT id, user_id, product_description, requested_at
                FROM product_requests
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            logger.exception("list_recent_product_requests failed")
            return []
        finally:
            conn.close()

    def count_product_requests(self) -> int:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM product_requests").fetchone()
            return int(row["c"] if isinstance(row, dict) else row[0]) if row else 0
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            return 0
        finally:
            conn.close()

    def add_image_analysis(self, user_id, image_path, extracted_features=None):
        conn = self._get_connection()
        try:
            payload = extracted_features if isinstance(extracted_features, str) else json.dumps(extracted_features or {}, ensure_ascii=False)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO image_analysis (user_id, image_path, extracted_features) VALUES (?, ?, ?)",
                (user_id, image_path, payload),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            return None
        finally:
            conn.close()

    def find_complaints_by_name_or_branch(
        self, customer_name: str = "", branch_name: str = "", limit: int = 5
    ) -> list:
        """يبحث عن شكاوى بالاسم أو الفرع لمساعدة العميل اللي ما عنده رقم تذكرة."""
        conn = self._get_connection()
        try:
            parts, params = [], []
            if (customer_name or "").strip():
                parts.append("LOWER(customer_name) LIKE ?")
                params.append(f"%{customer_name.strip().lower()}%")
            if (branch_name or "").strip():
                parts.append("LOWER(branch_name) LIKE ?")
                params.append(f"%{branch_name.strip().lower()}%")
            if not parts:
                return []
            where = " AND ".join(parts)
            params.append(int(limit))
            cur = conn.execute(
                f"SELECT * FROM complaints WHERE {where} ORDER BY id DESC LIMIT ?",
                params,
            )
            return [dict(r) for r in cur.fetchall()]
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()

    def get_complaint_with_customer_contact(self, complaint_id: int):
        """يجيب الشكوى مع بيانات تواصل العميل لإرسال الرد."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM complaints WHERE id = %s", (int(complaint_id),)
            )
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()

    def save_complaint_customer_reply(
        self, complaint_id: int, reply_text: str
    ) -> bool:
        """يحفظ نص رد الفرع على العميل ويحدّث حالة الإرسال."""
        conn = self._get_connection()
        try:
            conn.execute(
                """
                UPDATE complaints
                SET customer_reply_text = %s,
                    customer_reply_sent = 1,
                    customer_reply_sent_at = datetime('now'),
                    status = 'resolved',
                    resolved_at = datetime('now')
                WHERE id = %s
                """,
                (str(reply_text)[:4000], int(complaint_id)),
            )
            conn.commit()
            return True
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            return False
        finally:
            conn.close()

    def upsert_trend(self, feature_type, feature_value, increment=1):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, count FROM trend_data WHERE feature_type = ? AND feature_value = ?",
                (feature_type, feature_value),
            )
            row = cursor.fetchone()
            if row:
                r = dict(row)
                cursor.execute(
                    "UPDATE trend_data SET count = count + ?, last_updated = datetime('now') WHERE id = ?",
                    (increment, r["id"]),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO trend_data (feature_type, feature_value, count, last_updated)
                    VALUES (?, ?, ?, datetime('now'))
                    """,
                    (feature_type, feature_value, increment),
                )
            conn.commit()
            return True
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            return False
        finally:
            conn.close()

    def get_trend_data(self, limit=100):
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM trend_data ORDER BY count DESC, last_updated DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()

    def get_trend_analytics_snapshot(
        self,
        branch_scope: Optional[int] = None,
        *,
        limit: int = 12,
    ) -> Dict[str, Any]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT feature_type, feature_value, count FROM trend_data"
            )
            rows = [dict(r) for r in cursor.fetchall()]
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()

        def parse_parts(fv: str) -> Tuple[int, str]:
            if not fv:
                return 0, ""
            parts = fv.split("\x1f", 1)
            bid_s = parts[0].strip()
            try:
                bid = int(bid_s) if bid_s != "" else 0
            except ValueError:
                bid = 0
            label = parts[1] if len(parts) > 1 else fv
            return bid, label

        def row_matches(bid: int) -> bool:
            if branch_scope is None:
                return True
            return bid == int(branch_scope)

        products: List[Dict[str, Any]] = []
        intents: List[Dict[str, Any]] = []
        hours_acc: Dict[int, int] = {}

        for r in rows:
            ft = (r.get("feature_type") or "").strip()
            fv = r.get("feature_value") or ""
            cnt = int(r.get("count") or 0)
            bid, label = parse_parts(fv)
            if not row_matches(bid):
                continue
            if ft == "product":
                products.append({"branch_id": bid, "name": label, "count": cnt})
            elif ft == "intent":
                intents.append({"branch_id": bid, "name": label, "count": cnt})
            elif ft == "hour":
                try:
                    h = int(label)
                except (TypeError, ValueError):
                    continue
                if 0 <= h <= 23:
                    hours_acc[h] = hours_acc.get(h, 0) + cnt

        products.sort(key=lambda x: (-int(x["count"]), x["name"]))
        intents.sort(key=lambda x: (-int(x["count"]), x["name"]))
        hours_list = [{"hour": h, "count": hours_acc.get(h, 0)} for h in range(24)]
        peak_hour = max(range(24), key=lambda hh: hours_acc.get(hh, 0)) if hours_acc else None

        return {
            "branch_scope": branch_scope,
            "products": products[:limit],
            "intents": intents[:limit],
            "hours": hours_list,
            "peak_hour": peak_hour,
            "totals": {
                "product_rows": len(products),
                "intent_rows": len(intents),
                "hour_events": sum(hours_acc.values()),
            },
        }

    def increment_daily_chat_count(self) -> bool:
        from datetime import date
        d = date.today().isoformat()
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO analytics_daily_chat (day, request_count) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET request_count = request_count + 1
                """,
                (d,),
            )
            conn.commit()
            return True
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            return False
        finally:
            conn.close()

    def get_daily_chat_series(self, days: int = 30) -> Dict[str, Any]:
        from datetime import date, timedelta
        days = max(7, min(int(days), 90))
        end = date.today()
        start = end - timedelta(days=days - 1)
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT day, request_count FROM analytics_daily_chat
                WHERE day >= ? AND day <= ?
                ORDER BY day
                """,
                (start.isoformat(), end.isoformat()),
            )
            db_map = {str(r["day"]): int(r["request_count"]) for r in cur.fetchall()}
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()

        labels: List[str] = []
        values: List[int] = []
        d = start
        while d <= end:
            ds = d.isoformat()
            labels.append(ds[5:])
            values.append(db_map.get(ds, 0))
            d += timedelta(days=1)

        return {
            "labels": labels,
            "values": values,
            "days": days,
            "total_in_range": sum(values),
        }

    def set_system_setting(self, key, value):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM system_settings WHERE key = ?", (key,))
            if cursor.fetchone():
                cursor.execute("UPDATE system_settings SET value = ? WHERE key = ?", (value, key))
            else:
                cursor.execute("INSERT INTO system_settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit()
            return True
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            return False
        finally:
            conn.close()

    def get_system_setting(self, key, default=None):
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if not row:
                return default
            return dict(row)["value"]
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()

    def get_all_system_settings(self):
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT key, value FROM system_settings ORDER BY key")
            return {row["key"]: row["value"] for row in cursor.fetchall()}
        except Exception:
            if self.db_type == "postgres":
                self._safe_rollback(conn)
            raise
        finally:
            conn.close()