#!/usr/bin/env python3
"""
ترحيل بيانات: SQLite (قراءة فقط) → PostgreSQL (INSERT مُهيأ).

- المصدر: `DATABASE_PATH` (SQLite) — اتصال **read-only** (URI `mode=ro`؛ لا تعديل ولا حذف).
- الهدف: `DATABASE_URL` (PostgreSQL).
- الترتيب: من `PRAGMA foreign_key_list` (الجداول المُشار إليها قبل العائِد من FK).
- **قاعدة `family` (بلا تعديل الـ schema):** أعمدة INSERT من `information_schema`. لجدول
  `branches` إن وُجد `tenant_id` (NOT NULL) تُرسل دائماً القيمة الثابتة
  `BRANCHES_TENANT_ID_VALUE` (افتراضياً 1).
- أعمدة تُزال عالمياً من INSERT مذكورة في `FAMILY_EXCLUDED_FROM_INSERT` (ما عدا ما يُعالج أعلاه).
- من SQLite: `SELECT *` ثم إدراج **فقط** الأعمدة المقبولة أعلاه، مع `SQLITE_TO_PG_RENAME`
  و`FAMILY_AUTO_SQLITE_TO_PG` عند اختلاف اسم العمود (مثل `username` → `name`).
- **قيم NOT NULL:** يُستند إلى `information_schema` (`is_nullable`، `column_default`، `is_identity`):
  `NULL` من السورس يُستعاض عنه (منطق/نص/رقم/وغيرها)، ويُسقَط من الإدراج عند DEFAULT/IDENTITY.
- **لا يُنفّذ تلقائياً** — نفّذ: `python migrate_data.py` بعد توفر **schema** على PostgreSQL
  وجداول الهدف **فارغة** (أو `--truncate-unsafe` بعد فهم المخاطر).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, DefaultDict, Dict, FrozenSet, List, Optional, Set, Tuple

os.environ.setdefault(
    "SECRET_KEY",
    "migrate-ephemeral-00000000000000000000-not-for-production",
)
os.environ.setdefault("ADMIN_PASSWORD", "migrate-ephemeral")
os.environ.setdefault("FOUNDER_PASSWORD", "migrate-ephemeral")
os.environ.setdefault("DB_TYPE", "postgres")

logger = logging.getLogger("migrate_data")

# يدوي اختياري: { "جدول": { "عمود_في_sqlite": "عمود_فعلي_في_pg" } } — يتقدّم على التعيين التلقائي
SQLITE_TO_PG_RENAME: Dict[str, Dict[str, str]] = {}

# مشروع family: عند اختلاف تسمية الأعمدة، اختر أول عمود موجود فعلياً في PostgreSQL
FAMILY_AUTO_SQLITE_TO_PG: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "branches": {
        "username": (
            "username",
            "name",
            "branch_name",
            "login",
            "branch_login",
        ),
    },
}

# أعمدة تُتجاهل عالمياً (لا مُناسب لـ family عادةً). `tenant_id` مُتعامل معه لجدول
# branches بشكل خاص: يُضاف بقيمة ثابتة، لا تُسقط من INSERT.
FAMILY_EXCLUDED_FROM_INSERT: FrozenSet[str] = frozenset(
    {
        "tenancy_id",
        "org_id",
        "organization_id",
        "workspace_id",
        "account_tenant_id",
    }
)

# لجدول `branches` فقط: قيمة إلزامية لـ `tenant_id` (إن وُجد العمود في PostgreSQL)
BRANCHES_TENANT_ID_VALUE: int = 1


def _id_ok(name: str) -> str:
    s = str(name)
    if not s or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s):
        raise ValueError("معرف غير آمن: %r" % (name,))
    return s


def _sqlite_user_tables(c: sqlite3.Cursor) -> List[str]:
    c.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    return [str(r[0]) for r in c.fetchall()]


def _fk_parents(
    c: sqlite3.Cursor, table: str, all_tables: Set[str]
) -> Set[str]:
    t = _id_ok(table)
    c.execute("PRAGMA foreign_key_list('{}')".format(t))
    out: Set[str] = set()
    for r in c.fetchall():
        p = str(r[2])
        if p in all_tables and p != table:
            out.add(p)
    return out


def _topo_order(
    all_tables: Set[str], parent_map: Dict[str, Set[str]]
) -> List[str]:
    unmet: Dict[str, Set[str]] = {
        t: (parent_map.get(t) or set()) & all_tables for t in all_tables
    }
    children: DefaultDict[str, List[str]] = defaultdict(list)
    for t, ps in unmet.items():
        for p in ps:
            children[p].append(t)
    q: deque = deque([t for t in all_tables if not unmet[t]])
    out: List[str] = []
    done: Set[str] = set()
    while q:
        t = str(q.popleft())
        if t in done:
            continue
        out.append(t)
        done.add(t)
        for c in children.get(t, []):
            unmet[c].discard(t)
            if (not unmet[c]) and (c not in done):
                q.append(c)
    if len(out) < len(all_tables):
        rem = [x for x in all_tables if x not in done]
        raise RuntimeError("تعذر ترتيب الجداول: %r" % (rem,))
    return out


def _sqlite_table_cols_and_pk(
    c: sqlite3.Cursor, table: str
) -> Tuple[List[str], Optional[str], bool]:
    t = _id_ok(table)
    c.execute("PRAGMA table_info('{}')".format(t))
    cols: List[str] = []
    pk: Optional[str] = None
    pkt = ""
    for r in c.fetchall():
        cn, ct = str(r[1]), str(r[2] or "")
        is_pk = int(r[5] or 0) == 1
        if is_pk and pk is None:
            pk, pkt = cn, ct.lower()
        cols.append(cn)
    int_pk = (pk is not None) and (pkt in ("integer", "int", "int64", ""))
    return cols, pk, int_pk


def _pg_table_names(p_c: Any, schema: str) -> Set[str]:
    s = _id_ok(schema)
    p_c.execute(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = %s",
        (s,),
    )
    return {str(r[0]) for r in p_c.fetchall()}


def _pg_insertable_columns(
    p_c: Any, table: str, schema: str
) -> List[str]:
    """أعمدة تُقبل في INSERT: ترتيب `ordinal_position` (كما في PostgreSQL)."""
    t, s = _id_ok(table), _id_ok(schema)
    for q in (
        (
            "SELECT c.column_name FROM information_schema.columns c "
            "WHERE c.table_schema = %s AND c.table_name = %s "
            "AND (c.is_generated IS NULL OR c.is_generated <> 'ALWAYS') "
            "ORDER BY c.ordinal_position"
        ),
        (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position"
        ),
    ):
        try:
            p_c.execute(q, (s, t))
            return [str(r[0]) for r in p_c.fetchall()]
        except Exception:
            continue
    return []


def _pg_columns_for_family(
    p_c: Any, table: str, schema: str
) -> List[str]:
    """
    أعمدة INSERT من information_schema، ناقص FAMILY_EXCLUDED_FROM_INSERT
    (لا يتضمّن tenant_id: يُتعامَل معه في branches عبر قيمة ثابتة).
    """
    tq = _id_ok(table)
    raw = _pg_insertable_columns(p_c, tq, schema)
    if not raw:
        return []
    ok: List[str] = []
    skipped: List[str] = []
    for c in raw:
        if c in FAMILY_EXCLUDED_FROM_INSERT:
            skipped.append(c)
        else:
            ok.append(c)
    if skipped:
        logger.info(
            "جدول %s: أعمدة مُتجاهَة للإدراج (SaaS/tenant — غير مطلوبة لـ family): %s",
            tq,
            ", ".join(skipped),
        )
    return ok


def _pg_single_primary_key(
    p_c: Any, table: str, schema: str
) -> Optional[str]:
    t, s = _id_ok(table), _id_ok(schema)
    p_c.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = %s
          AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """,
        (s, t),
    )
    rows = p_c.fetchall()
    if len(rows) == 1:
        return str(rows[0][0])
    return None


