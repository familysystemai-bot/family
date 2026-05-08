# -*- coding: utf-8 -*-
"""
BranchRepositoryMixin — إدارة الفروع.

التحديثات في هذا الإصدار:
- إضافة _safe_rollback_pg() واستدعائها في كل دالة CRUD عند الفشل.
- استخدام self.db_type المتاح في DatabaseManager (تجنب إنشاء DBAdapter بلا داعي).
- نفس الواجهة العامة محفوظة بالكامل، نفس التواقيع، نفس الإرجاعات.
- لا تغيير في أي منطق أعمال أو حسابات.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from werkzeug.security import check_password_hash, generate_password_hash
from logic.db_adapter import DBAdapter

from logic.branch_helpers import (
    _branch_dedupe_key,
    _canonical_branch_city_from_input,
    _normalize_branch_city_label,
)

logger = logging.getLogger(__name__)


class BranchRepositoryMixin:
    """Mixin: يُدمج في DatabaseManager — يستخدم self._get_connection() فقط."""

    def _db_adapter(self) -> DBAdapter:
        return DBAdapter(sqlite_path=getattr(self, "db_path", None))

    def _safe_rollback_pg(self, conn) -> None:
        """rollback آمن للـ PostgreSQL فقط — لمنع InFailedSqlTransaction."""
        if getattr(self, "db_type", None) != "postgres":
            return
        try:
            conn.rollback()
        except Exception as e:
            logger.warning("rollback failed in branch_repository: %s", e)

    @staticmethod
    def _branch_password_is_hashed(value: Optional[str]) -> bool:
        s = (value or "").strip()
        return s.startswith("pbkdf2:") or s.startswith("scrypt:")

    @staticmethod
    def _hash_branch_password(raw_password: str) -> str:
        pw = (raw_password or "").strip()
        if not pw:
            raise ValueError("empty branch password")
        return generate_password_hash(pw)

    @classmethod
    def _branch_password_matches(cls, stored_password: Optional[str], plain_password: str) -> bool:
        stored = (stored_password or "").strip()
        plain = plain_password or ""
        if not stored or not plain:
            return False
        if cls._branch_password_is_hashed(stored):
            return check_password_hash(stored, plain)
        return stored == plain

    def check_branch_login_with_status(self, username, password) -> tuple[Optional[Dict[str, Any]], str]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT id, city_name, password FROM branches WHERE username = %s",
                ((username or "").strip(),),
            )
            row = cursor.fetchone()
            if not row:
                return None, "user_not_found"
            branch = dict(row)
            stored_password = branch.get("password")
            if not self._branch_password_matches(stored_password, password):
                return None, "password_mismatch"
            if stored_password and not self._branch_password_is_hashed(stored_password):
                conn.execute(
                    "UPDATE branches SET password = %s WHERE id = %s",
                    (self._hash_branch_password(password), branch["id"]),
                )
                conn.commit()
            return {"id": branch["id"], "city_name": branch["city_name"]}, "ok"
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("check_branch_login_with_status: %s", e)
            return None, "error"
        finally:
            conn.close()

    def get_branch_info(self, branch_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT id, city_name FROM branches WHERE id = %s", (branch_id,))
            b = cursor.fetchone()
            if not b:
                return None
            branch = dict(b)
            loc = self.get_branch_location(branch_id)
            if loc:
                branch.update(
                    {
                        "address": loc.get("address"),
                        "google_maps_url": loc.get("google_maps_url"),
                        "gps_lat": loc.get("gps_lat"),
                        "gps_lng": loc.get("gps_lng"),
                    }
                )
            return branch
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_branch_info: %s", e)
            return None
        finally:
            conn.close()

    def check_branch_login(self, username, password):
        branch, _status = self.check_branch_login_with_status(username, password)
        return branch

    def get_all_branches(self):
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT * FROM branches ORDER BY id")
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_all_branches: %s", e)
            return []
        finally:
            conn.close()

    def list_branches_complaint_emails(self) -> List[Dict[str, Any]]:
        """للتشخيص: city_name + complaint_email لكل فرع."""
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT city_name, complaint_email FROM branches ORDER BY id"
            )
            out: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                r = dict(row)
                em = (r.get("complaint_email") or "").strip()
                out.append(
                    {
                        "branch": r.get("city_name") or "",
                        "email": em if em else None,
                    }
                )
            return out
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("list_branches_complaint_emails: %s", e)
            return []
        finally:
            conn.close()

    def get_branch_by_id(self, b_id):
        row = self._db_adapter().fetch_one(
            "SELECT id, city_name, username, complaint_email, phone FROM branches WHERE id = %s",
            (b_id,),
        )
        if row:
            return dict(row)
        return None

    def create_new_branch(self, username, password, city_name):
        city_clean = _normalize_branch_city_label(city_name or "")
        un = (username or "").strip()
        pw = (password or "").strip()
        if not un or not city_clean or not pw:
            return False
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if self._find_conflicting_branch_id_cursor(cursor, un, city_clean) is not None:
                return False
            cursor.execute(
                "INSERT INTO branches (username, password, city_name) VALUES (%s, %s, %s)",
                (un, self._hash_branch_password(pw), city_clean),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("create_new_branch: %s", e)
            return False
        finally:
            conn.close()

    def update_branch_password(self, b_id, new_password):
        pw = (new_password or "").strip()
        if not pw:
            return False
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE branches SET password = %s WHERE id = %s",
                (self._hash_branch_password(pw), b_id),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("update_branch_password: %s", e)
            return False
        finally:
            conn.close()

    def get_branch_row(self, branch_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT * FROM branches WHERE id = %s", (int(branch_id),))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_branch_row: %s", e)
            return None
        finally:
            conn.close()

    def get_branch_full_detail(self, branch_id: int) -> Optional[Dict[str, Any]]:
        """فرع + موقع + أوقات الدوام للوحة المؤسس."""
        br = self.get_branch_row(branch_id)
        if not br:
            return None
        loc = self.get_branch_location(branch_id) or {}
        wh_rows = self.get_working_hours(branch_id)
        hours: Dict[str, Any] = {}
        for r in wh_rows:
            hours[r["day_type"]] = dict(r)
        return {"branch": br, "location": loc, "hours": hours}

    def update_branch_fields(
        self,
        branch_id: int,
        *,
        city_name: Optional[str] = None,
        complaint_email: Optional[str] = None,
        phone: Optional[str] = None,
        username: Optional[str] = None,
    ) -> bool:
        allowed = {"city_name", "complaint_email", "phone", "username"}
        vals: Dict[str, Any] = {}
        if city_name is not None:
            vals["city_name"] = (city_name or "").strip()
        if complaint_email is not None:
            vals["complaint_email"] = (complaint_email or "").strip()
        if phone is not None:
            vals["phone"] = (phone or "").strip()
        if username is not None:
            vals["username"] = (username or "").strip()
        if not vals:
            return True
        conn = self._get_connection()
        try:
            sets = ", ".join(f"{k} = %s" for k in vals)
            params = list(vals.values()) + [int(branch_id)]
            conn.execute(f"UPDATE branches SET {sets} WHERE id = %s", tuple(params))
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("update_branch_fields: %s", e)
            return False
        finally:
            conn.close()

    def upsert_branch_location(
        self,
        branch_id: int,
        address: Optional[str] = None,
        google_maps_url: Optional[str] = None,
        gps_lat: Optional[float] = None,
        gps_lng: Optional[float] = None,
    ) -> bool:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT branch_id FROM branch_locations WHERE branch_id = %s", (int(branch_id),)
            )
            row = cursor.fetchone()
            addr = (address or "").strip() if address is not None else ""
            gurl = (google_maps_url or "").strip() if google_maps_url is not None else ""
            try:
                lat = float(gps_lat) if gps_lat is not None and str(gps_lat).strip() != "" else None
            except (TypeError, ValueError):
                lat = None
            try:
                lng = float(gps_lng) if gps_lng is not None and str(gps_lng).strip() != "" else None
            except (TypeError, ValueError):
                lng = None
            if row:
                cursor.execute(
                    """
                    UPDATE branch_locations
                    SET address = %s, google_maps_url = %s, gps_lat = %s, gps_lng = %s
                    WHERE branch_id = %s
                    """,
                    (addr, gurl, lat, lng, int(branch_id)),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO branch_locations (branch_id, address, google_maps_url, gps_lat, gps_lng)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (int(branch_id), addr, gurl, lat, lng),
                )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("upsert_branch_location: %s", e)
            return False
        finally:
            conn.close()

    def replace_working_hours(
        self,
        branch_id: int,
        weekday_open: str,
        weekday_close: str,
        friday_open: str,
        friday_close: str,
        *,
        weekday_start_2: Optional[str] = None,
        weekday_end_2: Optional[str] = None,
        friday_start_2: Optional[str] = None,
        friday_end_2: Optional[str] = None,
    ) -> bool:
        w1s = (weekday_open or "09:00").strip()
        w1e = (weekday_close or "22:00").strip()
        f1s = (friday_open or "16:00").strip()
        f1e = (friday_close or "23:00").strip()
        w2s = (weekday_start_2 or "").strip() or None
        w2e = (weekday_end_2 or "").strip() or None
        if not w2s or not w2e:
            w2s, w2e = None, None
        f2s = (friday_start_2 or "").strip() or None
        f2e = (friday_end_2 or "").strip() or None
        if not f2s or not f2e:
            f2s, f2e = None, None
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM working_hours WHERE branch_id = %s", (int(branch_id),))
            cursor.execute(
                """
                INSERT INTO working_hours (
                    branch_id, day_type,
                    start_time_1, end_time_1, start_time_2, end_time_2,
                    open_time, close_time
                )
                VALUES (%s, 'weekday', %s, %s, %s, %s, %s, %s)
                """,
                (int(branch_id), w1s, w1e, w2s, w2e, w1s, w1e),
            )
            cursor.execute(
                """
                INSERT INTO working_hours (
                    branch_id, day_type,
                    start_time_1, end_time_1, start_time_2, end_time_2,
                    open_time, close_time
                )
                VALUES (%s, 'friday', %s, %s, %s, %s, %s, %s)
                """,
                (int(branch_id), f1s, f1e, f2s, f2e, f1s, f1e),
            )
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("replace_working_hours: %s", e)
            return False
        finally:
            conn.close()

    def delete_branch(self, b_id):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM inventory WHERE product_id IN (SELECT id FROM products WHERE branch_id = %s)",
                (b_id,),
            )
            cursor.execute(
                "DELETE FROM product_variants WHERE product_id IN (SELECT id FROM products WHERE branch_id = %s)",
                (b_id,),
            )
            cursor.execute(
                "DELETE FROM product_images WHERE product_id IN (SELECT id FROM products WHERE branch_id = %s)",
                (b_id,),
            )
            cursor.execute("DELETE FROM products WHERE branch_id = %s", (b_id,))
            cursor.execute("DELETE FROM sub_categories WHERE branch_id = %s", (b_id,))
            cursor.execute("DELETE FROM main_categories WHERE branch_id = %s", (b_id,))
            cursor.execute(
                "DELETE FROM sections WHERE category_id IN (SELECT id FROM categories WHERE branch_id = %s)",
                (b_id,),
            )
            cursor.execute("DELETE FROM categories WHERE branch_id = %s", (b_id,))
            cursor.execute("DELETE FROM working_hours WHERE branch_id = %s", (b_id,))
            cursor.execute("DELETE FROM branch_locations WHERE branch_id = %s", (b_id,))
            cursor.execute("DELETE FROM complaints WHERE branch_id = %s", (b_id,))
            cursor.execute("DELETE FROM branches WHERE id = %s", (b_id,))
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("delete_branch: %s", e)
            return False
        finally:
            conn.close()

    def _seed_branch_complaint_emails(self, cursor):
        """ملء complaint_email للفروع من site_config عندما يكون العمود فارغاً."""
        try:
            from site_config.branches import BRANCHES
        except ImportError:
            BRANCHES = {}
        for city_name, info in BRANCHES.items():
            em = (info.get("manager_email") or "").strip()
            if not em:
                continue
            cursor.execute(
                """
                UPDATE branches SET complaint_email = %s
                WHERE city_name = %s
                  AND (complaint_email IS NULL OR TRIM(complaint_email) = '')
                """,
                (em, city_name),
            )

    def _seed_branch_locations_and_hours(self, conn):
        """ملء مواقع وأوقات الفروع من config (مرة واحدة لكل فرع عبر ON CONFLICT DO NOTHING)."""
        try:
            from site_config.branches import BRANCHES
        except ImportError:
            BRANCHES = {}
        cursor = conn.cursor()
        cursor.execute("SELECT id, city_name FROM branches")
        for row in cursor.fetchall():
            r = dict(row)
            bid_id = r["id"]
            city = r["city_name"]
            info = BRANCHES.get(city)
            if not info:
                continue
            wh = info.get("working_hours") or {}
            cursor.execute(
                """
                INSERT INTO branch_locations (branch_id, address, google_maps_url, gps_lat, gps_lng)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (branch_id) DO NOTHING
                """,
                (
                    bid_id,
                    info.get("address"),
                    info.get("google_maps_url"),
                    info.get("gps_lat"),
                    info.get("gps_lng"),
                ),
            )
            wo, wc = wh.get("weekday_open", "08:30"), wh.get("weekday_close", "00:00")
            fo, fc = wh.get("friday_open", "16:00"), wh.get("friday_close", "00:00")
            cursor.execute(
                """
                INSERT INTO working_hours (
                    branch_id, day_type,
                    start_time_1, end_time_1, start_time_2, end_time_2,
                    open_time, close_time
                )
                VALUES (%s, 'weekday', %s, %s, NULL, NULL, %s, %s)
                ON CONFLICT (branch_id, day_type) DO NOTHING
                """,
                (bid_id, wo, wc, wo, wc),
            )
            cursor.execute(
                """
                INSERT INTO working_hours (
                    branch_id, day_type,
                    start_time_1, end_time_1, start_time_2, end_time_2,
                    open_time, close_time
                )
                VALUES (%s, 'friday', %s, %s, NULL, NULL, %s, %s)
                ON CONFLICT (branch_id, day_type) DO NOTHING
                """,
                (bid_id, fo, fc, fo, fc),
            )

    def _find_conflicting_branch_id_cursor(
        self, cursor, username: Optional[str], city_name: Optional[str]
    ) -> Optional[int]:
        """فرع موجود بنفس اسم المستخدم أو نفس المدينة (منطقياً)."""
        un = (username or "").strip()
        if un:
            cursor.execute("SELECT id FROM branches WHERE username = %s", (un,))
            row = cursor.fetchone()
            if row:
                return int(row["id"])
        cn = _normalize_branch_city_label(city_name or "")
        if not cn:
            return None
        canon_new = _canonical_branch_city_from_input(city_name or "")
        cursor.execute("SELECT id, city_name FROM branches")
        for row in cursor.fetchall():
            eid = int(row["id"])
            ec = row["city_name"] or ""
            if _normalize_branch_city_label(ec) == cn:
                return eid
            if canon_new:
                ec_canon = _canonical_branch_city_from_input(ec)
                if ec_canon and ec_canon == canon_new:
                    return eid
        return None

    def _reattach_branch_fk(self, cursor, from_id: int, to_id: int) -> None:
        """نقل بيانات الفرع من from_id إلى to_id ثم حذف صف الفرع المكرر."""
        cursor.execute("UPDATE products SET branch_id = %s WHERE branch_id = %s", (to_id, from_id))
        cursor.execute("UPDATE categories SET branch_id = %s WHERE branch_id = %s", (to_id, from_id))
        cursor.execute(
            "UPDATE main_categories SET branch_id = %s WHERE branch_id IS NOT NULL AND branch_id = %s",
            (to_id, from_id),
        )
        cursor.execute(
            "UPDATE sub_categories SET branch_id = %s WHERE branch_id IS NOT NULL AND branch_id = %s",
            (to_id, from_id),
        )
        cursor.execute("UPDATE complaints SET branch_id = %s WHERE branch_id = %s", (to_id, from_id))

        cursor.execute("SELECT 1 FROM branch_locations WHERE branch_id = %s", (to_id,))
        has_to = cursor.fetchone() is not None
        cursor.execute("SELECT 1 FROM branch_locations WHERE branch_id = %s", (from_id,))
        has_from = cursor.fetchone() is not None
        if has_from and not has_to:
            cursor.execute(
                "UPDATE branch_locations SET branch_id = %s WHERE branch_id = %s",
                (to_id, from_id),
            )
        elif has_from and has_to:
            cursor.execute("DELETE FROM branch_locations WHERE branch_id = %s", (from_id,))

        cursor.execute("SELECT day_type FROM working_hours WHERE branch_id = %s", (from_id,))
        for r in cursor.fetchall():
            dt = r[0] if not isinstance(r, dict) else r.get("day_type")
            cursor.execute(
                "SELECT 1 FROM working_hours WHERE branch_id = %s AND day_type = %s",
                (to_id, dt),
            )
            if cursor.fetchone():
                cursor.execute(
                    "DELETE FROM working_hours WHERE branch_id = %s AND day_type = %s",
                    (from_id, dt),
                )
            else:
                cursor.execute(
                    """
                    UPDATE working_hours SET branch_id = %s
                    WHERE branch_id = %s AND day_type = %s
                    """,
                    (to_id, from_id, dt),
                )

        cursor.execute(
            "SELECT complaint_email, phone FROM branches WHERE id = %s",
            (to_id,),
        )
        k = cursor.fetchone()
        cursor.execute(
            "SELECT complaint_email, phone FROM branches WHERE id = %s",
            (from_id,),
        )
        d = cursor.fetchone()
        if k and d:
            kem = (k["complaint_email"] or "").strip()
            dem = (d["complaint_email"] or "").strip()
            kph = (k["phone"] or "").strip()
            dph = (d["phone"] or "").strip()
            new_em = kem or dem
            new_ph = kph or dph
            if (new_em != kem) or (new_ph != kph):
                cursor.execute(
                    "UPDATE branches SET complaint_email = %s, phone = %s WHERE id = %s",
                    (new_em or "", new_ph or "", to_id),
                )

        cursor.execute("DELETE FROM branches WHERE id = %s", (from_id,))

    def _merge_duplicate_branches(self, cursor) -> None:
        """
        دمج صفوف branches المكررة (نفس المفتاح القياسي أو نفس الاسم بعد التطبيع).
        يُبقى أقل id ويُعاد ربط المنتجات والأقسام دون حذفها.
        """
        cursor.execute("SELECT id, city_name FROM branches ORDER BY id")
        rows = cursor.fetchall()
        buckets: Dict[str, List[int]] = {}
        for row in rows:
            bid = int(row["id"])
            city = row["city_name"] or ""
            cn = _normalize_branch_city_label(city)
            if not cn:
                key = f"id:{bid}"
            else:
                key = _branch_dedupe_key(city)
            buckets.setdefault(key, []).append(bid)

        for _key, ids in buckets.items():
            if len(ids) <= 1:
                continue
            keeper = min(ids)
            for dup in sorted(ids):
                if dup == keeper:
                    continue
                self._reattach_branch_fk(cursor, dup, keeper)

    # ═══════════════════════════════════════════════════════════════
    # شكاوى | طلبات منتجات | إعدادات النظام | مواقع وأوقات
    # ═══════════════════════════════════════════════════════════════

    def get_working_hours(self, branch_id):
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM working_hours WHERE branch_id = %s ORDER BY day_type",
                (branch_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_working_hours: %s", e)
            return []
        finally:
            conn.close()

    def get_branch_location(self, branch_id):
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT * FROM branch_locations WHERE branch_id = %s", (branch_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_branch_location: %s", e)
            return None
        finally:
            conn.close()

    def get_branch_location_by_city_name(self, city_name):
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT bl.* FROM branch_locations bl
                JOIN branches b ON b.id = bl.branch_id
                WHERE b.city_name = %s
                """,
                (city_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_branch_location_by_city_name: %s", e)
            return None
        finally:
            conn.close()

    def get_branch_id_by_city_name(self, city_name):
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT id FROM branches WHERE city_name = %s", (city_name,))
            row = cursor.fetchone()
            return int(row["id"]) if row else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_branch_id_by_city_name: %s", e)
            return None
        finally:
            conn.close()

    def get_branch_complaint_email(self, branch_id: Optional[int]) -> Optional[str]:
        """إيميل الشكاوى المسجل للفرع في قاعدة البيانات."""
        if branch_id is None:
            return None
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT complaint_email FROM branches WHERE id = %s",
                (int(branch_id),),
            )
            row = cursor.fetchone()
            if not row:
                return None
            em = (row["complaint_email"] or "").strip()
            return em if em else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_branch_complaint_email: %s", e)
            return None
        finally:
            conn.close()

    def get_branch_users(self) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    id,
                    city_name,
                    username,
                    CASE WHEN TRIM(COALESCE(password, '')) != '' THEN 1 ELSE 0 END AS has_password
                FROM branches
                ORDER BY id
                """
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_branch_users: %s", e)
            return []
        finally:
            conn.close()

    def update_branch_user(self, branch_id: int, username: str, password: Optional[str]) -> bool:
        username = (username or "").strip()
        password = (password or "").strip()
        if not username:
            return False
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            params: List[Any] = [username]
            set_parts = ["username = %s"]
            if password:
                set_parts.append("password = %s")
                params.append(self._hash_branch_password(password))
            params.append(branch_id)
            cursor.execute(
                f"UPDATE branches SET {', '.join(set_parts)} WHERE id = %s",
                tuple(params),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            # rollback صريح كان موجود في النسخة الأصلية — نُبقيه ونضيف log
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("update_branch_user: %s", e)
            return False
        finally:
            conn.close()

    def migrate_branch_passwords_to_hashes(self) -> int:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT id, password FROM branches").fetchall()
            updates: List[Tuple[str, int]] = []
            for row in rows:
                stored = (row["password"] or "").strip()
                if stored and not self._branch_password_is_hashed(stored):
                    updates.append((self._hash_branch_password(stored), int(row["id"])))
            if not updates:
                return 0
            conn.executemany(
                "UPDATE branches SET password = %s WHERE id = %s",
                updates,
            )
            conn.commit()
            return len(updates)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("migrate_branch_passwords_to_hashes: %s", e)
            return 0
        finally:
            conn.close()