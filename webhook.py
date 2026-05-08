# -*- coding: utf-8 -*-
"""
نقطة دخول قديمة — نفّذ التطبيق الكامل من app.py حتى يبقى /webhook متوفراً دائماً.

يُفضّل التشغيل عبر:
  python app.py
  أو: gunicorn app:app   (انظر Procfile)

تشغيل: python webhook.py  يعادل تشغيل app.py (نفس الكائن `app` ونفس المسارات).
"""
from app import app

if __name__ == "__main__":
    from config import FLASK_DEBUG, FLASK_HOST, FLASK_PORT

    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
