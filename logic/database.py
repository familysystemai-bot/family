import json
import secrets
import sqlite3
import string

from config import DATABASE_PATH, DEFAULT_BRANCH_PASSWORD, ensure_data_dir
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


class DatabaseManager(
    BranchRepositoryMixin,
    ComplaintRepositoryMixin,
    CategoryRepositoryMixin,
    ProductRepositoryMixin,
    CustomerRepositoryMixin,
    CompanyInfoRepositoryMixin,
):
    def __init__(self, db_path=None):
        ensure_data_dir()
        self.db_path = db_path or DATABASE_PATH
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.OperationalError:
            pass
        return conn

    def _init_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # الجداول الأساسية
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                user_id TEXT PRIMARY KEY, 
                name TEXT DEFAULT '', 
                dialect TEXT DEFAULT 'saudi', 
                last_intent TEXT DEFAULT 'GREETING', 
                chat_history TEXT DEFAULT '[]', 
                phone TEXT DEFAULT ''
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                username TEXT UNIQUE NOT NULL, 
                password TEXT NOT NULL, 
                city_name TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS main_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                name TEXT NOT NULL, 
                branch_id INTEGER DEFAULT NULL, 
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sub_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                main_id INTEGER, 
                branch_id INTEGER, 
                name TEXT NOT NULL, 
                FOREIGN KEY (main_id) REFERENCES main_categories (id), 
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                product_id INTEGER, 
                color TEXT, 
                size TEXT, 
                quantity INTEGER DEFAULT 0, 
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
        """)

        # ═══════════════════════════════════════════════════════════════
        # Schema جديد (جاهز بلوحات إدارة الفرع + الشات)
        # لا نلغي الجداول القديمة؛ فقط نضيف تنظيم Categories/Sections.
        # ملاحظة: جدول products موجود مسبقاً، لذلك سنضيف عمود section_id
        # وربطه منطقيًا مع sections.
        # ═══════════════════════════════════════════════════════════════

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)

        # إضافة عمود section_id إلى products إن لم يكن موجوداً
        for alter in (
            "ALTER TABLE products ADD COLUMN section_id INTEGER",
            "ALTER TABLE products ADD COLUMN sku TEXT",
        ):
            try:
                cursor.execute(alter)
            except sqlite3.OperationalError:
                pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                position INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (product_id) REFERENCES products (id),
                UNIQUE (product_id, position)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                product_description TEXT NOT NULL,
                requested_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS image_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                image_path TEXT,
                extracted_features TEXT,
                analyzed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trend_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_type TEXT NOT NULL,
                feature_value TEXT NOT NULL,
                count INTEGER DEFAULT 1,
                last_updated TEXT DEFAULT (datetime('now')),
                UNIQUE (feature_type, feature_value)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS working_hours (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id INTEGER NOT NULL,
                day_type TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                FOREIGN KEY (branch_id) REFERENCES branches (id),
                UNIQUE (branch_id, day_type)
            )
        """)
        self._migrate_working_hours_period_columns(cursor, conn)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS branch_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id INTEGER NOT NULL UNIQUE,
                address TEXT,
                google_maps_url TEXT,
                gps_lat REAL,
                gps_lng REAL,
                FOREIGN KEY (branch_id) REFERENCES branches (id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_verification_codes (
                email TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                name TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customer_merge_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_customer_id INTEGER NOT NULL,
                target_customer_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analytics_daily_chat (
                day TEXT PRIMARY KEY,
                request_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS company_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                value TEXT DEFAULT ''
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS company_branch_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id INTEGER NOT NULL,
                service_title TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE CASCADE
            )
        """)

        for alter in (
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
        ):
            try:
                cursor.execute(alter)
            except sqlite3.OperationalError:
                pass

        try:
            cursor.execute(
                """
                UPDATE complaints SET message = issue
                WHERE (message IS NULL OR TRIM(message) = '')
                  AND issue IS NOT NULL AND TRIM(issue) != ''
                """
            )
            cursor.execute(
                """
                UPDATE complaints SET branch_name = (
                    SELECT b.city_name FROM branches b WHERE b.id = complaints.branch_id
                )
                WHERE branch_id IS NOT NULL
                  AND (branch_name IS NULL OR TRIM(branch_name) = '')
                """
            )
            cursor.execute(
                "UPDATE complaints SET status = 'pending' "
                "WHERE status IS NULL OR TRIM(status) = '' "
                "OR LOWER(TRIM(status)) = 'open'"
            )
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_complaints_ticket_code
                ON complaints(ticket_code)
                WHERE ticket_code IS NOT NULL AND TRIM(ticket_code) != ''
                """
            )
        except sqlite3.OperationalError:
            pass

        _ticket_alphabet = string.ascii_uppercase + string.digits

        def _gen_complaint_ticket() -> str:
            return "TKT-" + "".join(
                secrets.choice(_ticket_alphabet) for _ in range(8)
            )

        try:
            need = cursor.execute(
                """
                SELECT id FROM complaints
                WHERE ticket_code IS NULL OR TRIM(COALESCE(ticket_code, '')) = ''
                """
            ).fetchall()
            for row in need:
                tid = int(row["id"])
                for _attempt in range(24):
                    code = _gen_complaint_ticket()
                    try:
                        cursor.execute(
                            "UPDATE complaints SET ticket_code = ? WHERE id = ?",
                            (code, tid),
                        )
                        break
                    except sqlite3.IntegrityError:
                        continue
        except sqlite3.OperationalError:
            pass

        # بذور الفروع الافتراضية: مرة واحدة فقط عند قاعدة فارغة (لا إعادة إدراج عند كل تشغيل)
        cursor.execute("SELECT COUNT(*) FROM branches")
        branch_count = int(cursor.fetchone()[0])
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
            cursor.executemany(
                "INSERT OR IGNORE INTO branches (username, password, city_name) VALUES (?, ?, ?)",
                branches_list,
            )
        self._seed_branch_complaint_emails(cursor)
        self._seed_branch_locations_and_hours(conn)
        self._seed_default_system_settings(cursor)
        self._merge_duplicate_branches(cursor)
        conn.commit()
        conn.close()

    def _migrate_working_hours_period_columns(self, cursor, conn):
        """أعمدة فترتين: start/end_1 و start/end_2 مع نسخ من open_time/close_time للبيانات القديمة."""
        cursor.execute("PRAGMA table_info(working_hours)")
        cols = {row[1] for row in cursor.fetchall()}
        for col in ("start_time_1", "end_time_1", "start_time_2", "end_time_2"):
            if col not in cols:
                try:
                    cursor.execute(f"ALTER TABLE working_hours ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
        try:
            cursor.execute(
                """
                UPDATE working_hours
                SET start_time_1 = open_time
                WHERE start_time_1 IS NULL OR TRIM(COALESCE(start_time_1, '')) = ''
                """
            )
            cursor.execute(
                """
                UPDATE working_hours
                SET end_time_1 = close_time
                WHERE end_time_1 IS NULL OR TRIM(COALESCE(end_time_1, '')) = ''
                """
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()

    # ═══════════════════════════════════════════════════════════════
    # دوال البوت (Clients)
    # ═══════════════════════════════════════════════════════════════

    def get_user(self, user_id):
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM clients WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_user(self, user_id, **kwargs):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO clients (user_id) VALUES (?)", (user_id,))
        for key, value in kwargs.items():
            if key not in ALLOWED_CLIENT_FIELDS:
                continue
            cursor.execute(f"UPDATE clients SET {key} = ? WHERE user_id = ?", (value, user_id))
        conn.commit()
        conn.close()

    def _seed_default_system_settings(self, cursor):
        defaults = [
            ("ai_provider", "ollama"),
            ("ai_model", "llama3.1:8b"),
        ]
        for k, v in defaults:
            cursor.execute(
                "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
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
            return None
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
            return None
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
        finally:
            conn.close()

    def get_trend_analytics_snapshot(
        self,
        branch_scope: Optional[int] = None,
        *,
        limit: int = 12,
    ) -> Dict[str, Any]:
        """
        تجميع trend_data للوحات التحليلات: منتجات، نيات، ساعات (feature_type = hour).
        branch_scope: عند تحديده (مستخدم فرع) يُقتصر على صفوف يطابق فيها معرّف الفرع في feature_value.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT feature_type, feature_value, count FROM trend_data"
            )
            rows = [dict(r) for r in cursor.fetchall()]
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
                products.append(
                    {"branch_id": bid, "name": label, "count": cnt}
                )
            elif ft == "intent":
                intents.append(
                    {"branch_id": bid, "name": label, "count": cnt}
                )
            elif ft == "hour":
                try:
                    h = int(label)
                except (TypeError, ValueError):
                    continue
                if 0 <= h <= 23:
                    hours_acc[h] = hours_acc.get(h, 0) + cnt

        products.sort(key=lambda x: (-int(x["count"]), x["name"]))
        intents.sort(key=lambda x: (-int(x["count"]), x["name"]))

        hours_list = [
            {"hour": h, "count": hours_acc.get(h, 0)} for h in range(24)
        ]
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
        """يزيد عداد تفاعلات الشات لليوم الحالي (يُستدعى مع تسجيل trend_data)."""
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
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    def get_daily_chat_series(self, days: int = 30) -> Dict[str, Any]:
        """
        سلسلة يومية لعدد «طلبات/تفاعلات» الشات المسجّلة (مرتبطة بتسجيل التحليلات).
        """
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
        finally:
            conn.close()

    def get_all_system_settings(self):
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT key, value FROM system_settings ORDER BY key")
            return {row["key"]: row["value"] for row in cursor.fetchall()}
        finally:
            conn.close()
