# -*- coding: utf-8 -*-
"""
integrations — مركز التكاملات الخارجية لمجمع العائلة.

الفئات الأربع:
    1. shipping  — شركات الشحن (SMSA, Aramex, DHL, Naqel...)
    2. payment   — بوابات الدفع (Moyasar, Tap, HyperPay, PayTabs)
    3. invoicing — نظام الفواتير (PDF + Email + Zoho/QuickBooks اختياري)
    4. storage   — تخزين الصور (Cloudinary, ImageKit, S3, R2)
                   موجود في logic/cloud_storage.py

كل تكامل:
    - يقرأ مفاتيحه من system_settings (يصبح Secrets Vault في الدفعة 5)
    - يدعم test_connection() للتحقق من الإعدادات
    - يدعم list_supported_providers() لعرضها في لوحة المؤسس
"""