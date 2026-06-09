# خطة هندسة البرمجيات والتصميم التقني للوحة التحكم الموحدة لوسائل التواصل الاجتماعي

## 1. المقدمة

تهدف هذه الوثيقة إلى تحديد الخطة التقنية لتطوير لوحة تحكم موحدة لوسائل التواصل الاجتماعي ضمن مشروع "مجمع العائلة" الحالي. ستركز اللوحة على إدارة الرسائل، نشر المنشورات، وإدارة الحملات الإعلانية عبر منصات متعددة، بدءًا من Instagram و Facebook، بالإضافة إلى التكامل الحالي مع WhatsApp.

## 2. الأهداف التقنية

*   توسيع البنية التحتية الحالية لـ Flask لدعم تكاملات جديدة لوسائل التواصل الاجتماعي.
*   توفير واجهة موحدة لإدارة الرسائل الواردة والصادرة من منصات مختلفة.
*   تمكين نشر المحتوى (نصوص، صور) على منصات متعددة بضغطة زر واحدة.
*   دمج إمكانيات إدارة الحملات الإعلانية (خاصة Meta Ads) وعرض تقاريرها.
*   الاستفادة من نظام `system_settings` الحالي لتخزين وإدارة مفاتيح الـ API بأمان.
*   الحفاظ على بنية المشروع الحالية (Flask, Jinja2, SQLite/PostgreSQL) وتوسيعها بشكل نظيف.

## 3. المكونات المعمارية الجديدة

### 3.1. Blueprint جديد: `social_dashboard`

تم إنشاء Blueprint جديد باسم `social_dashboard` ليكون نقطة الدخول الرئيسية للوحة التحكم الموحدة. سيحتوي هذا الـ Blueprint على المسارات (routes) التالية:

*   `/founder/social/dashboard`: الصفحة الرئيسية للوحة التحكم، تعرض إحصائيات عامة وحالة الربط مع المنصات.
*   `/founder/social/publisher`: واجهة لإنشاء ونشر المحتوى على منصات متعددة.
*   `/founder/social/inbox`: صندوق رسائل موحد لعرض وإدارة المحادثات من مختلف المنصات.
*   `/founder/social/ads`: واجهة لعرض وإدارة الحملات الإعلانية (سيتم تطويرها لاحقًا).

### 3.2. طبقة الخدمات (Service Layer)

سيتم إنشاء ملفات خدمات جديدة ضمن `logic/social/` للتعامل مع المنطق الخاص بكل منصة على حدة، مثل `instagram_service.py` و `facebook_service.py`. هذه الخدمات ستكون مسؤولة عن:

*   التفاعل مع الـ APIs الخاصة بكل منصة (مثل Meta Graph API).
*   معالجة البيانات قبل إرسالها أو بعد استقبالها.
*   إدارة الـ Access Tokens والتأكد من صلاحيتها.

### 3.3. طبقة التكاملات (Integrations Layer)

سيتم توسيع `app_integrations.py` و `logic/integrations/base.py` لإضافة إعدادات مفاتيح الـ API الخاصة بـ Instagram و Facebook. سيتم تخزين هذه المفاتيح في جدول `system_settings` بنفس الطريقة المستخدمة حاليًا لـ WhatsApp و OpenAI.

## 4. تصميم التكامل مع المنصات

### 4.1. Meta Graph API (لـ Facebook و Instagram)

تعتبر Meta Graph API هي الواجهة الأساسية للتفاعل مع Facebook و Instagram Business Accounts. سيتطلب التكامل الخطوات التالية:

*   **مصادقة المستخدم (OAuth 2.0)**: يجب على المؤسس ربط حسابه التجاري على Facebook/Instagram. سيتم توجيه المستخدم إلى Meta لتفويض التطبيق، ثم يتم استلام `Access Token` طويل الأجل (Long-Lived Access Token) وتخزينه بأمان في `system_settings`.
*   **إدارة الصفحات والحسابات**: بعد المصادقة، سيتم جلب قائمة الصفحات (Facebook Pages) وحسابات Instagram Business المرتبطة بحساب المستخدم، للسماح للمؤسس باختيار الصفحات/الحسابات التي يرغب في إدارتها عبر اللوحة.
*   **Webhooks**: لإدارة الرسائل الواردة (Instagram DMs, Facebook Messenger)، يجب إعداد Webhooks في تطبيق Meta Developer الخاص بالمشروع. سيتم توجيه هذه الـ Webhooks إلى مسار مخصص في `social_dashboard` Blueprint لمعالجة الرسائل في الوقت الفعلي.

#### 4.1.1. نشر المحتوى (Publishing)

