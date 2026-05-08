#!/usr/bin/env python3
"""
التحقق من سلامة ترحيل البيانات بين SQLite و PostgreSQL (قراءة فقط).

- يتصل بمسار SQLite من الإعدادات (DATABASE_PATH) وبـ DATABASE_URL لـ PostgreSQL.
- يقارن عدد الصفوف لكل جدول مشترك بين القاعدتين.
- يأخذ عيّنة عشوائية (5 صفوف) من branches و products و customers من SQLite
  ويقارن الصفوف ذات المفتاح الأساسي «id» في PostgreSQL.

لا يُعدّل أي بيانات. لا يستورد منطق التطبيق من logic/ (فقط config + مكتبات قياسية).

المتطلبات:
  - DATABASE_URL يشير إلى PostgreSQL (مثلاً قاعدة family).
  - ملف SQLite موجود في DATABASE_PATH (افتراضياً تحت data/).
  - إن لزم لتمرير تحميل config: SECRET_KEY, ADMIN_PASSWORD, FOUNDER_PASSWORD (يضبطها السكربت بقيم وهمية إن غابت).
"""

from __future__ import annotations

import decimal
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Set, Tuple


def _ensure_config_can_import() -> None:
    os.environ.setdefault(
        "SECRET_KEY",
        "verify-migration-script-do-not-use-in-production-0000000000",
    )
    os.environ.setdefault("ADMIN_PASSWORD", "verify-migration-only")
    os.environ.setdefault("FOUNDER_PASSWORD", "verify-migration-only")


def _sqlite_tables(conn: sqlite3.Connection) -> Set[str]:
    cur = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    return {str(r[0]) for r in cur.fetchall()}