def _pg_column_is_integerish(
    p_c: Any, table: str, column: str, schema: str
) -> bool:
    t, c0, s = _id_ok(table), _id_ok(column), _id_ok(schema)
    p_c.execute(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s AND column_name = %s
        """,
        (s, t, c0),
    )
    w = p_c.fetchone()
    if not w:
        return False
    dt = (w[0] or "").lower()
    return "int" in dt


@dataclass(frozen=True)
class PgColMeta:
    is_nullable: bool
    data_type: str
    udt_name: str
    column_default: Optional[str]
    is_identity: str


def _pg_table_column_metadata(
    p_c: Any, table: str, schema: str
) -> Dict[str, PgColMeta]:
    t, s = _id_ok(table), _id_ok(schema)
    sql = """
        SELECT column_name, is_nullable, data_type, udt_name, column_default, is_identity
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """
    try:
        p_c.execute(sql, (s, t))
    except Exception:
        p_c.execute(
            """
            SELECT column_name, is_nullable, data_type, udt_name, column_default, 'NO' AS is_identity
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (s, t),
        )
    out: Dict[str, PgColMeta] = {}
    for r in p_c.fetchall():
        cn = str(r[0])
        ide = str((r[5] if len(r) > 5 and r[5] is not None else "NO")).upper()
        out[cn] = PgColMeta(
            is_nullable=(str(r[1]).upper() == "YES"),
            data_type=(r[2] or "").lower(),
            udt_name=(r[3] or "").lower(),
            column_default=None if r[4] is None else str(r[4]),
            is_identity=ide,
        )
    return out


