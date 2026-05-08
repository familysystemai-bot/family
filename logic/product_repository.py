# -*- coding: utf-8 -*-
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from logic.db_adapter import DBAdapter

# مصطلحات عامة في رسالة المستخدم → تلميحات أسماء أقسام للبحث (تُطبَّق عبر _resolve_canonical_subcategory_name)
_GENERIC_SECTION_HINTS_FOR_FOLLOWUP: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("عربة ادوات", ("اثاث", "أثاث", "ادوات", "أدوات", "مطبخ", "منزلية")),
    ("عربة أدوات", ("اثاث", "أثاث", "ادوات", "أدوات", "مطبخ", "منزلية")),
    ("عربة", ("اثاث", "أثاث", "مفروشات", "ادوات", "أدوات", "منزلية")),
    ("أدوات منزلية", ("ادوات", "أدوات", "مطبخ", "منزلية")),
    ("ادوات منزلية", ("ادوات", "أدوات", "مطبخ", "منزلية")),
    ("أدوات", ("ادوات", "أدوات", "مطبخ", "منزلية")),
    ("ادوات", ("ادوات", "أدوات", "مطبخ", "منزلية")),
    ("اثاث", ("اثاث", "أثاث", "مفروشات")),
    ("أثاث", ("اثاث", "أثاث", "مفروشات")),
)


def filter_rows_keyword_in_product_name(
    rows: List[Dict[str, Any]], keyword: str
) -> List[Dict[str, Any]]:
    """
    يحتفظ بالصفوف التي يظهر فيها keyword (بعد تطبيع عربي) ضمن اسم المنتج فقط.
    يمنع نتائج مضلّلة (مثل تطابق الوصف/القسم دون الاسم).
    """
    k = _normalize_arabic_for_search(keyword or "")
    if len(k) < 2:
        return list(rows or [])
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        name = _normalize_arabic_for_search((r.get("product_name") or ""))
        if k in name:
            out.append(r)
    return out


def _normalize_arabic_for_search(s: str) -> str:
    """
    تطبيع عربي للبحث (بدون أعمدة جديدة): أ/إ/آ→ا، إزالة التشكيل، توحيد خفيف للحروف.
    """
    s = (s or "").strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u0640", "")
    for a, b in (
        ("\u0623", "\u0627"),
        ("\u0625", "\u0627"),
        ("\u0622", "\u0627"),
        ("\u0671", "\u0627"),
        ("\u0624", "\u0648"),
        ("\u0626", "\u064a"),
        ("\u0649", "\u064a"),
    ):
        s = s.replace(a, b)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = "".join(ch for ch in s if ch not in ("\u200c", "\u200d", "\ufeff"))
    s = " ".join(s.split())
    return s.strip()


def _expand_arabic_like_variants(needle: str) -> List[str]:
    """
    نصوص فريدة لـ LIKE حتى يطابق المستخدم والبيانات عند اختلاف ألف/همزة/تشكيل.
    """
    raw = (needle or "").strip()
    if len(raw) < 2:
        return []
    n = _normalize_arabic_for_search(raw)
    seen: Dict[str, None] = {}
    out: List[str] = []
    for t in (raw, n):
        if len(t) >= 2 and t not in seen:
            seen[t] = None
            out.append(t)
    for t in list(out):
        if len(t) < 2:
            continue
        if t[0] == "\u0627":
            for p in ("\u0623", "\u0625", "\u0622"):
                alt = p + t[1:]
                if len(alt) >= 2 and alt not in seen:
                    seen[alt] = None
                    out.append(alt)
        elif t[0] in ("\u0623", "\u0625", "\u0622"):
            alt = "\u0627" + t[1:]
            if len(alt) >= 2 and alt not in seen:
                seen[alt] = None
                out.append(alt)
    return out[:12]


def _sql_or_likes_expr(expr_sql: str, variants: List[str]) -> Tuple[str, List[str]]:
    """WHERE فرعي: (expr LIKE %s OR expr LIKE %s ...) لعدة تنويعات."""
    parts = []
    params: List[str] = []
    for v in variants:
        parts.append(f"{expr_sql} LIKE %s")
        params.append(f"%{v}%")
    return "(" + " OR ".join(parts) + ")", params