def _postgres_tables(cur) -> Set[str]:
    cur.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
        """
    )
    rows = cur.fetchall()
    out: Set[str] = set()
    for r in rows:
        if isinstance(r, dict):
            out.add(str(r["tablename"]))
        else:
            out.add(str(r[0]))
    return out


def _count_sqlite(conn: sqlite3.Connection, table: str) -> int:
    cur = conn.execute(f'SELECT COUNT(*) AS c FROM "{table}"')
    return int(cur.fetchone()[0])


def _count_postgres(cur, table: str) -> int:
    from psycopg2 import sql  # type: ignore

    q = sql.SQL("SELECT COUNT(*) AS c FROM {}").format(sql.Identifier(table))
    cur.execute(q)
    row = cur.fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(row["c"])
    return int(row[0])


def _normalize_cell(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, memoryview):
        return bytes(v).hex()
    if isinstance(v, bytes):
        return v.hex()
    return v


def _row_to_comparable(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        d = row
    elif hasattr(row, "keys"):
        d = dict(row)
    else:
        d = {k: row[k] for k in row.keys()}  # type: ignore[attr-defined]
    return {k: _normalize_cell(d[k]) for k in sorted(d.keys())}


def _sqlite_random_ids(conn: sqlite3.Connection, table: str, pk: str, n: int) -> List[Any]:
    cur = conn.execute(
        f'SELECT "{pk}" AS pk FROM "{table}" ORDER BY RANDOM() LIMIT ?',
        (n,),
    )
    return [r[0] for r in cur.fetchall()]


def _sqlite_row_by_pk(conn: sqlite3.Connection, table: str, pk: str, pk_val: Any) -> Optional[Dict[str, Any]]:
    cur = conn.execute(
        f'SELECT * FROM "{table}" WHERE "{pk}" = ? LIMIT 1',
        (pk_val,),
    )
    r = cur.fetchone()
    if r is None:
        return None
    return dict(r)


def _pg_row_by_pk(cur, table: str, pk: str, pk_val: Any) -> Optional[Dict[str, Any]]:
    from psycopg2 import sql  # type: ignore

    q = sql.SQL("SELECT * FROM {} WHERE {} = %s LIMIT 1").format(
        sql.Identifier(table),
        sql.Identifier(pk),
    )
    cur.execute(q, (pk_val,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def _rows_equal(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[bool, List[str]]:
    diffs: List[str] = []
    keys = sorted(set(a.keys()) | set(b.keys()))
    for k in keys:
        va, vb = a.get(k), b.get(k)
        na, nb = _normalize_cell(va), _normalize_cell(vb)
        if na != nb:
            diffs.append(f"{k}: {na!r} != {nb!r}")
    return (len(diffs) == 0, diffs)


def main() -> int:
    _ensure_config_can_import()

    if not (os.getenv("DATABASE_URL") or "").strip():
        print(
            "خطأ: عيّن DATABASE_URL لاتصال PostgreSQL (مثال: postgresql://user:pass@host:5432/family).",
            file=sys.stderr,
        )
        return 2

    from config import DATABASE_PATH, DATABASE_URL

    if not os.path.isfile(DATABASE_PATH):
        print(f"خطأ: ملف SQLite غير موجود: {DATABASE_PATH}", file=sys.stderr)
        return 2

    try:
        import psycopg2  # type: ignore
        import psycopg2.extras  # type: ignore
    except ImportError:
        print("خطأ: ثبّت psycopg2-binary (pip install psycopg2-binary).", file=sys.stderr)
        return 2

    sqlite_conn = sqlite3.connect(DATABASE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(
        DATABASE_URL.strip(),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    pg_cur = pg_conn.cursor()

    try:
        t_sqlite = _sqlite_tables(sqlite_conn)
        t_pg = _postgres_tables(pg_cur)
        common = sorted(t_sqlite & t_pg)
        only_sqlite = sorted(t_sqlite - t_pg)
        only_pg = sorted(t_pg - t_sqlite)

        print("=== Row count comparison (SQLite vs PostgreSQL) ===\n")
        matched: List[str] = []
        mismatched: List[Tuple[str, int, int]] = []

        for t in common:
            try:
                c1 = _count_sqlite(sqlite_conn, t)
                c2 = _count_postgres(pg_cur, t)
            except Exception as exc:
                mismatched.append((t, -1, -1))
                print(f"  [ERROR] {t}: {exc}")
                continue
            if c1 == c2:
                matched.append(t)
                print(f"  [OK]    {t}: {c1}")
            else:
                mismatched.append((t, c1, c2))
                print(f"  [DIFF]  {t}: sqlite={c1}  postgres={c2}")

        if only_sqlite:
            print("\n--- Tables only in SQLite ---")
            for t in only_sqlite:
                try:
                    n = _count_sqlite(sqlite_conn, t)
                except Exception:
                    n = -1
                print(f"  {t} (rows ~ {n})")

        if only_pg:
            print("\n--- Tables only in PostgreSQL ---")
            for t in only_pg:
                try:
                    n = _count_postgres(pg_cur, t)
                except Exception:
                    n = -1
                print(f"  {t} (rows ~ {n})")

        print("\n=== Summary (row counts) ===")
        count_errors = [x for x in mismatched if x[1] == -1]
        count_diffs = [x for x in mismatched if x[1] != -1]
        print(f"  Matching tables:     {len(matched)}")
        print(f"  Count mismatch:      {len(count_diffs)}")
        print(f"  Count query errors:  {len(count_errors)}")

        # --- Sample check: 5 random rows per sensitive table, match by id ---
        sample_tables = ["branches", "products", "customers"]
        pk = "id"
        print("\n=== Sample check (5 random ids from SQLite → compare row in PostgreSQL) ===")

        for tbl in sample_tables:
            if tbl not in common:
                print(f"\n  [{tbl}] skipped (not in both databases)")
                continue
            try:
                ids = _sqlite_random_ids(sqlite_conn, tbl, pk, 5)
            except Exception as exc:
                print(f"\n  [{tbl}] ERROR sampling SQLite: {exc}")
                continue
            if not ids:
                print(f"\n  [{tbl}] empty in SQLite — no samples.")
                continue

            print(f"\n  [{tbl}] sampled ids: {ids}")
            for rid in ids:
                srow = _sqlite_row_by_pk(sqlite_conn, tbl, pk, rid)
                prow = _pg_row_by_pk(pg_cur, tbl, pk, rid)
                if srow is None:
                    print(f"    id={rid}: missing in SQLite (unexpected)")
                    continue
                if prow is None:
                    print(f"    id={rid}: MISSING in PostgreSQL")
                    continue
                ok, diffs = _rows_equal(
                    _row_to_comparable(srow), _row_to_comparable(prow)
                )
                if ok:
                    print(f"    id={rid}: MATCH")
                else:
                    print(f"    id={rid}: DATA_DIFF ({len(diffs)} field(s))")
                    for d in diffs[:12]:
                        print(f"      - {d}")
                    if len(diffs) > 12:
                        print(f"      ... and {len(diffs) - 12} more")

        print("\nDone (read-only).")
        return 0
    finally:
        sqlite_conn.close()
        pg_cur.close()
        pg_conn.close()


if __name__ == "__main__":
    sys.exit(main())
