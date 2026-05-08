#!/usr/bin/env python3
"""
اختبار اتصال PostgreSQL عبر DBAdapter (قراءة فقط، بدون CREATE/INSERT/UPDATE/DELETE).

المتطلبات:
  - تثبيت الحزمة: psycopg2-binary (موجودة في requirements.txt).
  - تعيين متغيرات البيئة قبل التشغيل (أو عبر ملف .env في جذر المشروع):
      DB_TYPE=postgres
      DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/family
    حيث اسم قاعدة البيانات المستهدفة: family

  - ملف config.py يفرض أيضاً وجود SECRET_KEY و ADMIN_PASSWORD و FOUNDER_PASSWORD
    عند استيراد الإعدادات؛ إذا لم تكن معرّفة، يضبط السكربت قيماً وهمية *مؤقتة*
    فقط لتمرير التحميل (لا تستخدمها في إنتاج).

الاستخدام:
  set DB_TYPE=postgres
  set DATABASE_URL=postgresql://...
  python test_db_connection.py
"""

from __future__ import annotations

import os
import sys


def _ensure_config_can_import() -> None:
    os.environ.setdefault(
        "SECRET_KEY",
        "test-db-connection-script-do-not-use-in-production-32chars",
    )
    os.environ.setdefault("ADMIN_PASSWORD", "test-db-connection-only")
    os.environ.setdefault("FOUNDER_PASSWORD", "test-db-connection-only")
    os.environ["DB_TYPE"] = "postgres"
    if not (os.getenv("DATABASE_URL") or "").strip():
        print(
            "خطأ: عيّن DATABASE_URL للاتصال بقاعدة family، مثال:\n"
            "  postgresql://USER:PASSWORD@localhost:5432/family",
            file=sys.stderr,
        )
        sys.exit(2)


def main() -> int:
    _ensure_config_can_import()

    from logic.db_adapter import DBAdapter

    dsn = (os.getenv("DATABASE_URL") or "").strip()
    adapter = DBAdapter(db_type="postgres", postgres_dsn=dsn)

    row = adapter.fetch_one(
        "SELECT current_database() AS db, current_user AS role, version() AS version"
    )
    if not row:
        print("فشل: لم تُرجع الاستعلام أي صف.", file=sys.stderr)
        return 1

    db_name = (row.get("db") or row.get("current_database") or "").strip()
    print("الاتصال ناجح (قراءة فقط).")
    print(f"  current_database: {db_name}")
    print(f"  current_user:     {row.get('role')}")
    ver = (row.get("version") or "")[:120]
    print(f"  server version:   {ver}...")

    if db_name.lower() != "family":
        print(
            f"\nتحذير: اسم قاعدة البيانات المتصل بها هو «{db_name}» وليس «family».\n"
            "  تأكد من أن DATABASE_URL يشير إلى /family في نهاية المسار.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
