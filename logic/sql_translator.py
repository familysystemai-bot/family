"""
مترجم SQL مركزي: SQLite ⇄ PostgreSQL
=====================================

الغرض من هذا الملف:
- توفير دالة موحّدة `translate_sql(sql, db_type)` تترجم استعلام SQL
  من اللهجة المستخدمة في الكود (SQLite-style بشكل عام) إلى اللهجة
  المستهدفة (postgres أو sqlite)، دون كسر السلوك الحالي.

المبدأ التصميمي:
- التحويلات هنا "محافظة" — لا نلمس استعلاماً نعرف أنه يعمل.
- نستخدم regex مع علامات حدود الكلمات (\\b) لتجنب التطابق العرضي.
- الكود الموجود في المشروع يكتب الاستعلامات بـ "?" placeholders و
  `datetime('now')` و `INSERT OR IGNORE`. كل هذه أنماط SQLite قياسية،
  ونحن نترجمها إلى نظائرها في PostgreSQL عند الحاجة.

ملاحظة هامة جداً:
- `?` placeholder: psycopg2 لا يفهمها — يجب التحويل إلى `%s`.
- `INSERT OR IGNORE INTO X (...) VALUES (...)`:
    SQLite: يتجاهل الصف عند تعارض UNIQUE.
    PostgreSQL: لا توجد صياغة مكافئة بدون معرفة العمود.
    الحل المعتمد هنا: تحويلها إلى `INSERT INTO X (...) VALUES (...) ON CONFLICT DO NOTHING`
    وهذا يعمل بشكل صحيح ما دام الجدول لديه قيد UNIQUE/PRIMARY KEY مناسب.
- `INSERT OR REPLACE`: نتركها كما هي إن لم نجدها (لا يستخدمها المشروع حالياً
  في الـ logic، لكن أضفنا التحويل احتياطاً).
- `datetime('now')` / `date('now')`: تتحول إلى CURRENT_TIMESTAMP / CURRENT_DATE.
- `strftime`: لا تترجم تلقائياً (نحتاج توقيع الدالة كاملاً) — نُسجّل تحذيراً
  ونترك الاستعلام كما هو حتى يصلحه المطور يدوياً.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def _replace_datetime_now_day_offset(m: re.Match[str]) -> str:
    n = int(m.group("off"))
    if n == 0:
        return "CURRENT_TIMESTAMP"
    if n > 0:
        return f"CURRENT_TIMESTAMP + INTERVAL '{n} day'"
    return f"CURRENT_TIMESTAMP - INTERVAL '{abs(n)} day'"

# ──────────────────────────────────────────────────────────────────────
# تعابير منتظمة (precompiled لأداء أفضل)
# ──────────────────────────────────────────────────────────────────────

# datetime('now') → CURRENT_TIMESTAMP
_RE_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)

# datetime('now', 'localtime') → CURRENT_TIMESTAMP
_RE_DATETIME_NOW_LOCALTIME = re.compile(
    r"datetime\(\s*'now'\s*,\s*'localtime'\s*\)",
    re.IGNORECASE,
)

# datetime('now', '-1 day') / '+2 days' → CURRENT_TIMESTAMP ± INTERVAL
_RE_DATETIME_NOW_DAY_OFFSET = re.compile(
    r"datetime\(\s*'now'\s*,\s*'(?P<off>-?\d+)\s+day[s]?'\s*\)",
    re.IGNORECASE,
)

# datetime(column_name) → column_name::timestamp (أعمدة محفوظة كنص ISO)
_RE_DATETIME_ONE_COL = re.compile(
    r"\bdatetime\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)",
    re.IGNORECASE,
)

# date('now') → CURRENT_DATE
_RE_DATE_NOW = re.compile(r"date\(\s*'now'\s*\)", re.IGNORECASE)

# INSERT OR IGNORE INTO ... → INSERT INTO ... ON CONFLICT DO NOTHING
# يلتقط: INSERT OR IGNORE INTO <table> (cols) VALUES (...)
# ويحوّل: INSERT INTO <table> (cols) VALUES (...) ON CONFLICT DO NOTHING
_RE_INSERT_OR_IGNORE = re.compile(
    r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
    re.IGNORECASE,
)

# INSERT OR REPLACE INTO ...
# لا توجد ترجمة عامة دقيقة. نُسجّل تحذيراً ونتركها (لا تظهر في المشروع حالياً).
_RE_INSERT_OR_REPLACE = re.compile(
    r"\bINSERT\s+OR\s+REPLACE\s+INTO\b",
    re.IGNORECASE,
)

# strftime: نُحذّر فقط — لا توجد ترجمة سطر واحد آمنة.
_RE_STRFTIME = re.compile(r"\bstrftime\s*\(", re.IGNORECASE)

# AUTOINCREMENT داخل INTEGER PRIMARY KEY → SERIAL PRIMARY KEY
_RE_INTEGER_PK_AUTOINC = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
    re.IGNORECASE,
)


def _convert_qmark_placeholders(sql: str) -> str:
    """
    تحويل placeholders من '?' إلى '%s'.

    نُجري الاستبدال خارج النصوص الحرفية (single-quoted strings) فقط
    لتجنّب لمس حرف '?' داخل قيمة LIKE مثلاً. لكن في هذا المشروع لا
    يوجد '?' داخل نصوص — ومع ذلك نحتاط.
    """
    if "?" not in sql:
        return sql

    out = []
    i = 0
    n = len(sql)
    in_squote = False  # داخل '...'
    in_dquote = False  # داخل "..."
    while i < n:
        ch = sql[i]
        # معالجة تخطّي الحرف الهارب (نادر في SQL لكن ممكن: '' داخل النص)
        if ch == "'" and not in_dquote:
            # SQL يستخدم '' للتعبير عن apostrophe — نُبقيها كنص مرتبط
            in_squote = not in_squote
            out.append(ch)
        elif ch == '"' and not in_squote:
            in_dquote = not in_dquote
            out.append(ch)
        elif ch == "?" and not in_squote and not in_dquote:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ──────────────────────────────────────────────────────────────────────
# الدالة الرئيسية
# ──────────────────────────────────────────────────────────────────────

def translate_sql(sql: Optional[str], db_type: str) -> str:
    """
    يترجم استعلام SQL إلى اللهجة المطلوبة.

    Args:
        sql: نص الاستعلام (يمكن أن يكون None — يُعاد كنص فارغ).
        db_type: 'postgres' أو 'sqlite'.

    Returns:
        نص الاستعلام بعد الترجمة. لا يُرفع أي استثناء؛ التحويلات آمنة.
    """
    if not sql:
        return ""

    s = sql

    # ── الترجمة لـ PostgreSQL ──────────────────────────────
    if db_type == "postgres":
        # 1) تحويل placeholders ? → %s (الأهم — psycopg2 لا يقبل ?)
        s = _convert_qmark_placeholders(s)

        # 2) datetime('now', 'localtime') و datetime('now', '-N day') قبل datetime('now')
        s = _RE_DATETIME_NOW_LOCALTIME.sub("CURRENT_TIMESTAMP", s)
        s = _RE_DATETIME_NOW_DAY_OFFSET.sub(_replace_datetime_now_day_offset, s)

        # 3) datetime('now') → CURRENT_TIMESTAMP
        s = _RE_DATETIME_NOW.sub("CURRENT_TIMESTAMP", s)

        # 4) datetime(عمود) → عمود::timestamp (SQLite datetime(text) — غير موجود في PostgreSQL)
        s = _RE_DATETIME_ONE_COL.sub(r"(\1::timestamp)", s)

        # 5) date('now') → CURRENT_DATE
        s = _RE_DATE_NOW.sub("CURRENT_DATE", s)

        # 6) INSERT OR IGNORE INTO → INSERT INTO ... ON CONFLICT DO NOTHING
        if _RE_INSERT_OR_IGNORE.search(s):
            s = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", s)
            # نضيف ON CONFLICT DO NOTHING في نهاية الجملة (قبل ; إن وُجدت).
            # نتجاهل الإضافة لو الاستعلام أصلاً يحتوي على ON CONFLICT (احتراز).
            if "ON CONFLICT" not in s.upper():
                stripped = s.rstrip()
                if stripped.endswith(";"):
                    s = stripped[:-1].rstrip() + " ON CONFLICT DO NOTHING;"
                else:
                    s = stripped + " ON CONFLICT DO NOTHING"

        # 7) INSERT OR REPLACE: تحذير فقط — لا توجد ترجمة عامة آمنة.
        if _RE_INSERT_OR_REPLACE.search(s):
            logger.warning(
                "translate_sql: 'INSERT OR REPLACE' لا يمكن ترجمته تلقائياً. "
                "استبدله يدوياً بـ INSERT ... ON CONFLICT (col) DO UPDATE."
            )

        # 8) strftime: تحذير فقط (لا يستخدمها المشروع في logic حالياً).
        if _RE_STRFTIME.search(s):
            logger.debug(
                "translate_sql: 'strftime' لا تُترجم تلقائياً في هذا المشروع."
            )

        return s

    # ── الترجمة لـ SQLite ──────────────────────────────────
    # %s → ? (الكود قد يكتب %s صراحةً في بعض الأماكن للـ PostgreSQL،
    # لكن قد نشغّله محلياً على SQLite عبر nfs db_adapter — نقلب الاتجاه).
    if db_type == "sqlite":
        return s.replace("%s", "?")

    # نوع غير مدعوم — نُرجع النص كما هو (لا نكسر شيئاً)
    return s


def translate_ddl(ddl: Optional[str], db_type: str) -> str:
    """
    يترجم نصوص DDL (CREATE TABLE / CREATE INDEX / ALTER TABLE).

    التركيز على نوع المفاتيح الأساسية:
        SERIAL PRIMARY KEY (المعتمد في الكود) → INTEGER PRIMARY KEY AUTOINCREMENT لـ SQLite.
        INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY لـ PostgreSQL.

    وكذلك يترجم datetime('now') داخل DEFAULT.
    """
    if not ddl:
        return ""

    s = ddl

    if db_type == "sqlite":
        # SERIAL PRIMARY KEY → INTEGER PRIMARY KEY AUTOINCREMENT
        s = re.sub(
            r"\bSERIAL\s+PRIMARY\s+KEY\b",
            "INTEGER PRIMARY KEY AUTOINCREMENT",
            s,
            flags=re.IGNORECASE,
        )
        return s

    if db_type == "postgres":
        # INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
        s = _RE_INTEGER_PK_AUTOINC.sub("SERIAL PRIMARY KEY", s)
        # datetime('now') → CURRENT_TIMESTAMP داخل DEFAULT
        s = _RE_DATETIME_NOW.sub("CURRENT_TIMESTAMP", s)
        return s

    return s


# ──────────────────────────────────────────────────────────────────────
# واجهات قصيرة للاستخدام الشائع (back-compat مع _pg_adapt_sql القديم)
# ──────────────────────────────────────────────────────────────────────

def to_postgres(sql: str) -> str:
    """اختصار: ترجمة لـ PostgreSQL."""
    return translate_sql(sql, "postgres")


def to_sqlite(sql: str) -> str:
    """اختصار: ترجمة لـ SQLite."""
    return translate_sql(sql, "sqlite")