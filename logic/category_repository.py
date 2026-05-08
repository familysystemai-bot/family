# -*- coding: utf-8 -*-
"""
CategoryRepositoryMixin — إدارة الفئات والأقسام.

التحديثات في هذا الإصدار:
- إضافة _safe_rollback_pg() واستدعائها في كل دالة CRUD عند الفشل.
- تحسين add_sub_category: استخدام lastrowid (الموثوق في الكود الجديد عبر
  lastval() على PostgreSQL) كمصدر أساسي للـ id، مع SELECT احتياطي فقط
  إن لم يتوفر lastrowid (يحافظ على نفس السلوك السابق).
- نفس الواجهة العامة محفوظة بالكامل.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from logic.chat_semantic_expand import section_search_variants
from logic.db_adapter import DBAdapter

logger = logging.getLogger(__name__)


class CategoryRepositoryMixin:
    """Mixin: يُدمج في DatabaseManager — يستخدم self._get_connection() فقط."""

    def _db_adapter(self) -> DBAdapter:
        return DBAdapter(sqlite_path=getattr(self, "db_path", None))

    def _safe_rollback_pg(self, conn) -> None:
        """rollback آمن للـ PostgreSQL فقط."""
        if getattr(self, "db_type", None) != "postgres":
            return
        try:
            conn.rollback()
        except Exception as e:
            logger.warning("rollback failed in category_repository: %s", e)

    def get_main_categories(self):
        return self._db_adapter().fetch_all("SELECT * FROM main_categories ORDER BY id")

    def get_main_categories_by_branch(self, branch_id: int):
        """إرجاع صفوف main_categories التابعة لفرع محدد."""
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM main_categories WHERE branch_id = %s ORDER BY id",
                (branch_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_main_categories_by_branch: %s", e)
            return []
        finally:
            conn.close()

    def get_main_category_by_id(self, category_id: int) -> Optional[Dict[str, Any]]:
        return self._db_adapter().fetch_one(
            "SELECT * FROM main_categories WHERE id = %s",
            (int(category_id),),
        )

    def add_main_category(self, name, branch_id=None):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO main_categories (name, branch_id) VALUES (%s, %s)", (name, branch_id))
            conn.commit()
            return True
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("add_main_category: %s", e)
            return False
        finally:
            conn.close()

    def add_sub_category(self, main_id, branch_id, name):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO sub_categories (main_id, branch_id, name) VALUES (%s, %s, %s)",
                (main_id, branch_id, name),
            )
            # نحاول استخدام lastrowid أولاً (موثوق ويعمل على القاعدتين عبر الـ wrapper)
            sub_id = cursor.lastrowid
            if not sub_id:
                # احتياطي: SELECT بنفس المنطق السابق (لتطابق السلوك)
                cursor.execute(
                    "SELECT id FROM sub_categories WHERE branch_id = %s AND name = %s",
                    (branch_id, name),
                )
                row = cursor.fetchone()
                sub_id = int(row["id"]) if row else None
            conn.commit()
            return int(sub_id) if sub_id else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("add_sub_category: %s", e)
            return None
        finally:
            conn.close()

    def get_sections_by_category(self, category_id: int):
        """
        جلب الأقسام المرتبطة بالفئة (main_id) من sub_categories.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM sub_categories WHERE main_id = %s ORDER BY id",
                (category_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_sections_by_category: %s", e)
            return []
        finally:
            conn.close()

    def delete_branch_subcategory_and_products(self, sub_category_id: int) -> bool:
        """
        حذف قسم فرعي (sub_categories) وجميع المنتجات المرتبطة به داخل معاملة واحدة.
        يطابق المنتجات عبر sub_id أو section_id (نفس المعرف المستخدم في لوحة الفرع).
        """
        sid = int(sub_category_id)
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sub_categories WHERE id = %s", (sid,))
            if not cursor.fetchone():
                return False
            cursor.execute(
                "SELECT id FROM products WHERE sub_id = %s OR section_id = %s",
                (sid, sid),
            )
            product_ids = [int(r["id"]) for r in cursor.fetchall()]
            if product_ids:
                ph = ",".join("%s" * len(product_ids))
                cursor.execute(f"DELETE FROM product_variants WHERE product_id IN ({ph})", product_ids)
                cursor.execute(f"DELETE FROM product_images WHERE product_id IN ({ph})", product_ids)
                cursor.execute(f"DELETE FROM inventory WHERE product_id IN ({ph})", product_ids)
                cursor.execute(f"DELETE FROM products WHERE id IN ({ph})", product_ids)
            cursor.execute("DELETE FROM sub_categories WHERE id = %s", (sid,))
            conn.commit()
            return True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("delete_branch_subcategory_and_products: %s", e)
            return False
        finally:
            conn.close()

    def _extract_section_search_words(self, message: str) -> List[str]:
        """كل الكلمات المفيدة بعد إزالة الضجيج (قسم، عندكم، فيه، هل، …)."""
        t = (message or "").strip()
        for ch in ("\u061f", "\x3f", "\u060c", ","):
            t = t.replace(ch, " ")
        noise = frozenset(
            {
                "قسم",
                "عندكم",
                "فيه",
                "هل",
                "القسم",
                "أقسام",
                "اقسام",
                "الأقسام",
                "الاقسام",
                "عندك",
                "فيها",
                "في",
                "وين",
                "فين",
                "ممكن",
                "ابغى",
                "أبي",
                "عندنا",
                "يعني",
                "هذا",
                "هذي",
                "و",
            }
        )
        tokens = []
        for raw in t.split():
            w = raw.strip()
            if not w or w in noise or len(w) < 2:
                continue
            if w.startswith("ال") and len(w) > 3:
                w = w[2:]
            tokens.append(w)
        return tokens

    @staticmethod
    def _section_word_variants(w: str) -> List[str]:
        """تنويعات للبحث: المفرد/الجمع (نهاية ات / ين)."""
        w = (w or "").strip()
        if len(w) < 2:
            return []
        vs: List[str] = []
        seen = set()

        def add(x: str) -> None:
            if len(x) >= 2 and x not in seen:
                seen.add(x)
                vs.append(x)

        add(w)
        if w.startswith("ال") and len(w) > 3:
            w = w[2:]
            add(w)
        if len(w) > 3 and w.endswith("ات"):
            add(w[:-2])
        if len(w) > 3 and w.endswith("ين"):
            add(w[:-2])
        return vs

    def get_sections_by_name(self, message: str) -> List[Dict[str, Any]]:
        """
        يبحث فقط في main_categories + sub_categories (نفس لوحة التحكم).
        الترتيب: 1) تطابق sub_categories.name  2) ثم main_categories.name
        مع توسيع مناسبات عامة (زواج، مناسبة، …) إلى كلمات قريبة.
        """
        words = self._extract_section_search_words(message)
        if not words:
            return []
        all_vars: List[str] = []
        for w in words:
            all_vars.extend(self._section_word_variants(w))
        all_vars = list(dict.fromkeys([v for v in all_vars if len(v) >= 2]))
        all_vars = section_search_variants(message, all_vars)
        if not all_vars:
            return []
        conn = self._get_connection()
        out: List[Dict[str, Any]] = []
        seen = set()
        try:
            for var in all_vars:
                like = f"%{var}%"
                cur = conn.execute(
                    """
                    SELECT sc.name AS section_name, mc.name AS category_name,
                           sc.branch_id, b.city_name AS branch_city_name
                    FROM sub_categories sc
                    JOIN main_categories mc ON mc.id = sc.main_id
                    JOIN branches b ON b.id = sc.branch_id
                    WHERE sc.name LIKE %s
                    """,
                    (like,),
                )
                for row in cur.fetchall():
                    d = dict(row)
                    key = (d.get("section_name"), d.get("branch_id"))
                    if key not in seen:
                        seen.add(key)
                        out.append(d)
            if not out:
                for var in all_vars:
                    like = f"%{var}%"
                    cur = conn.execute(
                        """
                        SELECT sc.name AS section_name, mc.name AS category_name,
                               sc.branch_id, b.city_name AS branch_city_name
                        FROM sub_categories sc
                        JOIN main_categories mc ON mc.id = sc.main_id
                        JOIN branches b ON b.id = sc.branch_id
                        WHERE mc.name LIKE %s
                        """,
                        (like,),
                    )
                    for row in cur.fetchall():
                        d = dict(row)
                        key = (d.get("section_name"), d.get("branch_id"))
                        if key not in seen:
                            seen.add(key)
                            out.append(d)
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("get_sections_by_name: %s", e)
        finally:
            conn.close()
        return out

    def add_category(self, branch_id: int, name: str) -> Optional[int]:
        """إضافة Category مرتبطة بالفرع."""
        name = (name or "").strip()
        if not name:
            return None
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO categories (branch_id, name) VALUES (%s, %s)",
                (branch_id, name),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("add_category: %s", e)
            return None
        finally:
            conn.close()

    def add_section(self, category_id: int, name: str) -> Optional[int]:
        """إضافة Section مرتبطة بالفئة."""
        name = (name or "").strip()
        if not name:
            return None
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO sections (category_id, name) VALUES (%s, %s)",
                (category_id, name),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("add_section: %s", e)
            return None
        finally:
            conn.close()

    def _get_branch_id_for_section(self, section_id: int) -> Optional[int]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT c.branch_id
                FROM sections s
                JOIN categories c ON c.id = s.category_id
                WHERE s.id = %s
                """,
                (section_id,),
            )
            row = cursor.fetchone()
            return int(row["branch_id"]) if row else None
        except Exception as e:
            self._safe_rollback_pg(conn)
            logger.exception("_get_branch_id_for_section: %s", e)
            return None
        finally:
            conn.close()

    def delete_section(self, section_id: int) -> bool:
        """حذف Section وما تحته من منتجات + variants + images."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM products WHERE section_id = %s", (section_id,))
            product_rows = cursor.fetchall()
            product_ids = [int(r["id"]) for r in product_rows]
            if product_ids:
                cursor.execute(
                    f"DELETE FROM product_variants WHERE product_id IN ({','.join(['%s']*len(product_ids))})",
                    tuple(product_ids),
                )
                cursor.execute(
                    f"DELETE FROM product_images WHERE product_id IN ({','.join(['%s']*len(product_ids))})",
                    tuple(product_ids),
                )
                cursor.execute(
                    f"DELETE FROM products WHERE id IN ({','.join(['%s']*len(product_ids))})",
                    tuple(product_ids),
                )
            cursor.execute("DELETE FROM sections WHERE id = %s", (section_id,))
            conn.commit()
            return True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("delete_section: %s", e)
            return False
        finally:
            conn.close()

    def delete_category(self, category_id: int) -> bool:
        """حذف Category وما تحته من sections ومنتجات."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sections WHERE category_id = %s", (category_id,))
            section_rows = cursor.fetchall()
            section_ids = [int(r["id"]) for r in section_rows]
            if section_ids:
                for sid in section_ids:
                    self.delete_section(sid)
            cursor.execute("DELETE FROM categories WHERE id = %s", (category_id,))
            conn.commit()
            return True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("delete_category: %s", e)
            return False
        finally:
            conn.close()