*   **Instagram**: استخدام `Graph API` لنشر الصور ومقاطع الفيديو والـ Reels. يتطلب ذلك `Access Token` و `Instagram Business Account ID`.
*   **Facebook**: استخدام `Graph API` لنشر المنشورات (نصوص، صور، فيديوهات) على الصفحات المرتبطة.

#### 4.1.2. الرسائل الموحدة (Unified Inbox)

*   **Instagram DMs**: جلب الرسائل الواردة والصادرة عبر `Graph API` (أو Webhooks) وعرضها في واجهة `inbox`.
*   **Facebook Messenger**: جلب الرسائل الواردة والصادرة عبر `Graph API` (أو Webhooks) وعرضها في واجهة `inbox`.
*   **WhatsApp**: سيتم دمج صندوق رسائل WhatsApp الحالي (`wa_inbox`) ضمن الواجهة الموحدة الجديدة.

#### 4.1.3. الحملات الإعلانية (Ads Management)

*   **Meta Ads API**: سيتم استخدام `Meta Ads API` لجلب بيانات الحملات الإعلانية (الميزانية، الإنفاق، الوصول، التفاعل، التكلفة لكل نتيجة) وعرضها في لوحة التحكم. سيتم الاستفادة من مهارة `meta-ads-analyzer` المتاحة لدي لتحليل هذه البيانات وتقديم رؤى.

## 5. هيكل قاعدة البيانات (Database Schema)

قد نحتاج إلى جداول جديدة أو تحديث جداول موجودة لتخزين بيانات خاصة بالمنصات:

*   **`social_accounts`**: لتخزين معلومات الحسابات المرتبطة (Instagram Business ID, Facebook Page ID, Access Tokens).
    *   `id` (PRIMARY KEY)
    *   `platform` (TEXT: 'instagram', 'facebook')
    *   `platform_id` (TEXT: Instagram Business Account ID / Facebook Page ID)
    *   `access_token` (TEXT: مشفر)
    *   `expires_at` (TEXT)
    *   `owner_id` (INTEGER: Foreign Key to founder/admin user)
    *   `is_active` (INTEGER)
*   **`social_posts`**: لتخزين المنشورات المجدولة والمنشورة.
    *   `id` (PRIMARY KEY)
    *   `content` (TEXT)
    *   `image_url` (TEXT)
    *   `platforms` (TEXT: JSON array of platforms to publish on)
    *   `scheduled_at` (TEXT)
    *   `published_at` (TEXT)
    *   `status` (TEXT: 'draft', 'scheduled', 'published', 'failed')
    *   `created_by` (TEXT)
*   **`social_messages`**: لتخزين الرسائل من مختلف المنصات (يمكن دمجها مع جدول `messages` الحالي أو إنشاء جدول جديد).
    *   `id` (PRIMARY KEY)
    *   `platform` (TEXT: 'whatsapp', 'instagram', 'facebook')
    *   `conversation_id` (TEXT: معرّف المحادثة على المنصة)
    *   `sender_id` (TEXT: معرّف المرسل على المنصة)
    *   `sender_name` (TEXT)
    *   `message_body` (TEXT)
    *   `direction` (TEXT: 'inbound', 'outbound')
    *   `timestamp` (TEXT)
    *   `branch_id` (INTEGER: Foreign Key)

## 6. واجهة المستخدم (UI/UX)

*   **التصميم**: سيتم الالتزام بتصميم `fd-card` و `fd-page-head` الحالي لضمان التناسق البصري.
*   **المعاينة**: سيتم توفير معاينة حية للمنشورات قبل النشر على مختلف المنصات.
*   **الجدولة**: سيتم دمج وظيفة الجدولة للمنشورات باستخدام محرك الجدولة الحالي أو تطوير محرك جديد مخصص.

## 7. الأمان

*   **تشفير الـ Access Tokens**: سيتم تشفير جميع الـ Access Tokens الحساسة قبل تخزينها في قاعدة البيانات.
*   **التحقق من Webhooks**: سيتم التحقق من توقيع الـ Webhooks الواردة من Meta لضمان صحتها وأمانها.
*   **الصلاحيات**: سيتم تطبيق نظام الصلاحيات الحالي (Founder Only) على جميع صفحات لوحة التحكم الموحدة.

## 8. الخطوات التالية

1.  تحديث `logic/database.py` لإضافة الجداول الجديدة (`social_accounts`, `social_posts`, `social_messages`).
2.  توسيع `app_integrations.py` لإضافة إعدادات Instagram و Facebook API.
3.  تطوير `logic/social/instagram_service.py` و `logic/social/facebook_service.py`.
4.  تنفيذ منطق النشر في `social_dashboard_routes.py`.
5.  تنفيذ منطق صندوق الرسائل الموحد في `social_dashboard_routes.py`.

**المؤلف:** Manus AI