def _pg_has_implicit_or_explicit_default(m: PgColMeta) -> bool:
    if m.is_identity in ("YES", "ALWAYS",):
        return True
    d = m.column_default
    if d and str(d).strip():
        return True
    return False


def _is_missing_for_not_null(v: Any) -> bool:
    return v is None


def _is_numericish_column(m: PgColMeta) -> bool:
    u, dt = m.udt_name, m.data_type
    if u in (
        "int2", "int4", "int8", "float4", "float8", "numeric", "oid", "money",
        "serial2", "serial4", "serial8",
    ):
        return True
    if (m.column_default or "") and "nextval" in (m.column_default or "").lower():
        return True
    if dt in (
        "double precision", "real", "smallint", "integer", "bigint", "numeric",
    ):
        return True
    return False


def _synthetic_value_for_not_null(
    col_name: str, m: PgColMeta, tq: str, log_once: Set[Tuple[str, str]]
) -> Any:
    c = str(col_name)
    dt, u = m.data_type, m.udt_name
    r: Any
    if u == "bool" or dt == "boolean":
        r = True
    elif _is_numericish_column(m):
        r = 1 if (c.lower().startswith("is_") or c.lower() in (
            "is_active", "active", "declined_marketing_prompt", "prefers_marketing",
        )) else 0
    elif u in ("json", "jsonb") or (dt in ("json", "jsonb") or (u and "json" in u and u in ("json", "jsonb"))):
        r = {}
    elif u in ("text", "varchar", "bpchar", "name",) or u == "citext" or (
        "char" in (dt, u) or "text" in (dt, u) or dt in ("text", "character varying", "character")
    ) or (dt in ("user-defined",) and u and u not in ("json", "jsonb", "bool", "uuid",)):
        r = ""
    elif u == "bytea" or dt == "bytea":
        r = b""
    elif u == "timestamptz" or dt == "timestamp with time zone":
        r = datetime.now(timezone.utc)
    elif u == "timestamp" or dt in ("timestamp without time zone",) or (
        isinstance(dt, str) and dt.startswith("timestamp") and "without" in dt
    ):
        r = datetime.now()
    elif u == "date" or dt == "date":
        r = date.today()
    elif u == "time" or dt == "time without time zone":
        r = time(0, 0, 0)
    elif u == "timetz" or dt == "time with time zone":
        r = time(0, 0, 0, tzinfo=timezone.utc)
    elif u == "uuid" or dt == "uuid":
        r = uuid.uuid4()
    elif u == "interval" or dt == "interval":
        r = timedelta(0)
    elif u and u.endswith("[]"):
        r: Any = []  # type: ignore[no-redef]
    else:
        logger.warning(
            "NOT NULL: %s — نوع %s (udt=%s) — نصّ فارغ", tq, dt, u
        )
        r = ""
    key = (tq, c)
    if key not in log_once:
        log_once.add(key)
        logger.info(
            "تعبئة افتراضية NOT NULL: %s.%s → %r (نوع %s / udt %s)", tq, c, r, dt, u
        )
    return r


