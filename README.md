<div align="center">

# 🏬 مجمع العائلة — نظام خدمة العملاء الذكي

**منصة متكاملة لإدارة خدمة العملاء، الفروع، المنتجات، والشكاوى مع دعم الذكاء الاصطناعي وتكامل واتساب**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0+-000000?logo=flask&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791?logo=postgresql&logoColor=white)
![WhatsApp](https://img.shields.io/badge/WhatsApp-Cloud_API-25D366?logo=whatsapp&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-412991?logo=openai&logoColor=white)

</div>

---

## 📋 جدول المحتويات

- [نظرة عامة](#-نظرة-عامة)
- [الميزات](#-الميزات)
- [هيكل المشروع](#-هيكل-المشروع)
- [المتطلبات](#-المتطلبات)
- [التثبيت والتشغيل](#-التثبيت-والتشغيل)
- [متغيرات البيئة](#-متغيرات-البيئة)
- [الأدوار والصلاحيات](#-الأدوار-والصلاحيات)
- [واجهات النظام](#-واجهات-النظام)
- [تكامل واتساب](#-تكامل-واتساب)
- [النشر على Render](#-النشر-على-render)
- [الأمان](#-الأمان)
- [المساهمة](#-المساهمة)

---

## 🌟 نظرة عامة

نظام خدمة عملاء ذكي مبني بـ Flask يخدم **مجمع العائلة** التجاري. يوفر شات بوت ذكي يعمل على الموقع وعبر واتساب، مع لوحات تحكم متعددة المستويات لإدارة الفروع والمنتجات والشكاوى والحملات التسويقية.

---

## ✨ الميزات

### 🤖 شات بوت ذكي
- محادثة طبيعية بالعربية (سعودي / فصحى)
- تصنيف تلقائي لنوايا العميل (استفسار، شكوى، بحث عن منتج)
- دعم المرفقات: صور (Gemini / OpenAI Vision) وصوت (Whisper)
- اكتشاف تلقائي للهجات العربية

### 📱 تكامل واتساب
- استقبال وإرسال الرسائل عبر WhatsApp Cloud API
- تحليل الصور والرسائل الصوتية الواردة
- صندوق رسائل متكامل لكل فرع
- إدارة حالة المحادثات (إيقاف AI / حظر)

### 🏢 إدارة الفروع
- لوحة تحكم مستقلة لكل فرع
- إدارة المنتجات (فئات ← أقسام ← منتجات + ألوان/مقاسات)
- أوقات الدوام (فترة واحدة أو فترتين)
- مواقع الفروع (Google Maps + GPS)

### 📊 لوحة تحكم الإدارة
- إحصائيات شاملة (شكاوى، منتجات، فروع)
- إدارة المستخدمين وصلاحياتهم
- معلومات الشركة المركزية للشات

### 📢 الحملات التسويقية
- إنشاء وجدولة حملات بريدية
- إرسال عبر واتساب
- استهداف العملاء حسب الفرع

### 📝 نظام الشكاوى
- تصنيف ذكي للشكاوى (AI)
- تذاكر فريدة لكل شكوى
- تتبع الحالة (مفتوحة ← محلولة)
- إشعار العميل بالرد (بريد + واتساب)

### 💰 التقارير المالية
- خزنة مالية مشفّرة (AES-256-GCM)
- ربط مع أمازون (اختياري)
- تقارير الإيرادات والمصروفات

### 🔒 الأمان
- حماية CSRF عبر Flask-WTF
- تحقق توقيع Meta (HMAC-SHA256) للـ webhook
- أمان رفع الملفات (Magic Bytes + حد الحجم)
- تشفير كلمات المرور (Werkzeug scrypt/pbkdf2)
- رؤوس أمان المتصفح (CSP, HSTS, X-Frame-Options)

---

## 📂 هيكل المشروع

```
family-system-main/
├── app.py                    # نقطة الدخول الرئيسية (Flask app + routes)
├── config.py                 # إعدادات التطبيق المركزية
├── webhook.py                # نقطة دخول بديلة
├── requirements.txt          # التبعيات
├── Procfile                  # إعدادات Gunicorn (Render)
├── runtime.txt               # إصدار Python
│
├── logic/                    # طبقة المنطق والخدمات
│   ├── database.py           # DatabaseManager (SQLite + PostgreSQL)
│   ├── security.py           # CSRF, HMAC, File Validation
│   ├── chat_router.py        # توجيه المحادثات الذكية
│   ├── chat_service.py       # خدمة الشات الموحّدة
│   ├── ai_fallback.py        # ردود AI الاحتياطية
│   ├── ai_router.py          # توجيه مزودي AI
│   ├── llm_provider.py       # تكامل OpenAI / Gemini
│   ├── complaint_service.py  # معالجة الشكاوى
│   ├── product_service.py    # خدمة المنتجات والبحث
│   ├── campaign_service.py   # إدارة الحملات
│   ├── finance_routes.py     # المسارات المالية
│   ├── finance_crypto.py     # تشفير AES-256-GCM
│   ├── wa_inbox_routes.py    # مسارات صندوق الواتساب
│   ├── cloud_storage.py      # التخزين السحابي
│   ├── mail_service.py       # إرسال البريد (SMTP)
│   ├── otp_service.py        # رموز التحقق (معطّل حالياً)
│   ├── *_repository.py       # طبقة الوصول لقاعدة البيانات
│   └── integrations/         # تكاملات خارجية
│
├── templates/                # قوالب Jinja2
│   ├── index.html            # الصفحة الرئيسية + شات العملاء
│   ├── login.html            # تسجيل الدخول
│   ├── dashboard.html        # لوحة الفرع
│   ├── admin_dashboard.html  # لوحة الإدارة
│   ├── founder/              # قوالب لوحة المؤسس
│   ├── campaigns/            # قوالب الحملات
│   ├── wa_inbox/             # قوالب صندوق الواتساب
│   ├── macros/               # ماكروز مشتركة
│   └── partials/             # أجزاء مشتركة
│
├── static/                   # ملفات ثابتة
│   ├── css/                  # أنماط CSS
│   ├── js/                   # سكربتات JavaScript
│   ├── sw.js                 # Service Worker (PWA)
│   ├── manifest.json         # PWA Manifest
│   └── uploads/              # ملفات مرفوعة
│
├── site_config/              # إعدادات الموقع (فروع، سياسات)
│   ├── branches.py
│   ├── company_policies.py
│   └── founder_attribution.py
│
├── data/                     # قاعدة بيانات SQLite (تطوير فقط)
│   └── family_system.db
│
└── app_integrations.py       # Blueprint التكاملات الخارجية
```

---

## 📦 المتطلبات

- **Python** 3.11 أو أحدث
- **PostgreSQL** 15+ (للإنتاج) أو SQLite (للتطوير)
- حساب **OpenAI** (اختياري — للشات الذكي)
- حساب **Google Gemini** (اختياري — لتحليل الصور)
- حساب **WhatsApp Business** على Meta (اختياري — لتكامل واتساب)

---

## 🚀 التثبيت والتشغيل

### 1. استنساخ المشروع

```bash
git clone https://github.com/your-org/family-system.git
cd family-system
```

### 2. إنشاء بيئة افتراضية

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. تثبيت التبعيات

```bash
pip install -r requirements.txt
```

### 4. إعداد متغيرات البيئة

```bash
cp .env.example .env
# عدّل .env بالقيم الحقيقية (انظر قسم متغيرات البيئة)
```

### 5. تشغيل التطبيق

```bash
# وضع التطوير
python app.py

# أو عبر Gunicorn (إنتاج)
gunicorn app:app --bind 0.0.0.0:5000
```

التطبيق سيعمل على: `http://localhost:5000`

---

## 🔐 متغيرات البيئة

انسخ `.env.example` إلى `.env` وعدّل القيم:

| المتغير | الوصف | مطلوب |
|---------|-------|-------|
| `SECRET_KEY` | مفتاح Flask لتوقيع الجلسات (عشوائي طويل) | ✅ |
| `ADMIN_USERNAME` | اسم مستخدم المدير العام | ✅ |
| `ADMIN_PASSWORD` | كلمة مرور المدير (يُفضل hash) | ✅ |
| `FOUNDER_USERNAME` | اسم مستخدم المؤسس | ✅ |
| `FOUNDER_PASSWORD` | كلمة مرور المؤسس (يُفضل hash) | ✅ |
| `DATABASE_URL` | رابط PostgreSQL | ✅ (إنتاج) |
| `DB_TYPE` | نوع القاعدة: `sqlite` أو `postgres` | ❌ |
| `SENDER_EMAIL` | بريد SMTP المُرسل | ❌ |
| `SENDER_PASSWORD` | كلمة مرور SMTP | ❌ |
| `OPENAI_API_KEY` | مفتاح OpenAI | ❌ |
| `GEMINI_API_KEY` | مفتاح Google Gemini | ❌ |
| `WA_ACCESS_TOKEN` | توكن واتساب Cloud API | ❌ |
| `WA_PHONE_NUMBER_ID` | معرّف رقم واتساب | ❌ |
| `WA_VERIFY_TOKEN` | توكن تحقق Webhook | ❌ |
| `META_APP_SECRET` | سر تطبيق Meta (HMAC) | ❌ |

> ⚠️ **تنبيه أمني:** لا ترفع ملف `.env` للمستودع أبداً. استخدم متغيرات البيئة في منصة النشر.

---

## 👥 الأدوار والصلاحيات

| الدور | الوصف | الصلاحيات |
|-------|-------|-----------|
| **المؤسس** (Founder) | مالك النظام | كل شيء: فروع، منتجات، شكاوى، حسابات، إعدادات، تقارير مالية |
| **المدير العام** (Admin) | مدير الإدارة | إدارة الفروع والمنتجات والشكاوى ومعلومات الشركة |
| **الفرع** (Branch) | موظف الفرع | إدارة منتجات فرعه، الرد على الشكاوى والاستفسارات |
| **العميل** (Customer) | زائر الشات | محادثة الشات الذكي، تقديم شكاوى، البحث عن منتجات |

---

## 🖥️ واجهات النظام

| المسار | الوصف |
|--------|-------|
| `/` | الصفحة الرئيسية + شات العملاء |
| `/login` | تسجيل الدخول (مؤسس / إدارة / فرع) |
| `/dashboard` | لوحة تحكم الفرع |
| `/admin/dashboard` | لوحة المدير العام |
| `/founder/dashboard` | لوحة تحكم المؤسس |
| `/admin/complaints` | إدارة الشكاوى |
| `/admin/users` | إدارة مستخدمي الفروع |
| `/admin/settings` | إعدادات الحسابات |
| `/products` | منتجات الفرع |
| `/webhook` | WhatsApp Webhook |

---

## 📱 تكامل واتساب

### إعداد Webhook

1. أنشئ تطبيقاً في [Meta for Developers](https://developers.facebook.com/)
2. فعّل WhatsApp Business API
3. اضبط Webhook URL: `https://your-domain.com/webhook`
4. عيّن المتغيرات:
   - `WA_ACCESS_TOKEN` — توكن الوصول
   - `WA_PHONE_NUMBER_ID` — معرّف رقم الهاتف
   - `WA_VERIFY_TOKEN` — توكن التحقق
   - `META_APP_SECRET` — سر التطبيق (للتحقق من HMAC)

### الرسائل المدعومة
- ✅ نصوص
- ✅ صور (مع تحليل AI)
- ✅ رسائل صوتية (Whisper)
- ✅ أزرار تفاعلية
- ✅ قوائم تفاعلية

---

## ☁️ النشر على Render

1. أنشئ **Web Service** جديداً على [Render](https://render.com)
2. اربطه بمستودع Git
3. الإعدادات:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app` (موجود في `Procfile`)
4. أضف **PostgreSQL** database من Render
5. عيّن متغيرات البيئة في لوحة Render (لا تستخدم `.env`)
6. تأكد من ضبط:
   - `RENDER=true`
   - `DATABASE_URL` (يُعيَّن تلقائياً من Render PostgreSQL)
   - `SECRET_KEY` (مفتاح عشوائي طويل)

---

## 🔒 الأمان

- **CSRF:** حماية عبر Flask-WTF (معفاة للـ webhook و AJAX endpoints)
- **كلمات المرور:** مُشفّرة بـ Werkzeug (scrypt / pbkdf2)
- **رفع الملفات:** تحقق Magic Bytes + حد حجم 10MB للصور
- **Webhook:** تحقق HMAC-SHA256 عبر `META_APP_SECRET`
- **رؤوس الأمان:** CSP, X-Frame-Options, HSTS, Referrer-Policy
- **الجلسات:** HttpOnly + SameSite + Secure (إنتاج) + انتهاء بعد 8 ساعات خمول

---

## 🛠️ التطوير

### قاعدة البيانات

```bash
# SQLite (تطوير محلي — افتراضي)
DB_TYPE=sqlite

# PostgreSQL (إنتاج)
DB_TYPE=postgres
DATABASE_URL=postgresql://user:pass@host:5432/family
```

المخطط يُنشأ تلقائياً عند بدء التطبيق (`DatabaseManager._init_db`).

### تشخيص النظام

| المسار | الوصف |
|--------|-------|
| `/admin/diagnostics/email` | تشخيص إعدادات البريد |
| `/admin/diagnostics/full` | تشخيص شامل للمشروع |

---

## 📄 الترخيص

جميع الحقوق محفوظة © مجمع العائلة.

---

<div align="center">

**صُنع بـ ❤️ لمجمع العائلة**

</div>
