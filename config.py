"""
إعدادات التطبيق المركزية (مسارات، مفاتيح، قيم افتراضية).

- الإنتاج (مثل Render): المتغيرات من لوحة Environment فقط — لا يُفترض وجود ملف .env.
- محلياً: اختياريًا تحميل `.env` من جذر المشروع عند عدم تعيين RENDER.

ملاحظة: حزمة بيانات العلامة والفروع موجودة في المجلد site_config/ لتفادي التعارض مع اسم هذا الملف.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

# جذر المشروع (حيث يقع config.py)
BASE_DIR = Path(__file__).resolve().parent
_ENV_PATH = BASE_DIR / ".env"

if os.getenv("RENDER") is None:
    from dotenv import load_dotenv

    load_dotenv(_ENV_PATH)

# ——— قاعدة البيانات ———
DATA_DIR = BASE_DIR / "data"
DATABASE_FILENAME = os.getenv("DATABASE_FILENAME", "family_system.db")
DATABASE_PATH = str(DATA_DIR / DATABASE_FILENAME)

# ——— Flask ———
SECRET_KEY = os.getenv("SECRET_KEY", "default_secret_key_123_change_me")
STATIC_FOLDER = "static"
UPLOAD_FOLDER_NAME = "uploads"
UPLOAD_FOLDER = str(BASE_DIR / STATIC_FOLDER / UPLOAD_FOLDER_NAME)

ALLOWED_EXTENSIONS = frozenset(
    {"png", "jpg", "jpeg", "gif", "webp", "webm", "wav", "mp3", "ogg", "m4a"}
)

# ——— تسجيل الدخول (لوحة الإدارة) ———
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "kazm")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "2255")

# ——— حساب المؤسس (لا يُخزَّن في قاعدة البيانات) ———
FOUNDER_USERNAME = os.getenv("FOUNDER_USERNAME", "kazm")
FOUNDER_PASSWORD = os.getenv("FOUNDER_PASSWORD", "2255")

# ——— معلومات المؤسس (تُعرض في المحادثة فقط عند السؤال الصريح عنها) ———
FOUNDER_PUBLIC_FULL_NAME = os.getenv("FOUNDER_PUBLIC_FULL_NAME", "كاظم نجيب المطحني")
FOUNDER_PUBLIC_PHONE = os.getenv("FOUNDER_PUBLIC_PHONE", "0538344673")

# ——— البريد ———
# المرسل الرسمي لـ SMTP (المستلمون: MAIN_RECEIVER_EMAIL / الفروع / الإدارة — لا تُستخدم كمرسل)
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_OFFICE_EMAIL = os.getenv("ADMIN_OFFICE_EMAIL", "management@family-mall.com")
# صندوق استقبال الشكاوى الرئيسي (يُرسل إليه كل بلاغ). يدعم الاسم الجديد أو القديم في .env
MAIN_RECEIVER_EMAIL = (
    os.getenv("MAIN_RECEIVER_EMAIL")
    or os.getenv("COMPLAINT_INBOX_EMAIL")
    or "almthnyalkazm@gmail.com"
)
DEFAULT_COMPLAINT_INBOX = MAIN_RECEIVER_EMAIL

# ——— تنبيهات تشخيص النظام (مسار /admin/diagnostics/full) ———
SYSTEM_ALERTS_EMAIL = (
    os.getenv("SYSTEM_ALERTS_EMAIL") or "theking73995@gmail.com"
).strip()
# تجنّب إعادة إرسال نفس التنبيهات خلال عدد الثواني التالية
DIAGNOSTICS_ALERT_COOLDOWN_SECONDS = int(
    os.getenv("DIAGNOSTICS_ALERT_COOLDOWN_SECONDS", "3600")
)

# ——— نماذج ذكاء اصطناعي (اختياري، يُقرأ من البيئة) ———
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY is not None:
    OPENAI_API_KEY = OPENAI_API_KEY.strip() or None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# طبقة LLM كمحلل فقط (بدون ردود للمستخدم) داخل /chat_query
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() in ("1", "true", "yes")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
LLM_MODEL = os.getenv("LLM_MODEL", OLLAMA_MODEL)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "90"))

# ——— تشغيل السيرفر ———
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("PORT", os.getenv("FLASK_PORT", "5000")))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() in ("1", "true", "yes")

# ——— الحملات المجدولة (روابط الصور في البريد + خيط المجدول) ———
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
CAMPAIGN_SCHEDULER_ENABLED = os.getenv("CAMPAIGN_SCHEDULER_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
CAMPAIGN_SCHEDULER_INTERVAL_SEC = int(os.getenv("CAMPAIGN_SCHEDULER_INTERVAL_SEC", "60"))

# ——— جلسة Flask (لوحة التحكم) ———
PERMANENT_SESSION_LIFETIME = timedelta(days=30)


def ensure_upload_dir() -> None:
    """إنشاء مجلد الرفع إن لم يكن موجوداً."""
    Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)


def ensure_data_dir() -> None:
    """إنشاء مجلد البيانات إن لم يكن موجوداً."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def update_env_file(key: str, value: str) -> None:
    """
    يحدّث أو يضيف سطر KEY=value في ملف .env بجذر المشروع.
    يُستخدم لتحديث كلمة مرور المؤسس دون لمس قاعدة البيانات.
    """
    key = (key or "").strip()
    if not key or "=" in key or "\n" in key or "\r" in key:
        raise ValueError("مفتاح .env غير صالح")
    raw = str(value).replace("\n", "").replace("\r", "")
    if any(c in raw for c in ' #"\'\\'):
        escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
        new_line = f'{key}="{escaped}"'
    else:
        new_line = f"{key}={raw}"

    path = _ENV_PATH
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k == key:
                out.append(new_line)
                found = True
                continue
        out.append(line)
    if not found:
        out.append(new_line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def set_founder_password_runtime(new_password: str) -> None:
    """يحدّث كلمة مرور المؤسس في الذاكرة وفي os.environ بعد كتابة .env."""
    global FOUNDER_PASSWORD
    pw = str(new_password)
    os.environ["FOUNDER_PASSWORD"] = pw
    FOUNDER_PASSWORD = pw


def set_admin_password_runtime(new_password: str) -> None:
    """يحدّث كلمة مرور الإدارة في الذاكرة بعد كتابة .env."""
    global ADMIN_PASSWORD
    pw = str(new_password)
    os.environ["ADMIN_PASSWORD"] = pw
    ADMIN_PASSWORD = pw


def password_matches_stored(plain_password: str, stored: str | None) -> bool:
    """
    يطابق كلمة المرور المدخلة مع القيمة المحفوظة في .env.
    يدعم النص الصريح (القديم) وقيمة مُشفّرة بـ werkzeug (pbkdf2 / scrypt).
    """
    s = (stored or "").strip()
    if not s:
        return False
    plain = plain_password or ""
    if s.startswith("pbkdf2:") or s.startswith("scrypt:"):
        return check_password_hash(s, plain)
    return plain == s


def set_admin_username_runtime(new_username: str) -> None:
    global ADMIN_USERNAME
    u = (new_username or "").strip()
    if not u:
        raise ValueError("اسم مستخدم الإدارة فارغ")
    os.environ["ADMIN_USERNAME"] = u
    ADMIN_USERNAME = u


def set_founder_username_runtime(new_username: str) -> None:
    global FOUNDER_USERNAME
    u = (new_username or "").strip()
    if not u:
        raise ValueError("اسم مستخدم المؤسس فارغ")
    os.environ["FOUNDER_USERNAME"] = u
    FOUNDER_USERNAME = u


def persist_admin_password_hashed(new_plain: str) -> None:
    """تخزين كلمة مرور الإدارة في .env بصيغة hash وتحديث الذاكرة."""
    pw = (new_plain or "").strip()
    if not pw:
        raise ValueError("كلمة مرور الإدارة لا يمكن أن تكون فارغة")
    if len(pw) < 4:
        raise ValueError("كلمة مرور الإدارة قصيرة جداً (4 أحرف على الأقل)")
    hashed = generate_password_hash(pw)
    update_env_file("ADMIN_PASSWORD", hashed)
    set_admin_password_runtime(hashed)


def persist_founder_password_hashed(new_plain: str) -> None:
    pw = (new_plain or "").strip()
    if not pw:
        raise ValueError("كلمة مرور المؤسس لا يمكن أن تكون فارغة")
    if len(pw) < 4:
        raise ValueError("كلمة مرور المؤسس قصيرة جداً (4 أحرف على الأقل)")
    hashed = generate_password_hash(pw)
    update_env_file("FOUNDER_PASSWORD", hashed)
    set_founder_password_runtime(hashed)