def _apply_not_null_fills(
    tq: str,
    pg_order: List[str],
    val_by_pg: Dict[str, Any],
    meta: Dict[str, PgColMeta],
    log_once: Set[Tuple[str, str]],
) -> None:
    """يُدير أعمدة NOT NULL: إسقاط NULL عند DEFAULT/IDENTITY، وإلّا قيم اصطناعية آمنة."""
    for c in pg_order:
        if c not in meta:
            continue
        m = meta[c]
        if m.is_nullable:
            continue
        v = val_by_pg.get(c)
        if m.is_identity in ("YES", "ALWAYS",):
            if c in val_by_pg and _is_missing_for_not_null(v):
                del val_by_pg[c]
            continue
        if c in val_by_pg and _is_missing_for_not_null(v) and _pg_has_implicit_or_explicit_default(
            m
        ):
            del val_by_pg[c]
            continue
        if c not in val_by_pg and _pg_has_implicit_or_explicit_default(m):
            continue
        if c in val_by_pg and not _is_missing_for_not_null(v):
            continue
        val_by_pg[c] = _synthetic_value_for_not_null(c, m, tq, log_once)


def _coerce(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, memoryview):
        return v.tobytes()
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return v


def _build_insert(t: str, ncols: int) -> str:
    t = _id_ok(t)
    vph = ", ".join(["%s"] * ncols)
    return f'INSERT INTO "public"."{t}" ({{cols}}) VALUES ({vph})'


def _tr_pg(c, table: str) -> None:
    t = _id_ok(table)
    c.execute(f'TRUNCATE TABLE "public"."{t}" RESTART IDENTITY CASCADE')


def _setval_pg(c, table: str, pk: str) -> None:
    """تعيين تسلسل serial/identity بعد الإدراج (مُتجاهل إن لم يكن لعمود تسلسل)."""
    tt, p = _id_ok(table), _id_ok(pk)
    c.execute(
        "SELECT setval( pg_get_serial_sequence(%s, %s), "
        f"(SELECT GREATEST( COALESCE( MAX( \"{p}\" )::bigint, 1), 1 )::bigint "
        f'FROM "public"."{tt}"), true )',
        (f"public.{tt}", p),
    )


def _safe_remap_for_table(
    tq: str, t_raw: str
) -> Dict[str, str]:
    m = (SQLITE_TO_PG_RENAME.get(t_raw) or SQLITE_TO_PG_RENAME.get(tq) or {}) or {}
    out: Dict[str, str] = {}
    for a, b in m.items():
        try:
            a_ok, b_ok = _id_ok(str(a)), _id_ok(str(b))
        except ValueError:
            logger.warning("تجاهل إدخال في SQLITE_TO_PG_RENAME: %r -> %r", a, b)
            continue
        if b_ok in FAMILY_EXCLUDED_FROM_INSERT:
            logger.warning(
                "تجاهل خريطة نحو عمود محظور (SaaS/tenant): %s: %s -> %s",
                tq, a_ok, b_ok,
            )
            continue
        out[a_ok] = b_ok
    return out


def _merge_family_remap(
    tq: str,
    t_raw: str,
    sqlite_cols: List[str],
    pg_set: Set[str],
) -> Dict[str, str]:
    """دمج خريطة المستخدم مع قواعد family: أعمدة مُتاحة فقط (حسب pg_set)."""
    m = _safe_remap_for_table(tq, t_raw)
    auto = (FAMILY_AUTO_SQLITE_TO_PG.get(t_raw) or FAMILY_AUTO_SQLITE_TO_PG.get(tq) or {}) or {}
    for s_col, candidates in auto.items():
        if s_col not in sqlite_cols:
            continue
        if s_col in m:
            continue
        if s_col in pg_set:
            continue
        for c in candidates:
            c = str(c)
            if c in FAMILY_EXCLUDED_FROM_INSERT:
                continue
            if c in pg_set:
                try:
                    c_ok = _id_ok(c)
                except ValueError:
                    continue
                m[s_col] = c_ok
                logger.info(
                    "تعيين تلقائي (family) %s: عمود SQLite %s → %s",
                    tq, s_col, c_ok,
                )
                break
    return m


def _row_to_pg_values(
    d: Dict[str, Any], pg_order: List[str], remap: Dict[str, str]
) -> Dict[str, Any]:
    """دمج بيانات صف SQLite مع أعمدة الهدف في PostgreSQL (بعد إعادة التسمية)."""
    pg_set = set(pg_order)
    out: Dict[str, Any] = {}
    for k, v in d.items():
        sk = str(k)
        pgc = remap.get(sk, sk)
        if pgc in pg_set:
            out[pgc] = v
    return out


