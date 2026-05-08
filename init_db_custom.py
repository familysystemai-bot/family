#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تهيئة قاعدة البيانات عبر DatabaseManager باتصال PostgreSQL (من DATABASE_URL).

- يجب أن يُعيَّن `DATABASE_URL` في البيئة (أو في `.env` لأن `config` يحمّله عند الاستيراد).
- يُجبر السكربت `DB_TYPE=postgres` — لا يستخدم مسار SQLite.

ملاحظة: `DatabaseManager._init_db` مكتوب أصلاً لنحو SQL قريب من SQLite (مثل AUTOINCREMENT).
لإنتاج PostgreSQL يلزم عادة سكربت DDL مخصص؛ هذا الملف يربط `DATABASE_URL` ويستدعي `init_db()`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# فرض ‎Postgres قبل أي ‎`import` يقرأ ‎`config` (لأن ‎`logic.database` تستورده).
os.environ["DB_TYPE"] = "postgres"

# تحميل ‎.env إن وُجد (نفس سلوك ‎`config` محلياً) حتى تُتاح ‎`DATABASE_URL`
if os.getenv("RENDER") is None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    except Exception:
        pass

if not (os.environ.get("DATABASE_URL") or "").strip():
    print(
        "تعيين DATABASE_URL (اتصال PostgreSQL) مطلوب. مثال: postgresql://user:pass@host:5432/db",
        file=sys.stderr,
    )
    sys.exit(1)

# بعد ضبط البيئة: استيراد ‎`DatabaseManager` يربط ‎`DATABASE_URL` مثل التطبيق
from logic.database import DatabaseManager  # noqa: E402


def main() -> int:
    db = DatabaseManager()
    if db.db_type != "postgres":
        print("توقع DB_TYPE=postgres ؛ الإعداد الحالي: %r" % (db.db_type,), file=sys.stderr)
        return 1
    dsn = (getattr(db, "_postgres_dsn", None) or os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL فارغ بعد التهيئة.", file=sys.stderr)
        return 1
    print("الربط: PostgreSQL عبر DATABASE_URL (ليس مسار SQLite).")
    db.init_db()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