def _normalize_variant_rows(variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    مقاس + لون + كمية فقط (بدون سعر من النموذج).
    يهمل الصف الفارغ تماماً ويجمع الصفوف المكررة (نفس المقاس+اللون).
    عمود price في قاعدة البيانات يُعبّأ من سعر المنتج الأساسي فقط.
    """
    merged: Dict[Tuple[str, str], int] = {}
    for v in variants or []:
        if not isinstance(v, dict):
            continue
        size = (v.get("size") or "").strip()
        color = (v.get("color") or "").strip()
        try:
            qty = int(float(v.get("quantity", 0) or 0))
        except (TypeError, ValueError):
            qty = 0
        if not size and not color and qty <= 0:
            continue
        key = (size, color)
        merged[key] = merged.get(key, 0) + qty
    return [
        {"size": sz, "color": cl, "quantity": q}
        for (sz, cl), q in sorted(merged.items(), key=lambda x: (x[0][0], x[0][1]))
    ]


class ProductRepositoryMixin:
    """Mixin: يُدمج في DatabaseManager — يستخدم self._get_connection() فقط."""
    def _db_adapter(self) -> DBAdapter:
        return DBAdapter(sqlite_path=getattr(self, "db_path", None))

    def add_product_from_section(
        self,
        section_id: int,
        product_name: str,
        description: str,
        variants: List[Dict[str, Any]],
        image_paths: List[str],
        sku: Optional[str] = None,
        product_price: float = 0.0,
    ) -> Optional[int]:
        """
        حفظ Product عبر section_id (SubCategory):
        - products.price = سعر المنتج الأساسي (من النموذج)
        - product_variants: مقاس + لون + كمية؛ عمود price يُنسخ من سعر المنتج (لا يُستقبل من النموذج)
        - product_images: حتى 3 صور
        - inventory (للتوافق): نفس variants داخل جدول inventory القديم
        """
        product_name = (product_name or "").strip()
        description = description or ""
        if not product_name:
            return None

        try:
            product_price_val = float(product_price)
        except (TypeError, ValueError):
            product_price_val = 0.0

        sku_val = (sku or "").strip() or None

        variants = variants or []
        image_paths = image_paths or []

        if len(image_paths) > 3:
            return None

        cleaned_variants = _normalize_variant_rows(variants)

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # branch_id من sub_categories نفسها
            cursor.execute(
                "SELECT branch_id FROM sub_categories WHERE id = %s",
                (section_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            branch_id = int(row["branch_id"])

            cursor.execute(
                """
                INSERT INTO products (
                    branch_id, sub_id, section_id,
                    product_name, description, price,
                    img1, img2, img3, sku
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    branch_id,
                    section_id,   # sub_id
                    section_id,   # section_id (العمود الجديد)
                    product_name,
                    description,
                    product_price_val,
                    image_paths[0] if len(image_paths) >= 1 else None,
                    image_paths[1] if len(image_paths) >= 2 else None,
                    image_paths[2] if len(image_paths) >= 3 else None,
                    sku_val,
                ),
            )
            product_id = int(cursor.lastrowid)

            # product_images
            for idx, p in enumerate(image_paths[:3], start=1):
                cursor.execute(
                    """
                    INSERT INTO product_images (product_id, image_path, position)
                    VALUES (%s, %s, %s)
                    """,
                    (product_id, p, idx),
                )

            # product_variants + inventory (للتوافق)
            for v in cleaned_variants:
                cursor.execute(
                    """
                    INSERT INTO product_variants (product_id, size, color, price, quantity)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (product_id, v["size"], v["color"], product_price_val, v["quantity"]),
                )
                cursor.execute(
                    """
                    INSERT INTO inventory (product_id, color, size, quantity)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (product_id, v["color"], v["size"], v["quantity"]),
                )

            conn.commit()
            return product_id
        except Exception:
            conn.rollback()
            return None
        finally:
            conn.close()

    def get_product_variants(self, product_id: int) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT size, color, price, quantity
                FROM product_variants
                WHERE product_id = %s
                ORDER BY size, color
                """,
                (product_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_product_images(self, product_id: int) -> List[str]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT image_path
                FROM product_images
                WHERE product_id = %s
                ORDER BY position
                """,
                (product_id,),
            )
            images = []
            for row in cursor.fetchall():
                v = (row["image_path"] or "").strip()
                if v:
                    images.append(v)
            if images:
                return images
            # fallback على الأعمدة القديمة في products
            cursor = conn.execute(
                "SELECT img1, img2, img3 FROM products WHERE id = %s",
                (product_id,),
            )
            row = cursor.fetchone()
            if not row:
                return []
            out = []
            for k in ("img1", "img2", "img3"):
                v = (row[k] or "").strip() if row[k] is not None else ""
                if v:
                    out.append(v)
            return out
        finally:
            conn.close()

    def decrement_variant_quantity(
        self,
        product_id: int,
        size: str,
        color: str,
        qty: int = 1,
    ) -> bool:
        """
        خصم الكمية من product_variants عند تأكيد الطلب.
        يمنع الخصم إذا الرصيد غير كافٍ.
        """
        size = (size or "").strip()
        color = (color or "").strip()
        try:
            qty_int = int(qty)
        except (TypeError, ValueError):
            qty_int = 1
        if qty_int <= 0 or not size or not color:
            return False

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT quantity
                FROM product_variants
                WHERE product_id = %s AND size = %s AND color = %s
                """,
                (product_id, size, color),
            )
            row = cursor.fetchone()
            if not row:
                return False
            current_qty = int(row["quantity"])
            if current_qty < qty_int:
                return False
            cursor.execute(
                """
                UPDATE product_variants
                SET quantity = quantity - %s
                WHERE product_id = %s AND size = %s AND color = %s
                """,
                (qty_int, product_id, size, color),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════
    # دوال الفروع (Branches)
    # ═══════════════════════════════════════════════════════════════

    def count_products_total(self) -> int:
        row = self._db_adapter().fetch_one("SELECT COUNT(*) AS c FROM products")
        return int(row["c"]) if row else 0

    def list_all_products_for_founder(self, limit: int = 800) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    p.id AS id,
                    p.product_name,
                    p.description,
                    p.price,
                    p.branch_id,
                    b.city_name AS branch_name,
                    COALESCE(SUM(pv.quantity), 0) AS total_quantity,
                    COALESCE(MIN(pi.image_path), p.img1) AS image_path
                FROM products p
                JOIN branches b ON b.id = p.branch_id
                LEFT JOIN product_variants pv ON pv.product_id = p.id
                LEFT JOIN product_images pi ON pi.product_id = p.id AND pi.position = 1
                GROUP BY p.id, b.id
                ORDER BY p.id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _search_needle_tokens(self, needle: str) -> List[str]:
        """كلمات مفردة للبحث الاحتياطي عندما لا يطابق الجملة كاملة."""
        t = (needle or "").strip()
        for ch in ("\u061f", "\x3f", "\u060c", ","):
            t = t.replace(ch, " ")
        return [w for w in t.split() if len(w) >= 2]

    def _search_products_one_like(self, needle: str, limit: int) -> List[Dict[str, Any]]:
        """استعلام على عمود اسم المنتج فقط: بادئة أو احتواء (يُكمّل بفلتر اسم في Python)."""
        needle = (needle or "").strip()
        if len(needle) < 2:
            return []
        variants = _expand_arabic_like_variants(needle)
        if not variants:
            return []
        like_parts: List[str] = []
        params: List[Any] = []
        for v in variants:
            pref = f"{v}%"
            ins = f"%{v}%"
            like_parts.append("(p.product_name LIKE %s OR p.product_name LIKE %s)")
            params.extend([pref, ins])
        where_sql = "(" + " OR ".join(like_parts) + ")"
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                f"""
                SELECT
                    p.id AS product_id,
                    p.product_name,
                    p.description,
                    p.price,
                    p.img1,
                    p.img2,
                    p.img3,
                    p.branch_id,
                    b.city_name AS branch_city_name,
                    mc.id AS category_id,
                    mc.name AS category_name,
                    sc.id AS section_id,
                    sc.name AS section_name
                FROM products p
                LEFT JOIN branches b ON b.id = p.branch_id
                LEFT JOIN sub_categories sc ON sc.id = p.sub_id
                LEFT JOIN main_categories mc ON mc.id = sc.main_id
                WHERE
                    {where_sql}
                ORDER BY p.id DESC
                LIMIT %s
                """,
                tuple(params) + (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def search_products(self, needle: str, limit: int = 30):
        """
        بحث في اسم المنتج فقط (LIKE بادئة أو احتواء)، ثم تجزئة الكلمات مع
        التحقق أن كل كلمة بحث تظهر في اسم المنتج (لا وصف/قسم فقط).
        """
        needle = (needle or "").strip()
        if len(needle) < 2:
            return []
        rows = self._search_products_one_like(needle, limit)
        if rows:
            for cand in [needle] + self._search_needle_tokens(needle):
                c = (cand or "").strip()
                if len(c) < 2:
                    continue
                fr = filter_rows_keyword_in_product_name(rows, c)
                if fr:
                    return fr[:limit]
        tokens = self._search_needle_tokens(needle)
        if len(tokens) <= 1:
            return []
        seen = set()
        merged: List[Dict[str, Any]] = []
        for tok in tokens[:10]:
            if len(tok) < 2:
                continue
            for r in self._search_products_one_like(tok, limit):
                ok = filter_rows_keyword_in_product_name([r], tok)
                if not ok:
                    continue
                r = ok[0]
                pid = int(r["product_id"])
                if pid not in seen:
                    seen.add(pid)
                    merged.append(r)
                if len(merged) >= limit:
                    return merged[:limit]
        return merged[:limit]

    def list_products_for_main_category_name(
        self, main_category_name: str, limit: int = 24
    ) -> List[Dict[str, Any]]:
        """منتجات مرتبطة بفئة رئيسية (اسم main_categories) — للشات/المنسّق."""
        cat = (main_category_name or "").strip()
        if not cat:
            return []
        like_pat = f"%{cat}%"
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    p.id AS product_id,
                    p.product_name,
                    p.description,
                    p.price,
                    p.img1,
                    p.img2,
                    p.img3,
                    p.branch_id,
                    b.city_name AS branch_city_name,
                    mc.id AS category_id,
                    mc.name AS category_name,
                    sc.id AS section_id,
                    sc.name AS section_name
                FROM products p
                JOIN branches b ON b.id = p.branch_id
                JOIN sub_categories sc ON sc.id = p.sub_id
                JOIN main_categories mc ON mc.id = sc.main_id
                WHERE p.sub_id IS NOT NULL
                  AND (mc.name = %s OR mc.name LIKE %s)
                ORDER BY p.id DESC
                LIMIT %s
                """,
                (cat, like_pat, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def chat_tiered_product_search(self, needle: str, per_tier_limit: int = 4) -> Tuple[List[Dict[str, Any]], bool]:
        """
        بحث للشات — ترتيب المستويات حتى أول نتائج (الاسم أولاً لتقليل الخلط):
        1) اسم المنتج
        2) الوصف
        3) sub_categories.name (قسم)
        4) main_categories.name (فئة)
        (فقط main_categories + sub_categories، بلا categories/sections)
        يعيد (قائمة صفوف، هل يوجد أكثر من 3 نتائج في المستوى المعتمد).
        """
        needle = (needle or "").strip()
        if len(needle) < 2:
            return [], False
        variants = _expand_arabic_like_variants(needle)
        if not variants:
            return [], False
        base_sql = """
                SELECT
                    p.id AS product_id,
                    p.product_name,
                    p.description,
                    p.price,
                    p.img1,
                    p.img2,
                    p.img3,
                    p.branch_id,
                    b.city_name AS branch_city_name,
                    mc.id AS category_id,
                    mc.name AS category_name,
                    sc.id AS section_id,
                    sc.name AS section_name
                FROM products p
                LEFT JOIN branches b ON b.id = p.branch_id
                LEFT JOIN sub_categories sc ON sc.id = p.sub_id
                LEFT JOIN main_categories mc ON mc.id = sc.main_id
        """
        tier_exprs = (
            "COALESCE(p.product_name, '')",
            "COALESCE(p.description, '')",
            "COALESCE(sc.name, '')",
            "COALESCE(mc.name, '')",
        )
        tiers: List[Tuple[str, Tuple[Any, ...]]] = []
        for expr in tier_exprs:
            wsql, wparams = _sql_or_likes_expr(expr, variants)
            tiers.append(
                (
                    f"""
                WHERE {wsql}
                ORDER BY p.id DESC
                LIMIT %s
            """,
                    tuple(wparams) + (per_tier_limit,),
                )
            )
        conn = self._get_connection()
        try:
            for tier_sql, params in tiers:
                cursor = conn.execute(base_sql + tier_sql, params)
                rows = [dict(row) for row in cursor.fetchall()]
                if not rows:
                    continue
                has_more = len(rows) > 3
                return rows, has_more
            return [], False
        finally:
            conn.close()

    def _normalize_arabic_label_for_section_match(self, s: str) -> str:
        """تطبيع خفيف للعربية قبل مطابقة اسم القسم مع sub_categories.name."""
        s = (s or "").strip()
        if not s:
            return ""
        s = unicodedata.normalize("NFKC", s)
        s = s.replace("\u0640", "")
        for a, b in (
            ("\u0623", "\u0627"),
            ("\u0625", "\u0627"),
            ("\u0622", "\u0627"),
            ("\u0671", "\u0627"),
        ):
            s = s.replace(a, b)
        s = "".join(ch for ch in s if ch not in ("\u200c", "\u200d", "\ufeff"))
        s = " ".join(s.split())
        return s.strip()

    def _resolve_canonical_subcategory_name(self, section_name: str) -> str:
        """
        يحوّل اسم القسم القادم من الجلسة (last_section) إلى الاسم كما في sub_categories.name
        قدر الإمكان: تطابق مباشر، تنويعات مفرد/جمع، أو احتواء جزئي.
        """
        raw = (section_name or "").strip()
        if not raw:
            return raw
        n = self._normalize_arabic_label_for_section_match(raw)
        if not n:
            return raw
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sub_categories WHERE TRIM(name) = TRIM(%s) LIMIT 1",
                (n,),
            ).fetchone()
            if row:
                return (row["name"] or "").strip() or n
            row = conn.execute(
                "SELECT name FROM sub_categories WHERE TRIM(name) = TRIM(%s) LIMIT 1",
                (raw,),
            ).fetchone()
            if row:
                return (row["name"] or "").strip() or n
            for v in self._section_word_variants(n):
                if len(v) < 2:
                    continue
                row = conn.execute(
                    "SELECT name FROM sub_categories WHERE TRIM(name) = TRIM(%s) LIMIT 1",
                    (v,),
                ).fetchone()
                if row:
                    return (row["name"] or "").strip() or n
            row = conn.execute(
                """
                SELECT name FROM sub_categories
                WHERE TRIM(%s) LIKE '%' || TRIM(name) || '%'
                ORDER BY LENGTH(TRIM(name)) DESC
                LIMIT 1
                """,
                (n,),
            ).fetchone()
            if row:
                return (row["name"] or "").strip() or n
            row = conn.execute(
                """
                SELECT name FROM sub_categories
                WHERE TRIM(name) LIKE '%' || TRIM(%s) || '%'
                ORDER BY LENGTH(TRIM(name)) ASC
                LIMIT 1
                """,
                (n,),
            ).fetchone()
            if row:
                return (row["name"] or "").strip() or n
        finally:
            conn.close()
        return n

    def section_names_for_followup(self, last_section: str, user_message: str) -> List[str]:
        """
        قائمة أسماء أقسام مُطبَّعة للبحث: آخر قسم من الجلسة ثم تلميحات من كلمات عامة
        (مثل عربة/أدوات) تُربَط بأقسام موجودة في sub_categories عند الإمكان.
        """
        out: List[str] = []
        seen = set()

        def add(name: str) -> None:
            x = (name or "").strip()
            if not x:
                return
            canon = self._resolve_canonical_subcategory_name(x)
            if not canon:
                return
            if canon not in seen:
                seen.add(canon)
                out.append(canon)

        add(last_section or "")
        blob = self._normalize_arabic_label_for_section_match(user_message or "")
        for phrase, hints in sorted(
            _GENERIC_SECTION_HINTS_FOR_FOLLOWUP, key=lambda ph: -len(ph[0])
        ):
            if phrase in blob:
                for h in hints:
                    add(h)
        return out

    def search_products_in_section(
        self, section_name: str, keyword: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """بحث منتجات ضمن قسم مطابق عبر sub_categories (نفس لوحة التحكم)."""
        section_name = (section_name or "").strip()
        keyword = (keyword or "").strip()
        if not section_name or not keyword:
            return []
        section_name = self._resolve_canonical_subcategory_name(section_name)
        if not section_name:
            return []
        parts = [p.strip() for p in keyword.split() if len(p.strip()) >= 1]
        if not parts:
            return []
        uniq_kw: List[str] = []
        for w in parts:
            for v in self._section_word_variants(w):
                if len(v) >= 2:
                    uniq_kw.append(v)
        uniq_kw = list(dict.fromkeys(uniq_kw))
        if not uniq_kw:
            return []
        sec_like = f"%{section_name}%"
        kw_parts: List[str] = []
        kw_params: List[str] = []
        seen_like: set = set()
        for kw in uniq_kw:
            for v in _expand_arabic_like_variants(kw):
                if len(v) < 2:
                    continue
                lk = f"%{v}%"
                pref = f"{v}%"
                key = ("pd", lk)
                if key in seen_like:
                    continue
                seen_like.add(key)
                kw_parts.append("(p.product_name LIKE %s OR p.product_name LIKE %s)")
                kw_params.extend([pref, lk])
        if not kw_parts:
            return []
        kw_clause = "(" + " OR ".join(kw_parts) + ")"
        conn = self._get_connection()
        out: List[Dict[str, Any]] = []
        seen_ids = set()
        try:
            sql_old = f"""
                SELECT
                    p.id AS product_id,
                    p.product_name,
                    p.description,
                    p.price,
                    p.img1,
                    p.img2,
                    p.img3,
                    p.branch_id,
                    b.city_name AS branch_city_name,
                    mc.id AS category_id,
                    mc.name AS category_name,
                    sc.id AS section_id,
                    sc.name AS section_name
                FROM products p
                JOIN branches b ON b.id = p.branch_id
                JOIN sub_categories sc ON sc.id = p.sub_id
                JOIN main_categories mc ON mc.id = sc.main_id
                WHERE p.sub_id IS NOT NULL
                  AND (sc.name = %s OR sc.name LIKE %s)
                  AND {kw_clause}
                ORDER BY p.id DESC
                LIMIT %s
            """
            params_old = [section_name, sec_like] + kw_params + [limit]
            cur = conn.execute(sql_old, params_old)
            for row in cur.fetchall():
                d = dict(row)
                pid = int(d["product_id"])
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    out.append(d)
        finally:
            conn.close()
        cands = [keyword] + [p.strip() for p in parts if len(p.strip()) >= 2]
        for c in cands:
            if len(c) < 2:
                continue
            fr = filter_rows_keyword_in_product_name(out, c)
            if fr:
                return fr[:limit]
        return []

    def get_product_branches(self, product_id: int) -> List[Dict[str, Any]]:
        """
        get_product_branches(product_id)
        يرجع فرع المنتج (لأن المنتج مربوط بفرع واحد في schema الحالي).
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT b.id AS branch_id, b.city_name
                FROM products p
                JOIN branches b ON b.id = p.branch_id
                WHERE p.id = %s
                """,
                (product_id,),
            )
            row = cursor.fetchone()
            if not row:
                return []
            branch_id = int(row["branch_id"])
            loc = self.get_branch_location(branch_id) or {}
            return [
                {
                    "name": row["city_name"],
                    "location": loc.get("address") or "",
                    "map_link": loc.get("google_maps_url") or "",
                }
            ]
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════
    # لوحة تحكم الفرع: عرض/تعديل/حذف المنتجات
    # ═══════════════════════════════════════════════════════════════

    def list_products_for_branch(self, branch_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    p.id AS id,
                    p.product_name,
                    p.description,
                    p.price,
                    p.branch_id,
                    mc.name AS category_name,
                    sc.name AS section_name,
                    COALESCE(SUM(pv.quantity), 0) AS total_quantity,
                    COALESCE(MIN(pi.image_path), p.img1) AS image_path
                FROM products p
                LEFT JOIN sub_categories sc ON sc.id = p.sub_id
                LEFT JOIN main_categories mc ON mc.id = sc.main_id
                LEFT JOIN product_variants pv ON pv.product_id = p.id
                LEFT JOIN product_images pi ON pi.product_id = p.id AND pi.position = 1
                WHERE p.branch_id = %s
                GROUP BY
                    p.id,
                    p.product_name,
                    p.description,
                    p.price,
                    p.branch_id,
                    mc.name,
                    sc.name
                ORDER BY p.id DESC
                LIMIT %s
                """,
                (branch_id, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_product_detail(self, product_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT
                    p.*,
                    sc.name AS section_name,
                    mc.name AS category_name
                FROM products p
                LEFT JOIN sub_categories sc ON sc.id = p.sub_id
                LEFT JOIN main_categories mc ON mc.id = sc.main_id
                WHERE p.id = %s
                """,
                (product_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            product = dict(row)
            product["variants"] = self.get_product_variants(product_id)
            product["images"] = self.get_product_images(product_id)[:3]
            return product
        finally:
            conn.close()

    def delete_product_cascade(self, product_id: int) -> bool:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM product_variants WHERE product_id = %s", (product_id,))
            cursor.execute("DELETE FROM product_images WHERE product_id = %s", (product_id,))
            cursor.execute("DELETE FROM inventory WHERE product_id = %s", (product_id,))
            cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def update_product_basic(self, product_id: int, name: str, description: str, price: float) -> bool:
        name = (name or "").strip()
        if not name:
            return False
        try:
            price_val = float(price)
        except (TypeError, ValueError):
            price_val = 0.0
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE products
                SET product_name = %s, description = %s, price = %s
                WHERE id = %s
                """,
                (name, description or "", price_val, product_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def replace_product_variants(
        self,
        product_id: int,
        variants: List[Dict[str, Any]],
        product_price: Optional[float] = None,
    ) -> bool:
        """
        يستبدل متغيرات المنتج. السعر المخزّن في كل صف variant = سعر المنتج الأساسي (products.price).
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if product_price is None:
                cursor.execute("SELECT price FROM products WHERE id = %s", (product_id,))
                row = cursor.fetchone()
                try:
                    price_val = float(row["price"] if row else 0.0)
                except (TypeError, ValueError):
                    price_val = 0.0
            else:
                try:
                    price_val = float(product_price)
                except (TypeError, ValueError):
                    price_val = 0.0

            cleaned = _normalize_variant_rows(variants)

            cursor.execute("DELETE FROM product_variants WHERE product_id = %s", (product_id,))
            cursor.execute("DELETE FROM inventory WHERE product_id = %s", (product_id,))

            for v in cleaned:
                cursor.execute(
                    """
                    INSERT INTO product_variants (product_id, size, color, price, quantity)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (product_id, v["size"], v["color"], price_val, v["quantity"]),
                )
                cursor.execute(
                    """
                    INSERT INTO inventory (product_id, color, size, quantity)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (product_id, v["color"], v["size"], v["quantity"]),
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def replace_product_images(self, product_id: int, image_paths: List[str]) -> bool:
        image_paths = [p for p in (image_paths or []) if p][:3]
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM product_images WHERE product_id = %s", (product_id,))
            for idx, p in enumerate(image_paths, start=1):
                cursor.execute(
                    """
                    INSERT INTO product_images (product_id, image_path, position)
                    VALUES (%s, %s, %s)
                    """,
                    (product_id, p, idx),
                )
            # أيضاً نحافظ على الأعمدة القديمة img1..img3 لعرض سريع
            img1 = image_paths[0] if len(image_paths) >= 1 else None
            img2 = image_paths[1] if len(image_paths) >= 2 else None
            img3 = image_paths[2] if len(image_paths) >= 3 else None
            cursor.execute(
                "UPDATE products SET img1 = %s, img2 = %s, img3 = %s WHERE id = %s",
                (img1, img2, img3, product_id),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════
    # لوحة الإدارة: إدارة مستخدمي الفروع
    # ═══════════════════════════════════════════════════════════════

    def get_branch_products(self, branch_id):
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT p.*, s.name as sub_category_name 
                FROM products p
                LEFT JOIN sub_categories s ON p.sub_id = s.id
                WHERE p.branch_id = %s
                ORDER BY p.id
            """, (branch_id,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def add_full_product(self, branch_id, sub_id, name, desc, price, imgs, inventory_data):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO products (branch_id, sub_id, product_name, description, price, img1, img2, img3)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (branch_id, sub_id, name, desc, price, imgs[0], imgs[1], imgs[2]))
            product_id = cursor.lastrowid
            
            for item in inventory_data:
                cursor.execute("""
                    INSERT INTO inventory (product_id, color, size, quantity)
                    VALUES (%s, %s, %s, %s)
                """, (product_id, item['color'], item['size'], item['quantity']))
            
            conn.commit()
        except Exception:
            # rollback صريح لمنع InFailedSqlTransaction على PostgreSQL
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════
    # CRUD Schema الجديد: categories / sections / products / variants / images
    # ═══════════════════════════════════════════════════════════════

    def add_product(
        self,
        section_id: int,
        name: str,
        description: str,
        variants: List[Dict[str, Any]],
        image_paths: List[str],
        product_price_fallback: float = 0.0,
    ) -> Optional[int]:
        """
        إضافة Product + Variants + Images.

        - variants: [{size, color, quantity}, ...] — السعر من product_price_fallback فقط
        - image_paths: حتى 3 مسارات (ملفات) — position 1..3
        """
        name = (name or "").strip()
        description = description or ""
        if not name:
            return None
        image_paths = image_paths or []
        variants = variants or []

        # حد أقصى لصور المنتج (3)
        if len(image_paths) > 3:
            return None

        try:
            product_price = float(product_price_fallback)
        except (TypeError, ValueError):
            product_price = 0.0

        cleaned_variants = _normalize_variant_rows(variants)

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            branch_id = self._get_branch_id_for_section(section_id)
            if branch_id is None:
                return None

            cursor.execute(
                """
                INSERT INTO products (
                    branch_id, sub_id, section_id,
                    product_name, description, price,
                    img1, img2, img3
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    branch_id,
                    None,  # sub_id غير مستخدم في schema الجديد
                    section_id,
                    name,
                    description,
                    product_price,
                    image_paths[0] if len(image_paths) >= 1 else None,
                    image_paths[1] if len(image_paths) >= 2 else None,
                    image_paths[2] if len(image_paths) >= 3 else None,
                ),
            )
            product_id = cursor.lastrowid

            # حفظ صور product_images
            for idx, p in enumerate(image_paths[:3], start=1):
                cursor.execute(
                    """
                    INSERT INTO product_images (product_id, image_path, position)
                    VALUES (%s, %s, %s)
                    """,
                    (product_id, p, idx),
                )

            # حفظ variants
            for v in cleaned_variants:
                cursor.execute(
                    """
                    INSERT INTO product_variants (product_id, size, color, price, quantity)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (product_id, v["size"], v["color"], product_price, v["quantity"]),
                )

            conn.commit()
            return int(product_id)
        except Exception:
            conn.rollback()
            return None
        finally:
            conn.close()

    def delete_product(self, product_id: int) -> bool:
        """حذف Product + Variants + Images من schema الجديد."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM product_variants WHERE product_id = %s", (product_id,))
            cursor.execute("DELETE FROM product_images WHERE product_id = %s", (product_id,))
            cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
            conn.commit()
            return cursor.rowcount >= 0
        except Exception:
            # rollback صريح لمنع InFailedSqlTransaction على PostgreSQL
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()