def _migrate_one_table(
    s_c: sqlite3.Cursor,
    p_c: Any,
    t: str,
    truncate: bool,
    pg_tables: Set[str],
    schema: str,
) -> Tuple[int, int]:
    tq = _id_ok(t)
    if tq not in pg_tables:
        s_c.execute('SELECT COUNT(*) AS c FROM "{}"'.format(tq))
        n_miss = int(s_c.fetchone()["c"])
        if n_miss:
            logger.warning(
                "تخطٍ: الجدول %s غير ضمن public في PostgreSQL (%d صف في SQLite) — يُتجاهَل، يُواصل "
                "السكربت",
                tq, n_miss,
            )
        else:
            logger.info("تخطٍ: جدول %s غير في PostgreSQL (0 صف في السورس) — مُتجاهَل", tq)
        return 0, 0

    pg_order = _pg_columns_for_family(p_c, tq, schema)
    if not pg_order:
        logger.error("لا أعمدة مُدخَلة لجدول %s (بعد فلترة family)", tq)
        return 0, 1
    if tq == "branches":
        logger.info(
            "قاعدة family — أعمدة branches المستخدمة في INSERT: %s",
            ", ".join(pg_order),
        )
        if "tenant_id" in set(pg_order):
            logger.info(
                "branches: إدراج tenant_id = %s لكل الصفوف (مطلوب NOT NULL)",
                BRANCHES_TENANT_ID_VALUE,
            )

    sqlite_cols, _sqlite_pk, _sqlite_int = _sqlite_table_cols_and_pk(s_c, t)
    if not sqlite_cols:
        return 0, 0
    pg_set = set(pg_order)
    remap = _merge_family_remap(tq, t, sqlite_cols, pg_set)
    if remap:
        logger.info("خريطة أعمدة (SQLite→PostgreSQL) لـ %s: %s", tq, remap)
    dropped = [sc for sc in sqlite_cols if (remap.get(sc, sc) not in pg_set)]
    if dropped:
        logger.warning(
            "أعمدة في SQLite بلا مُطابِق باسم في PostgreSQL لـ %s (لن تُنقل): %s",
            tq,
            ", ".join(dropped),
        )
    fillable_pg: Set[str] = set()
    for sc in sqlite_cols:
        tcol = remap.get(sc, sc)
        if tcol in pg_set:
            fillable_pg.add(tcol)
    only_in_pg = [c for c in pg_order if c not in fillable_pg]
    if tq == "branches" and "tenant_id" in pg_set:
        only_in_pg = [c for c in only_in_pg if c != "tenant_id"]
    if only_in_pg:
        logger.info(
            "أعمدة فقط في PostgreSQL لـ %s (يُرجّح DEFAULT أو NULL): %s",
            tq,
            ", ".join(only_in_pg),
        )

    s_c.execute('SELECT * FROM "{}"'.format(tq))
    rows = s_c.fetchall()
    n_rows = len(rows)
    if truncate:
        try:
            logger.warning("TRUNCATE (غير آمن) public.%s", tq)
            _tr_pg(p_c, tq)
        except Exception as e:
            logger.critical("فشل TRUNCATE %s: %s", tq, e, exc_info=True)
            raise
    if n_rows == 0:
        return 0, 0

    pk_pg = _pg_single_primary_key(p_c, tq, schema)
    int_pk = bool(
        pk_pg
        and _pg_column_is_integerish(p_c, tq, pk_pg, schema)
    )

    pg_col_meta = _pg_table_column_metadata(p_c, tq, schema)
    not_null_log: Set[Tuple[str, str]] = set()

    n_ok, n_err = 0, 0
    for r in rows:
        d = {str(k): _coerce(r[k]) for k in r.keys()}  # type: ignore[union-attr]
        val_by_pg = _row_to_pg_values(d, pg_order, remap)
        if tq == "branches" and "tenant_id" in pg_set:
            val_by_pg["tenant_id"] = BRANCHES_TENANT_ID_VALUE
        _apply_not_null_fills(tq, pg_order, val_by_pg, pg_col_meta, not_null_log)
        insert_cols = [c for c in pg_order if c in val_by_pg]
        if not insert_cols:
            n_err += 1
            logger.error(
                "لا أعمدة تُنقل لصف في %s; مفاتيح السورس: %r",
                tq,
                list(d.keys())[:12],
            )
            continue
        ncols = len(insert_cols)
        ins = _build_insert(tq, ncols).format(
            cols=", ".join('"' + _id_ok(x) + '"' for x in insert_cols)
        )
        vals = tuple(val_by_pg[c] for c in insert_cols)
        try:
            p_c.execute(ins, vals)
            n_ok += 1
        except Exception as e:
            n_err += 1
            logger.error("صف في %s فشل: %s (القيم مقطوعة: %r)", tq, e, vals[:3])
    if int_pk and pk_pg and n_ok:
        try:
            _setval_pg(p_c, tq, pk_pg)
        except Exception as e:
            logger.warning("تعيين التسلسل لـ %s فشل (غير مزعج إن لم يكن serial): %s", tq, e)
    return n_ok, n_err


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="ترحيل SQLite → PostgreSQL")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="عرض الترتيب وعدد الصفوف دون الاتصال بـ PG للكتابة",
    )
    p.add_argument(
        "--truncate-unsafe",
        action="store_true",
        help="(PostgreSQL) TRUNCATE CASCADE لكل جدول بترتيب مُنقل قبل الإدراج",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(asctime)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    from config import DATABASE_PATH, DATABASE_URL

    dsn = (DATABASE_URL or "").strip()
    if not dsn:
        logger.error("عيّن متغير البيئة DATABASE_URL (PostgreSQL).")
        return 1
    if not os.path.isfile(DATABASE_PATH):
        logger.error("ملف SQLite غير موجود: %s", DATABASE_PATH)
        return 1

    u = "file:" + os.path.abspath(DATABASE_PATH).replace("\\", "/") + "?mode=ro&cache=shared"
    try:
        sl = sqlite3.connect(u, uri=True, check_same_thread=False)
    except sqlite3.Error as e:
        logger.critical("فشل فتح SQLite للقراءة فقط: %s", e, exc_info=True)
        return 1

    sl.row_factory = sqlite3.Row
    s_c = sl.cursor()
    try:
        tset = set(_sqlite_user_tables(s_c))
        if not tset:
            logger.error("لا جداول بيانات في السورس.")
            return 1
        pmap: Dict[str, Set[str]] = {t: _fk_parents(s_c, t, tset) for t in tset}
        try:
            order = _topo_order(tset, pmap)
        except RuntimeError as e:
            logger.critical("%s", e, exc_info=True)
            return 1

        counts: Dict[str, int] = {}
        for t in tset:
            tq = _id_ok(t)
            s_c.execute('SELECT COUNT(*) AS c FROM "{}"'.format(tq))
            c = int(s_c.fetchone()["c"])
            counts[t] = c
        if args.dry_run:
            logger.info("--- dry-run: الترتيب = %s", " → ".join(order))
            for t in order:
                logger.info("  %s: %d صف", t, counts.get(t, 0))
            return 0

        try:
            import psycopg2  # type: ignore
        except ImportError:
            logger.error("تثبيت مطلوب: pip install psycopg2-binary")
            return 1
        try:
            pg = psycopg2.connect(dsn)
        except Exception as e:
            logger.critical("تعذر الاتصال بـ PostgreSQL: %s", e, exc_info=True)
            return 1
        p_c = None
        try:
            try:
                pg.autocommit = False
            except Exception:
                pass
            p_c = pg.cursor()
            pg_tables = _pg_table_names(p_c, "public")
            if args.truncate_unsafe:
                for tb in reversed(order):
                    tid = _id_ok(tb)
                    if tid not in pg_tables:
                        logger.warning("تخطٍ TRUNCATE: جدول %s غير موجود في PostgreSQL", tid)
                        continue
                    _tr_pg(p_c, tid)
            for tb in order:
                logger.info("--- بدء ترحيل جدول: %s", tb)
                try:
                    n_ok, n_err = _migrate_one_table(
                        s_c, p_c, tb, False, pg_tables, "public"
                    )
                    if n_err:
                        raise RuntimeError(
                            "توقف: أخطاء إدراج في %s (نجح: %d، فشل: %d)" % (tb, n_ok, n_err)
                        )
                    logger.info("إنهاء %s: صف منقول %d", tb, n_ok)
                except Exception as e:
                    logger.critical("توقف لجدول %s: %s", tb, e, exc_info=True)
                    pg.rollback()
                    return 1
            pg.commit()
            logger.info("اكتمل الترحيل بنجاح (تأكد يدوياً من البيانات والفهارس/التسلسلات).")
            return 0
        finally:
            if p_c is not None:
                try:
                    p_c.close()
                except Exception:
                    pass
            try:
                pg.close()
            except Exception:
                pass
    finally:
        try:
            sl.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
