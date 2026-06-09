# دليل ربط الأنظمة المحاسبية (ERP Integration Guide) - KognitixAI

تم تصميم النظام ليكون مرناً وقابلاً للربط مع أي نظام محاسبي خارجي من خلال "طبقة المحولات" (Adapters).

## 🏗️ هيكلية النظام
تعتمد التحليلات المالية على ملف `logic/finance_adapters.py` الذي يحتوي على المحولات المختلفة. كل محول يجب أن يرث من الفئة الأساسية `BaseFinanceAdapter`.

## ➕ إضافة نظام محاسبي جديد (مثال: يمن سوفت)

لإضافة نظام جديد، اتبع الخطوات التالية:

### 1. تعريف المحول في `logic/finance_adapters.py`
قم بإضافة فئة جديدة للمحول:

```python
class YemenSoftAdapter(BaseFinanceAdapter):
    def __init__(self, config):
        self.base_url = config.get('base_url')
        self.api_key = config.get('api_key')

    def fetch_dashboard_data(self, branch_id=None):
        # 1. قم بإرسال طلب HTTP لـ API يمن سوفت
        # 2. قم بتنسيق البيانات لتعود بنفس هيكل KognitixAI
        return {
            "today_sales": 1500.0,
            "transaction_count": 25,
            "mode": "YemenSoft"
        }
```

### 2. تسجيل المحول في المصنع (Factory)
في نفس الملف، أضف النظام الجديد لدالة `get_adapter`:

```python
def get_adapter(provider_name, config):
    providers = {
        'yemensoft': YemenSoftAdapter,
        'microsoft': MicrosoftDynamicsAdapter,
        # أضف هنا...
    }
```

## 📍 فلترة الفروع الحقيقية
تم تفعيل الفلترة الحقيقية من خلال معامل `branch_id` الذي يتم تمريره من الواجهة الأمامية إلى الـ Backend. 
*   **Backend:** دالة `fetch_financial_dashboard` تستقبل `branch_id` وتقوم بفلترة النتائج قبل إرسالها للواجهة.
*   **Frontend:** عند تغيير الفرع من القائمة المنسدلة، يتم إعادة تحميل الصفحة مع معامل `?branch_id=X`.

## 🔐 الأمان
يتم تخزين كافة مفاتيح الربط (API Keys) مشفرة داخل "الخزنة المالية" باستخدام تشفير AES-256-GCM لضمان أقصى درجات الأمان لبيانات العملاء المالية.
