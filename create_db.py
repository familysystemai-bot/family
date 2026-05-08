# -*- coding: utf-8 -*-
from app import app, db

with app.app_context():
    db.create_all()

print("تم إنشاء الجداول بنجاح!")
